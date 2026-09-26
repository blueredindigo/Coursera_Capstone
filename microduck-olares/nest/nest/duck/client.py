"""A typed Microduck client: the calls a pet needs, with the robot's own units and rules.

Everything is in the duck's trunk frame, radians and metres: `x` forward, `y` left, `z` up,
positive `vyaw` turns left (`duck-ipc-proto`, "Units and frame, stated once").

Two rules from the robot that shape this file:

* **`robot.move` is continuous.** `robotd` zeroes the velocity when intents stop arriving for
  `[safety] deadman_ms` (500 ms by default), so a walk is a stream of twists, not one call.
  `walk_for()` resends at 10 Hz and always ends with `robot.stop`.
* **Battery and servo temperature come from `robot.health`,** not from the `robot.state` stream.
  `poll_health()` asks every few seconds. Absent means *not known yet*, never zero.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .rpc import Rpc, RpcError
from .transport import Transport

logger = logging.getLogger(__name__)

API_VERSION = 37  # duck-ipc-proto API_VERSION this client was written against.

#: `robot.sound` tags (duck-ipc-proto `SoundTag`).
SOUND_TAGS = ("alarm", "greet", "inquire", "peck", "chirp", "coo", "wheee")

#: `robot.do` names every release answers to. `sit_toggle` and `ground_pick` are daemon-owned;
#: the others are the default `[[policy.skill]]` table. `robot.skills` lists the real set.
SKILLS = ("sit_toggle", "ground_pick", "roulade", "kick_left", "kick_right")

MOVE_HZ = 10.0


@dataclass
class Health:
    healthy: bool = False
    reason: str | None = None
    battery_percent: float | None = None
    battery_volts: float | None = None
    hottest_joint: str | None = None
    hottest_c: float | None = None
    at: float = 0.0


@dataclass
class Sighting:
    """What the duck's own duck-detector saw (`media.detections`)."""

    width: int
    height: int
    boxes: list[dict[str, float]] = field(default_factory=list)
    at: float = 0.0

    def bearing(self, box: dict[str, float], hfov_rad: float = 1.08) -> float:
        """Horizontal angle to a box centre, radians, positive left (trunk convention).

        The IMX219 is about 62° across; the picture is the upright one mediad publishes.
        """
        cx = (box["x0"] + box["x1"]) / 2.0
        return -((cx / max(self.width, 1)) - 0.5) * hfov_rad


class DuckClient:
    def __init__(self, name: str, transport: Transport, http_base: str | None = None,
                 timeout: float = 10.0):
        self.name = name
        self.transport = transport
        self.rpc = Rpc(timeout=timeout)
        transport.attach(self.rpc)
        self.http_base = http_base  # e.g. "http://ah-ah.local:8080", for GET /frame
        self.health = Health()
        self.state: dict[str, Any] | None = None
        self.state_at = 0.0
        self.tof: dict[str, Any] | None = None
        self.tof_at = 0.0
        self.sighting: Sighting | None = None
        self.api_version: int | None = None
        self.skills: list[str] = list(SKILLS)
        self._move_task: asyncio.Task | None = None
        self.rpc.on("robot.state", self._on_state)
        self.rpc.on("tof.frame", self._on_tof)
        self.rpc.on("media.detections", self._on_detections)

    # ── lifecycle ──────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self.transport.connected.is_set()

    async def on_connected(self, state_hz: int = 10) -> None:
        """What to do each time the channel (re)opens: identify, subscribe, learn the skills."""
        hello = await self.rpc.call("hello", {"api_version": API_VERSION})
        self.api_version = (hello or {}).get("api_version")
        if self.api_version is not None and self.api_version != API_VERSION:
            logger.info("%s speaks API v%s, the Nest was written for v%s; carrying on "
                        "(a call it does not know will say so)", self.name, self.api_version,
                        API_VERSION)
        subscribed = await self.rpc.call("robot.subscribe", {"hz": state_hz})
        if subscribed and subscribed.get("skills"):
            self.skills = sorted(set(SKILLS) | set(subscribed["skills"]))
        try:
            await self.rpc.call("tof.stream")
        except RpcError as exc:  # not every duck has the sensor fitted
            logger.info("%s: no ToF stream (%s)", self.name, exc)
        await self.poll_health()

    async def poll_health(self) -> Health:
        result = await self.rpc.call("robot.health") or {}
        battery = result.get("battery") or {}
        motors = result.get("motors") or {}
        self.health = Health(
            healthy=bool(result.get("healthy")),
            reason=result.get("reason"),
            battery_percent=battery.get("percent"),
            battery_volts=battery.get("volts"),
            hottest_joint=motors.get("hottest"),
            hottest_c=motors.get("max_c"),
            at=time.monotonic(),
        )
        return self.health

    # ── what the robot tells us ────────────────────────────────────────────────

    def _on_state(self, params: dict[str, Any]) -> None:
        self.state = params
        self.state_at = time.monotonic()

    def _on_tof(self, params: dict[str, Any]) -> None:
        self.tof = params
        self.tof_at = time.monotonic()

    def _on_detections(self, params: dict[str, Any]) -> None:
        self.sighting = Sighting(width=int(params.get("width") or 0),
                                 height=int(params.get("height") or 0),
                                 boxes=list(params.get("boxes") or []),
                                 at=time.monotonic())

    @property
    def fallen(self) -> bool:
        return bool(((self.state or {}).get("safety") or {}).get("fallen"))

    @property
    def odom(self) -> tuple[float, float, float] | None:
        odom = (self.state or {}).get("odom")
        if not odom:
            return None
        x, y, _ = odom.get("position", [0.0, 0.0, 0.0])
        return float(x), float(y), float(odom.get("yaw", 0.0))

    def state_age(self) -> float:
        return time.monotonic() - self.state_at if self.state_at else float("inf")

    def sees_a_duck(self, within_s: float = 3.0, min_score: float = 0.0) -> bool:
        """Did this duck's own detector see another Microduck recently?

        With two ducks in the house, any duck Ah-Ah sees is Tee-Tee: the detector cannot tell
        ducks apart, but here it does not need to. The robot has already applied its own
        threshold (`[duck_detector] threshold`, on the INT8 model's scale, where every real
        detection reads about 1.3), so any box it sends counts.
        """
        s = self.sighting
        if s is None or time.monotonic() - s.at > within_s:
            return False
        return any(float(b.get("score", 0.0)) >= min_score for b in s.boxes)

    # ── discrete intents (answered) ────────────────────────────────────────────

    async def sound(self, tag: str) -> dict[str, Any]:
        if tag not in SOUND_TAGS:
            raise ValueError(f"unknown sound tag {tag!r}; the voice bank has {SOUND_TAGS}")
        return await self.rpc.call("robot.sound", {"tag": tag})

    async def look(self, x: float, y: float, z: float, neck_pitch: float = 0.0) -> dict[str, Any]:
        """Point the camera at a trunk-frame point, metres. The floor is ~0.12 m below trunk."""
        return await self.rpc.call("robot.look", {"x": x, "y": y, "z": z,
                                                  "neck_pitch": neck_pitch})

    async def do(self, skill: str) -> dict[str, Any]:
        return await self.rpc.call("robot.do", {"skill": skill})

    async def stop(self) -> dict[str, Any]:
        self._cancel_move()
        return await self.rpc.call("robot.stop")

    async def enable(self, on: bool = True) -> dict[str, Any]:
        return await self.rpc.call("robot.enable", {"on": on})

    async def shutdown(self) -> dict[str, Any]:
        """Sit down, then power off: the pit-stop and surgery-day call."""
        self._cancel_move()
        return await self.rpc.call("robot.shutdown")

    # ── continuous intents (streamed) ──────────────────────────────────────────

    async def head(self, neck_pitch: float = 0.0, head_pitch: float = 0.0,
                   head_yaw: float = 0.0, head_roll: float = 0.0) -> None:
        await self.rpc.tell("robot.head", {"neck_pitch": neck_pitch, "head_pitch": head_pitch,
                                           "head_yaw": head_yaw, "head_roll": head_roll})

    async def mouth(self, open_: float) -> None:
        await self.rpc.tell("robot.mouth", {"open": max(0.0, min(1.0, open_))})

    async def walk_for(self, vx: float, vy: float, vyaw: float, seconds: float,
                       keep_going: Any = None) -> None:
        """Stream a twist for `seconds`, then stop.

        `keep_going()` is asked before every resend; returning False ends the walk early. The
        safety gate uses it to stop at an obstacle the moment one appears.
        """
        self._cancel_move()
        task = asyncio.ensure_future(self._stream(vx, vy, vyaw, seconds, keep_going))
        self._move_task = task
        try:
            # `wait` rather than `await task`: a walk replaced by another (`_cancel_move`) ends
            # quietly here, while a caller that is itself cancelled still gets its
            # CancelledError, so interrupting a behaviour really does stop it.
            await asyncio.wait({task})
        except asyncio.CancelledError:
            task.cancel()
            raise

    async def _stream(self, vx, vy, vyaw, seconds, keep_going) -> None:
        deadline = time.monotonic() + seconds
        try:
            while time.monotonic() < deadline:
                if keep_going is not None and not keep_going():
                    break
                await self.rpc.tell("robot.move", {"vx": vx, "vy": vy, "vyaw": vyaw})
                await asyncio.sleep(1.0 / MOVE_HZ)
        finally:
            try:
                await self.rpc.tell("robot.move", {"vx": 0.0, "vy": 0.0, "vyaw": 0.0})
            except Exception:
                pass

    def _cancel_move(self) -> None:
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()

    # ── the camera, over plain HTTP ────────────────────────────────────────────

    async def frame_png(self) -> bytes:
        """One upright PNG from `GET http://<duck>:8080/frame`. Snapshots, not a stream."""
        if not self.http_base:
            raise RuntimeError(f"{self.name}: no http_base configured for /frame")
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.http_base}/frame")
            response.raise_for_status()
            return response.content
