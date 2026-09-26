"""The Nest: wires the ducks, Reachy, the spine, memory and the duck bus together.

    python -m nest --config config.toml     # the real house
    python -m nest --sim                    # a simulated living room, no hardware

Loops, all on one asyncio event loop:

* one **transport** per duck, reconnecting forever;
* the **spine** at ~1 Hz: every mind ticks, the bus delivers what is in earshot;
* the **overseer**: Reachy watches whichever duck is doing something worth watching, and droops
  its antennas at a duck that is very tired;
* the **room eye**: Reachy's camera → the floor map (what it can see now) and, with the
  detector, duck positions (when enabled);
* **Reachy's diary**: now and then, what the local vision model sees in the lounge;
* the **Pond**: a small web page on `:8090` with every duck's state and a few buttons.

Before the ducks arrive, all of this runs with Reachy alone: each duck is an egg in its bed until
the first time it connects (hatch day), and then it inherits everything Reachy has learned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .batteries import BatteryDiary
from .bus import DuckBus, Earshot
from .config import LANDMARK_WORDS, Config, DuckConfig
from .duck.client import DuckClient
from .duck.transport import LoopbackTransport, Transport, UnixSocketTransport, WebRtcTransport
from .reachy import ReachyGaze
from .routines import QuietHours, Routine, Routines, daily_at, every
from .spine.mind import BEHAVIOURS, Mind, go_to_bed
from .spine.personality import Personality
from .spine.safety import SafetyGate, SafetyLimits
from .spine.tiredness import Band, Thresholds
from .world.duckdex import Duckdex
from .world.floormap import REACHY, FloorMap, cone_polygon, view_polygon
from .world.messages import DuckMessage, Vocabulary
from .world.room import DuckTracker, FloorHomography, RoomMap
from .world.scene import Memory, Sighting

logger = logging.getLogger(__name__)

INTERESTING = {"wander", "play", "seek_friend", "greet_friend", "go_to_bed", "call_out"}


class Nest:
    def __init__(self, config: Config, sim: bool = False):
        self.config = config
        self.sim = sim
        self.data = Path(config.data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.world = None
        if sim:
            from .sim import SimWorld

            self.world = SimWorld()
            for i, duck in enumerate(config.ducks):
                self.world.add_duck(duck.name, 1.5 + 2.0 * i, 1.5 + 0.8 * i, yaw=0.6 * i,
                                    battery=80.0 - 45.0 * i)
            if tuple(config.reachy.position) == (0.0, 0.0):
                config.reachy.position = (2.5, 0.05)  # on the simulated TV stand, facing in

        self.vocab = Vocabulary(config.vocabulary)
        self.room = RoomMap(reachy_xy=config.reachy.position, reachy_facing=config.reachy.facing,
                            landmarks=dict(config.landmarks))
        self.tracker = DuckTracker([d.name for d in config.ducks])
        self.homography = FloorHomography(config.reachy.homography)
        self.batteries = BatteryDiary(self.data / "batteries.json")
        self.ducks: dict[str, DuckClient] = {}
        self.minds: dict[str, Mind] = {}
        self.feed: list[dict[str, Any]] = []  # the Pond's event feed
        self.reachy: Any = None
        self.gaze: ReachyGaze | None = None
        self.reachy_status = "off"
        self.reachy_online = False
        self.watchdog_s = 3.0 if sim else 20.0
        self.quiet = QuietHours.from_config(config.quiet_hours)
        self.clock = datetime.now  # tests replace this to live through a night in seconds
        self.routines = Routines(self.available, clock=lambda: self.clock())
        self._greeted: dict[str, str] = {}  # duck -> date of its first-up greeting
        self.reachy_focus: str | None = None
        self._reachy_moved = 0.0
        self._gaze_busy_until = 0.0  # a peek or a greeting in progress: the overseer waits
        self._tasks: list[asyncio.Task] = []
        # The floor map, Reachy's own memory of the lounge, and who has hatched.
        plan = self._load_floorplan()
        self.floormap = FloorMap(self._map_bounds(plan), cell=plan["cell"] if plan else
                                 config.map_cell, who=[d.name for d in config.ducks],
                                 path=self.data / "floormap.npz")
        if plan and (self.floormap.h, self.floormap.w) == plan["floor"].shape:
            self.floormap.seed(plan["floor"], plan["blocked"], plan["filmed_at"])
        self.walkthrough = bool(plan)
        self.reachy_memory = Memory(REACHY, self.data / "reachy-memory.json")
        for name, xy in config.landmarks.items():
            if not self.reachy_memory.knows(name):
                self.reachy_memory.add_landmark(name, xy)
        self._missing: dict[str, int] = {}  # Reachy's diary: looks in a row without an object
        self.hatched: dict[str, str] = self._load_json("hatched.json")
        self.reachy_frame: Any = None  # the newest camera frame, for the diary
        self.reachy_frame_at = 0.0
        self._view: tuple[Any, list[tuple[float, float]], Any] | None = None

        limits = SafetyLimits(**config.safety)
        thresholds = Thresholds(**config.tiredness)
        for index, duck in enumerate(config.ducks):
            client = DuckClient(duck.name, self._transport(duck),
                                http_base=None if sim else f"http://{duck.host}:{duck.http_port}")
            memory = Memory(duck.name, self.data / f"{duck.name}-memory.json")
            for name, xy in config.landmarks.items():
                if not memory.knows(name):
                    memory.add_landmark(name, xy)
            mind = Mind(duck.name, client, Personality.from_seed(duck.name, duck.personality),
                        memory, Duckdex(duck.name, self.data / f"{duck.name}-duckdex.json"),
                        thresholds=thresholds, gate=SafetyGate(limits), walk_speed=duck.walk_speed)
            self.ducks[duck.name] = client
            self.minds[duck.name] = mind
        self._wire_minds()

        self.bus = DuckBus(self.vocab, Earshot(max_m=config.earshot_m), perform=self._perform,
                           on_delivered=self._delivered)
        self.bus.distance = lambda a, b: self.room.distance(a, b)
        self.bus.sees_a_duck = lambda name: self.ducks[name].sees_a_duck()
        # Only ducks that are switched on can hear anything; the message waits for them.
        self.bus.present = lambda name: self.ducks[name].connected

    # ── construction helpers ───────────────────────────────────────────────────

    def _transport(self, duck: DuckConfig) -> Transport:
        if self.sim:
            return LoopbackTransport(duck.name, self.world.handler(duck.name),
                                     powered=self.world.powered(duck.name))
        if duck.transport == "unix":
            return UnixSocketTransport(duck.name, duck.socket)
        return WebRtcTransport(duck.name, duck.host, duck.signalling_port, duck.keep_video)

    def _wire_minds(self) -> None:
        names = list(self.minds)
        for name, mind in self.minds.items():
            friend = next((n for n in names if n != name), None)
            bed = next((d.bed for d in self.config.ducks if d.name == name), None)
            ctx = mind.ctx
            ctx.friend_name = friend
            ctx.on_event = self._event
            if friend:
                ctx.friend_connected = lambda f=friend: self.ducks[f].connected
                ctx.friend_near = lambda a=name, b=friend: self.bus_in_earshot(a, b)
                ctx.friend_room_xy = lambda f=friend: self.room.duck(f)
            ctx.my_room_xy = lambda n=name: self.room.duck(n)
            ctx.bed_xy = lambda b=bed: self.room.landmarks.get(b) if b else None
            ctx.nest_xy = self._nest_spot
            ctx.tell_friend = lambda *a, n=name, **k: self.tell_friend(n, *a, **k)

    async def _first_up(self, name: str) -> None:
        """A duck switched on after quiet hours gets one "good morning" a day, whenever that is.
        Switched on during quiet hours, it is left asleep and silent."""
        mind = self.minds[name]
        today = self.clock().date().isoformat()
        if self.is_quiet():
            mind.asleep = True
            return
        if self._greeted.get(name) == today or mind.asleep:
            return
        self._greeted[name] = today
        try:
            await mind.client.look(1.0, 0.0, 0.4)
            await mind.say("greet")
            self._event(name, "up and about: good morning")
        except Exception as exc:
            logger.info("%s: morning greeting skipped: %s", name, exc)

    def available(self, device: str) -> bool:
        if device == "reachy":
            return self.reachy_online
        client = self.ducks.get(device)
        return bool(client and client.connected)

    def is_quiet(self) -> bool:
        return self.quiet.is_quiet(self.clock())

    def _nest_spot(self) -> tuple[float, float] | None:
        """Where "come here" leads: the `nest` landmark if you set one, else 0.8 m out in front
        of Reachy on the TV stand."""
        if "nest" in self.room.landmarks:
            return self.room.landmarks["nest"]
        if not self.config.reachy.enabled and not self.sim:
            return None
        (x, y), facing = self.room.reachy_xy, self.room.reachy_facing
        return x + 0.8 * math.cos(facing), y + 0.8 * math.sin(facing)

    def bus_in_earshot(self, a: str, b: str) -> bool:
        return self.bus.in_earshot(a, b) is not None if hasattr(self, "bus") else False

    def _event(self, duck: str, text: str) -> None:
        self.feed.append({"at": time.time(), "who": duck, "text": text})
        self.feed = self.feed[-100:]

    def _load_json(self, name: str) -> dict[str, Any]:
        try:
            return json.loads((self.data / name).read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _save_json(self, name: str, data: dict[str, Any]) -> None:
        tmp = self.data / f".{name}.tmp"
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.data / name)

    # ── the floor map ──────────────────────────────────────────────────────────

    def _load_floorplan(self) -> dict[str, Any] | None:
        """The walkthrough's floor plan, from the Olares pipeline (`floorplan.npz`), if any."""
        path = self.data / "floorplan.npz"
        if not path.exists():
            return None
        try:
            import numpy as np

            data = np.load(path, allow_pickle=False)
            return {"x0": float(data["x0"]), "y0": float(data["y0"]), "cell": float(data["cell"]),
                    "floor": data["floor"].astype(bool), "blocked": data["blocked"].astype(bool),
                    "filmed_at": float(data["filmed_at"])}
        except Exception as exc:
            logger.warning("floorplan.npz unreadable, ignoring it: %s", exc)
            return None

    def _map_bounds(self, plan: dict[str, Any] | None) -> tuple[float, float, float, float]:
        if plan:
            h, w = plan["floor"].shape
            return (plan["x0"], plan["y0"], plan["x0"] + w * plan["cell"],
                    plan["y0"] + h * plan["cell"])
        if self.config.map_bounds:
            return tuple(self.config.map_bounds)  # type: ignore[return-value]
        if self.sim:
            from .sim import ROOM_H, ROOM_W

            return (0.0, 0.0, ROOM_W, ROOM_H)
        points = list(self.config.landmarks.values()) + [tuple(self.config.reachy.position)]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        x0, y0, x1, y1 = min(xs) - 1.5, min(ys) - 1.5, max(xs) + 1.5, max(ys) + 1.5
        # at least 6 × 5 m, so the lounge fits before anything has been measured
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hw, hh = max((x1 - x0) / 2, 3.0), max((y1 - y0) / 2, 2.5)
        return (cx - hw, cy - hh, cx + hw, cy + hh)

    def map_calibrated(self) -> bool:
        return self.sim or self.homography.ready

    def _reachy_sees(self, now: float | None = None) -> None:
        """Reachy's view onto the floor map, while it's awake, still and in the watch pose."""
        if (self.gaze is None or not self.reachy_online or self.is_quiet()
                or not self.reachy_status.startswith("awake") or not self.gaze.at_watch_pose()):
            return
        if self.sim:
            key = "sim"
            if self._view is None or self._view[0] != key:
                poly = cone_polygon(self.room.reachy_xy, self.room.reachy_facing, 0.75, 4.2)
                self._view = (key, poly, self.floormap.polygon_mask(poly))
        else:
            if self.reachy_frame is None or not self.homography.ready:
                return
            key = self.reachy_frame.shape[:2]
            if self._view is None or self._view[0] != key:
                poly = view_polygon(self.homography.h, key[1], key[0], self.room.reachy_xy,
                                    self.config.reachy.view_range)
                self._view = (key, poly, self.floormap.polygon_mask(poly))
        self.floormap.mark(self._view[2], REACHY, now)

    def _ducks_see(self) -> None:
        """Where Reachy has placed a duck, the floor around it has just been seen by it."""
        for name, client in self.ducks.items():
            xy = self.room.duck(name)
            if client.connected and xy is not None and not self.minds[name].asleep:
                self.floormap.see_disc(xy[0], xy[1], 0.4, name)

    def map_state(self) -> dict[str, Any]:
        state = self.floormap.encode()
        state.update({
            "calibrated": self.map_calibrated(), "walkthrough": self.walkthrough,
            "reachy": {"xy": list(self.room.reachy_xy), "facing": self.room.reachy_facing,
                       "view": [list(p) for p in self._view[1]] if self._view else None,
                       "watching": bool(self.gaze and self.gaze.at_watch_pose()
                                        and self.reachy_status.startswith("awake")
                                        and not self.is_quiet())},
            "ducks": {name: {"xy": list(self.room.duck(name) or []) or None,
                             "hatched": name in self.hatched,
                             "bed": list(self._bed_xy(name)) if self._bed_xy(name) else None}
                      for name in self.minds},
            "landmarks": {k: list(v) for k, v in self.room.landmarks.items()},
            "things": self.reachy_diary(),
        })
        return state

    def map_at(self, x: float, y: float) -> dict[str, Any]:
        return self.floormap.at(x, y)

    # ── hatch day ──────────────────────────────────────────────────────────────

    async def _maybe_hatch(self, name: str) -> None:
        """The first time a duck ever connects: it hatches. It inherits what Reachy knows about
        the lounge, and Reachy greets it (unless it's quiet hours, when that waits)."""
        if name in self.hatched:
            return
        self.hatched[name] = self.clock().isoformat(timespec="minutes")
        self._save_json("hatched.json", self.hatched)
        mind = self.minds[name]
        inherited = 0
        for obj in self.reachy_memory.things_seen():
            sighting = self.reachy_memory.where_is(obj)
            if sighting and mind.memory.where_is(obj) is None:
                mind.memory.heard(sighting, REACHY)
                inherited += 1
        self._event(name, f"hatched! Welcome home, {name.title()}"
                          + (f". Reachy told it where {inherited} things are." if inherited
                             else "."))
        if self.is_quiet() or not self.gaze or not self.reachy_status.startswith("awake"):
            return
        self._gaze_busy_until = time.monotonic() + 12.0
        xy = self.room.duck(name) or self._bed_xy(name)
        if xy:
            await self._reachy_try("hatch", self.gaze.look_at_bearing(
                self.room.bearing_from_reachy(*xy), antennas="perk"))
        await self._reachy_try("hatch", self.gaze.wiggle(3))
        await self._reachy_try("hatch", self.gaze.watch())

    def _bed_xy(self, name: str) -> tuple[float, float] | None:
        bed = next((d.bed for d in self.config.ducks if d.name == name), None)
        return self.room.landmarks.get(bed) if bed else None

    async def _routine_peek_at_eggs(self) -> None:
        """Now and then before hatch day, Reachy leans over to look at an egg."""
        eggs = [n for n in self.minds if n not in self.hatched and self._bed_xy(n)]
        if (not eggs or self.is_quiet() or self.gaze is None
                or not self.reachy_status.startswith("awake")):
            return
        egg = eggs[int(time.time() // 60) % len(eggs)]
        self._gaze_busy_until = time.monotonic() + 8.0
        await self.gaze.look_at_bearing(self.room.bearing_from_reachy(*self._bed_xy(egg)),
                                        pitch=-0.3, antennas="curious", duration=1.5)
        await asyncio.sleep(3.0)
        await self.gaze.watch()
        self._event("reachy", f"peeked at {egg.title()}'s egg")

    # ── running ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        for name, client in self.ducks.items():
            self._tasks.append(asyncio.ensure_future(client.transport.run()))
            self._tasks.append(asyncio.ensure_future(self._on_connect_loop(name)))
        self._setup_reachy()
        self._setup_routines()
        loops = [("spine", self._spine_loop), ("overseer", self._overseer_loop),
                 ("reachy watchdog", self._reachy_watchdog),
                 ("routines", self.routines.run_forever)]
        if self.config.captioner.enabled and not self.sim:
            loops.append(("eyes", self._eyes_loop))
            if self.config.reachy.enabled and self.config.reachy.camera:
                loops.append(("reachy's diary", self._diary_loop))
        if self.sim:
            loops.append(("sim", self._sim_loop))
        elif self.config.reachy.camera:
            loops.append(("room eye", self._room_eye_loop))
        for name, loop in loops:
            self._tasks.append(asyncio.ensure_future(self._keep_running(name, loop)))

    async def _keep_running(self, name: str, loop) -> None:
        """Self-healing: a loop that crashes is logged and restarted, never lost until reboot."""
        delay = 2.0
        while True:
            started = time.monotonic()
            try:
                await loop()
                return  # a loop that ends on purpose (room eye switched off) stays ended
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("%s loop crashed; restarting", name)
                self._event("nest", f"{name} loop crashed ({exc}); restarting")
            delay = 2.0 if time.monotonic() - started > 300 else min(delay * 2, 120.0)
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        self.floormap.save(force=True)
        for mind in self.minds.values():
            mind.interrupt()
        for client in self.ducks.values():
            await client.transport.stop()
        for task in self._tasks:
            task.cancel()
        if self.reachy is not None:
            await self.reachy.close()

    async def _on_connect_loop(self, name: str) -> None:
        client = self.ducks[name]
        while True:
            await client.transport.connected.wait()
            delay = 2.0
            while client.transport.connected.is_set():
                try:
                    await client.on_connected()
                    self._event(name, f"connected (API v{client.api_version})")
                    await self._maybe_hatch(name)
                    break
                except Exception as exc:
                    # Without robot.subscribe nothing streams and every walk is refused, so keep
                    # trying rather than idling on a half-open channel.
                    self._event(name, f"connected, but setup failed ({exc}); retrying")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)
            await self._first_up(name)
            while client.transport.connected.is_set():
                await asyncio.sleep(0.5)
            self.minds[name].on_disconnected()
            self._event(name, "switched off or out of reach")

    def _setup_reachy(self) -> None:
        if self.sim:
            self.reachy = self.world.reachy
        elif self.config.reachy.enabled:
            from .reachy import ReachyClient

            self.reachy = ReachyClient(self.config.reachy.url)
        if self.reachy is not None:
            self.gaze = ReachyGaze(self.reachy)

    async def _reachy_watchdog(self) -> None:
        """Notices Reachy being switched off and on, and wakes it when it may be awake.

        Off is a normal state, not an error: the status just says so, and when it answers
        again it is woken, unless it is quiet hours or bedtime, when it is left resting."""
        if self.reachy is None:
            return
        while True:
            online = await self.reachy.ping()
            if not online:
                if self.reachy_online:
                    self._event("reachy", "switched off")
                self.reachy_online = False
                self.reachy_status = "off"
                if self.gaze:
                    self.gaze.forget_pose()
            else:
                came_back = not self.reachy_online
                self.reachy_online = True
                resting = self.is_quiet() or any(m.asleep for m in self.minds.values())
                if resting:
                    self.reachy_status = "asleep" if not self.is_quiet() else "resting (quiet hours)"
                elif came_back or not self.reachy_status.startswith("awake"):
                    if self.gaze:
                        self.gaze.forget_pose()
                    if await self._reachy_try("wake-up", self.reachy.wake_up()):
                        self.reachy_status = "awake"
                        await self._reachy_try("watch", self.gaze.watch())
                        self._event("reachy", "switched on: awake")
            await asyncio.sleep(self.watchdog_s)

    def _setup_routines(self) -> None:
        q = self.quiet
        lead = timedelta(minutes=self.config.bedtime_lead_min)
        goodnight = (datetime.combine(self.clock().date(), q.start) - lead).time()
        self.routines.add(Routine("goodnight", self._routine_goodnight, daily_at(goodnight),
                                  catch_up=lead + timedelta(minutes=5)))
        self.routines.add(Routine("good morning", self._routine_morning, daily_at(q.end),
                                  catch_up=timedelta(hours=12)))
        self.routines.add(Routine("battery diary", self._routine_battery_sample,
                                  every(timedelta(minutes=4)), catch_up=timedelta(minutes=4)))
        if any(n not in self.hatched for n in self.minds):
            self.routines.add(Routine("peek at the eggs", self._routine_peek_at_eggs,
                                      every(timedelta(minutes=25)), needs=("reachy",),
                                      catch_up=timedelta(minutes=10)))
        # Coming up inside quiet hours (a reboot at 11 pm): the ducks are already asleep.
        if self.is_quiet():
            for mind in self.minds.values():
                mind.asleep = True

    async def _routine_goodnight(self) -> None:
        """A little before quiet hours: the goodnight ritual with whoever is switched on.
        Anyone already off is simply skipped; nothing waits for them."""
        if not self.quiet.enabled:
            return
        self.quiet.override_until = None  # a "Good morning" lift ends at bedtime
        await self.bedtime()

    async def _routine_morning(self) -> None:
        self.quiet.override_until = None
        for mind in self.minds.values():
            mind.asleep = False
        self._event("nest", "morning: quiet hours are over")
        for name, client in self.ducks.items():
            if client.connected:  # switched on early: say good morning now
                await self._first_up(name)

    async def _routine_battery_sample(self) -> None:
        for name, client in self.ducks.items():
            if client.connected:
                self.batteries.sample(name, client.health.battery_percent)

    async def _spine_loop(self) -> None:
        period = 1.0 / max(self.config.tick_hz, 0.1)
        while True:
            quiet = self.is_quiet()
            for mind in self.minds.values():
                mind.quiet = quiet
                try:
                    await mind.tick()
                except Exception:
                    logger.exception("%s: tick failed", mind.name)
            try:
                await self.bus.pump()
            except Exception:
                logger.exception("bus pump failed")
            self._ducks_see()
            self.floormap.save()
            await asyncio.sleep(period)

    async def _overseer_loop(self) -> None:
        """Reachy watches the ducks: the tired one first, then whoever is up to something."""
        while True:
            await asyncio.sleep(2.0)
            if (self.gaze is None or not self.reachy_online or self.is_quiet()
                    or not self.reachy_status.startswith("awake")
                    or time.monotonic() < self._gaze_busy_until):
                continue
            focus, expression = self._choose_focus()
            now = time.monotonic()
            if focus == self.reachy_focus and now - self._reachy_moved < 6.0:
                continue
            xy = self.room.duck(focus) if focus else None
            try:
                if xy is not None:
                    await self.gaze.look_at_bearing(self.room.bearing_from_reachy(*xy),
                                                    antennas=expression, duration=1.2)
                elif focus is None and (self.reachy_focus is not None
                                        or not self.gaze.at_watch_pose(settle_s=0.0)):
                    await self.gaze.watch()  # back to watching the floor
                self.reachy_focus = focus
                self._reachy_moved = now
            except Exception as exc:
                self.reachy_status = f"awake (last move failed: {exc})"

    def _choose_focus(self) -> tuple[str | None, str | None]:
        for name, mind in self.minds.items():
            if mind.tiredness.band == Band.VERY_LOW:
                return name, "droop"
        for name, mind in self.minds.items():
            if mind.tiredness.band == Band.CHARGING and mind.behaviour == "nap":
                continue
            if mind.behaviour in INTERESTING:
                return name, "curious" if mind.behaviour != "go_to_bed" else "tired_look"
        return None, None

    async def _sim_loop(self) -> None:
        ticks = 0
        while True:
            self.world.step(0.1)
            self.world.publish()
            for name, (x, y) in self.world.reachy.positions.items():
                if self.world.ducks[name].switched_on:
                    self.room.set_duck(name, x, y)
            ticks += 1
            if ticks % 10 == 0:
                self._reachy_sees()
            await asyncio.sleep(0.1)

    async def _room_eye_loop(self) -> None:
        """Reachy's camera → the floor map (what it can see right now) and, with the duck
        detector, where the ducks are. Also keeps the newest frame for Reachy's diary.

        Needs the floor calibration; the detector model is optional (no ducks before
        Christmas, and the map works without it)."""
        from .reachy import ReachyCamera

        if not self.homography.ready:
            self._event("reachy", "room eye off: it needs the floor calibration first "
                                  "(see SETUP.md)")
            return
        loop = asyncio.get_running_loop()
        detector = None
        model = self.config.reachy.detector_model
        if model and Path(model).exists():
            from .vision.duck_detector import DuckDetector

            detector = await loop.run_in_executor(None, DuckDetector, model,
                                                  self.config.reachy.detector_threshold)
        camera = None
        while True:
            if not self.reachy_online or self.is_quiet():
                if camera is not None:
                    camera.close()
                    camera = None
                await asyncio.sleep(10.0)
                continue
            try:
                if camera is None:
                    camera = await loop.run_in_executor(None, ReachyCamera)
                frame = await loop.run_in_executor(None, camera.frame)
                if frame is not None:
                    self.reachy_frame, self.reachy_frame_at = frame, time.time()
                    # The homography is only true in the pose it was calibrated in.
                    if self.gaze is not None and self.gaze.at_watch_pose():
                        self._reachy_sees()
                        if detector is not None:
                            boxes = await loop.run_in_executor(None, detector.detect, frame)
                            self.tracker.update([self.homography.to_floor(*b.foot)
                                                 for b in boxes])
                            for track in self.tracker.tracks:
                                if track.name:
                                    self.room.set_duck(track.name, track.x, track.y,
                                                       at=track.at)
            except Exception as exc:
                # Reachy unplugged mid-frame: drop the camera and try again once it's back.
                logger.info("room eye: %s", exc)
                if camera is not None:
                    try:
                        camera.close()
                    except Exception:
                        pass
                camera = None
                await asyncio.sleep(10.0)
                continue
            await asyncio.sleep(0.33)

    # ── Reachy's diary ─────────────────────────────────────────────────────────

    async def _diary_loop(self) -> None:
        """By day, every few minutes, the local vision model looks at the lounge through
        Reachy's camera. What moved, what's new and what's gone goes in the feed."""
        from .vision.captioner import Captioner

        cfg = self.config.captioner
        captioner = Captioner(cfg.url, cfg.model)
        try:
            while True:
                await asyncio.sleep(cfg.reachy_every_s)
                try:
                    await self.diary_step(captioner)
                except Exception as exc:
                    logger.info("reachy's diary: %s", exc)
        finally:
            await captioner.close()

    async def diary_step(self, captioner: Any) -> list[str]:
        """One look. Returns what went in the feed."""
        if (self.is_quiet() or not self.reachy_online or self.reachy_frame is None
                or time.time() - self.reachy_frame_at > 30 or self.gaze is None
                or not self.gaze.at_watch_pose()):
            return []
        png = _png(self.reachy_frame)
        seen = await captioner.describe(png, sorted(self.reachy_memory.landmarks),
                                        viewer="reachy")
        min_conf = self.config.captioner.min_confidence
        seen = [item for item in seen if item["confidence"] >= min_conf]
        notes = []
        for item in seen:
            before = self.reachy_memory.where_is(item["object"])
            now = self.reachy_memory.saw(item["object"], item["relation"], item["landmark"],
                                         item["near"], confidence=item["confidence"])
            self._missing.pop(item["object"], None)
            if before is None:
                notes.append(f"spotted a {now.obj} {now.describe()}")
            elif (before.relation, before.landmark) != (now.relation, now.landmark):
                notes.append(f"the {now.obj} has moved: it was {before.describe()}, "
                             f"now it's {now.describe()}")
        # Small models miss things, so something counts as gone only after three looks.
        found = {item["object"] for item in seen}
        for obj in self.reachy_memory.things_seen():
            if obj in found:
                continue
            self._missing[obj] = self._missing.get(obj, 0) + 1
            if self._missing[obj] == 3:
                before = self.reachy_memory.where_is(obj)
                self.reachy_memory.gone(obj)
                notes.append(f"the {obj} isn't {before.describe() if before else 'there'} "
                             "any more")
        for note in notes:
            self._event(REACHY, note)
        return notes

    def reachy_diary(self) -> list[dict[str, Any]]:
        """What Reachy believes is in the lounge now."""
        out = []
        for obj in self.reachy_memory.things_seen():
            s = self.reachy_memory.where_is(obj)
            if s:
                xy = self.room.landmarks.get(s.landmark)
                out.append({"obj": obj, "where": s.describe(), "at": s.at,
                            "xy": list(xy) if xy else None})
        return out

    async def _eyes_loop(self) -> None:
        """While a duck is looking around, ask the local vision model what it sees."""
        from .vision.captioner import Captioner

        cfg = self.config.captioner
        captioner = Captioner(cfg.url, cfg.model)
        last: dict[str, float] = {}
        try:
            while True:
                await asyncio.sleep(2.0)
                if self.is_quiet():
                    continue
                for name, mind in self.minds.items():
                    if mind.behaviour not in ("look_around", "investigate", "wander"):
                        continue
                    if not mind.client.connected or time.monotonic() - last.get(name, 0) < \
                            cfg.every_s:
                        continue
                    last[name] = time.monotonic()
                    try:
                        png = await mind.client.frame_png()
                        seen = await captioner.describe(png, sorted(mind.memory.landmarks))
                    except Exception as exc:
                        logger.info("%s: captioning failed: %s", name, exc)
                        continue
                    for item in seen:
                        if item["confidence"] >= cfg.min_confidence:
                            self.record_sighting(name, item["object"], item["relation"],
                                                 item["landmark"], item["near"],
                                                 confidence=item["confidence"])
        finally:
            await captioner.close()

    async def roll_call(self, mover: str) -> bool:
        """Tell the two ducks apart in Reachy's view: `mover` takes one small step."""
        snapshot = self.tracker.snapshot()
        mind = self.minds[mover]
        ok, why = mind.gate.may_move(mind.client)
        if not ok:
            self._event(mover, f"roll call skipped: {why}")
            return False
        await mind.client.walk_for(0.1, 0.0, 0.0, 1.5)
        await asyncio.sleep(1.0)
        resolved = self.tracker.roll_call(mover, snapshot)
        self._event("reachy", f"roll call {'worked' if resolved else 'was inconclusive'}")
        return resolved

    # ── the duck bus ───────────────────────────────────────────────────────────

    def sender_index(self, name: str) -> int:
        return list(self.minds).index(name)

    def tell_friend(self, speaker: str, kind: str, obj: str, relation: str = "near",
                    landmark: str | None = None, near: str | None = None,
                    confidence: float = 1.0) -> str | None:
        listener = self.minds[speaker].ctx.friend_name
        if listener is None:
            return None
        message = DuckMessage(kind, self.sender_index(speaker), self.bus.next_seq(speaker), obj,
                              relation, landmark, near, confidence)
        self.bus.say(speaker, listener, message)
        return message.subtitle()

    async def _perform(self, speaker: str, listener: str, message: DuckMessage) -> None:
        """Turn to face the listener, chirp the phrase, and let the listener answer."""
        if self.is_quiet():
            return  # delivered, but nobody chirps during quiet hours
        talker, hearer = self.minds[speaker], self.minds[listener]
        talker.interrupt()
        sighting = talker.client.sighting
        if sighting and sighting.boxes and talker.client.sees_a_duck():
            bearing = sighting.bearing(sighting.boxes[0])
            await talker.client.look(math.cos(bearing), math.sin(bearing), 0.0)
        for tag in message.chirp_phrase():
            await talker.say(tag)
            await asyncio.sleep(0.6)
        await hearer.say("inquire" if message.kind == "ask" else "greet")

    def _delivered(self, delivery) -> None:
        listener = self.minds[delivery.listener]
        m = delivery.message
        self._event(delivery.speaker, f"told {delivery.listener.title()}: “{m.subtitle()}” "
                                      f"({delivery.size_bytes} bytes; {delivery.evidence})")
        if m.kind == "found" and m.landmark:
            known = listener.memory.where_is(m.obj)
            listener.memory.heard(Sighting(obj=m.obj, relation=m.relation, landmark=m.landmark,
                                           near=m.near, seen_by=delivery.speaker,
                                           confidence=m.confidence), delivery.speaker)
            if known is None:
                listener.needs.stir("curiosity", 0.3)  # it wants to go and see
        elif m.kind == "gone":
            listener.memory.gone(m.obj)
        elif m.kind == "ask":
            where = listener.memory.where_is(m.obj)
            if where:
                self.tell_friend(delivery.listener, "found", m.obj, where.relation,
                                 where.landmark, where.near, where.confidence)
            else:
                self.tell_friend(delivery.listener, "unknown", m.obj)

    # ── things the Pond (or Home Assistant) can ask for ────────────────────────

    def record_sighting(self, duck: str, obj: str, relation: str, landmark: str,
                        near: str | None = None, tell: bool = True,
                        confidence: float = 1.0) -> dict[str, Any]:
        """A duck saw something: into its memory, into its Duckdex, and (maybe) to its friend.

        Called by the Pond today; by the vision captioner once Phase 5's model is running."""
        mind = self.minds[duck]
        rumour = mind.memory.where_is(obj)
        credited = rumour.told_by if rumour and rumour.told_by else None
        sighting = mind.memory.saw(obj, relation, landmark, near, confidence=confidence)
        meal = mind.duckdex.feed(obj, credited_to=credited)
        if credited and meal == "meal":
            self._event(duck, f"found the {obj}, just where {credited.title()} said!")
            mind.needs.on_friend_near(20.0)  # a good feeling about its friend
        mind.needs.on_fed(meal)
        self._event(duck, f"saw a {obj} {sighting.describe()} ({meal})")
        told = None
        if tell and meal == "meal" and not credited:
            told = self.tell_friend(duck, "found", obj, relation, landmark, near, confidence)
        return {"meal": meal, "where": sighting.describe(), "queued_for_friend": told}

    def where_is(self, duck: str, obj: str) -> dict[str, Any]:
        sighting = self.minds[duck].memory.where_is(obj)
        if sighting is None:
            return {"known": False}
        return {"known": True, "where": sighting.describe(), "seen_by": sighting.seen_by,
                "told_by": sighting.told_by, "confidence": sighting.confidence,
                "at": sighting.at}

    async def call(self, duck: str) -> None:
        mind = self.minds[duck]
        mind.interrupt()
        await mind.stand()
        await mind.client.look(1.0, 0.0, 0.5)
        await mind.say("greet")
        if self.gaze and self.room.duck(duck):
            await self._reachy_try("look", self.gaze.look_at_bearing(
                self.room.bearing_from_reachy(*self.room.duck(duck)), antennas="perk"))

    ACTIONS = {
        "come_here": ("come_here", "{duck} is coming over."),
        "look_at_me": (None, "{duck} looked up and said hello."),
        "play": ("play", "{duck} is playing."),
        "find_friend": ("seek_friend", "{duck} is off to find {friend}."),
        "show_something": ("show_something", "Hold the thing in front of {duck}'s beak."),
        "go_to_bed": ("go_to_bed", "{duck} is heading to bed."),
    }

    async def direct(self, duck: str, action: str) -> tuple[bool, str]:
        """Something you asked a duck to do from the Pond. (accepted, what to tell you)

        Your request replaces whatever the duck was doing. Its safety still applies: a walk the
        gate refuses turns into a turn-away or a greeting, and the reason shows in the Pond."""
        mind = self.minds[duck]
        name = duck.title()
        friend = (mind.ctx.friend_name or "its friend").title()
        if action not in self.ACTIONS:
            return False, f"Unknown action {action!r}."
        if not mind.client.connected:
            return False, f"{name} isn't connected right now."
        if mind.paused:
            return False, f"{name} is paused. Tap Resume first."
        if self.is_quiet():
            return False, (f"Quiet hours until {self.quiet.end.strftime('%H:%M')}. "
                           "Tap Good morning to wake them anyway.")
        if mind.asleep and action != "go_to_bed":
            return False, f"{name} is asleep. Say good morning first."
        behaviour, message = self.ACTIONS[action]
        if behaviour is None:
            await self.call(duck)
        else:
            mind.interrupt()
            mind._start(behaviour, BEHAVIOURS[behaviour](mind))
        self._event(duck, f"you asked: {action.replace('_', ' ')}")
        return True, message.format(duck=name, friend=friend)

    async def bedtime(self) -> None:
        """Reachy looks at each duck in turn, wiggles goodnight, then sleeps itself."""
        for name, mind in self.minds.items():
            mind.interrupt()
            mind.asleep = True
            if mind.client.connected and mind.tiredness.band != Band.CHARGING:
                mind._start("go_to_bed", go_to_bed(mind))
        for name in self.minds:
            xy = self.room.duck(name)
            if self.gaze and xy:
                await self._reachy_try("goodnight", self.gaze.look_at_bearing(
                    self.room.bearing_from_reachy(*xy)))
                await self._reachy_try("goodnight", self.gaze.wiggle(1))
        if self.gaze:
            self.gaze.forget_pose()
        if self.reachy and await self._reachy_try("sleep", self.reachy.goto_sleep()):
            self.reachy_status = "asleep"
        self._event("reachy", "bedtime: goodnight, Ah-Ah and Tee-Tee")

    async def _reachy_try(self, what: str, coroutine) -> bool:
        """Reachy is a nice extra, never a dependency: a failed move is logged, not raised."""
        try:
            await coroutine
            return True
        except Exception as exc:
            self.reachy_status = f"{self.reachy_status.split(' ')[0]} (last {what} failed: {exc})"
            return False

    async def wake(self) -> None:
        if self.is_quiet():
            self.quiet.lift_until_bedtime(self.clock())  # you decided: awake until the next bedtime
        for mind in self.minds.values():
            mind.asleep = False
            mind.interrupt()
        if self.reachy and await self._reachy_try("wake-up", self.reachy.wake_up()):
            self.reachy_status = "awake"
            await self._reachy_try("watch", self.gaze.watch())
        awake = [m for m in self.minds.values() if m.client.connected]
        if not awake:
            self._event("nest", "good morning (the ducks are still switched off)")
            return
        best = max(awake, key=lambda m: m.tiredness.percent or 0)
        try:
            await best.stand()
            await best.say("greet")
            self._greeted[best.name] = self.clock().date().isoformat()
            self._event(best.name, "first up: good morning")
        except Exception as exc:
            self._event(best.name, f"good morning didn't reach it ({exc})")

    def status(self) -> dict[str, Any]:
        ducks = {}
        for name, mind in self.minds.items():
            client = mind.client
            ducks[name] = {
                "connected": client.connected,
                "transport": client.transport.status,
                "battery_percent": client.health.battery_percent,
                "hottest": [client.health.hottest_joint, client.health.hottest_c],
                "tiredness": mind.tiredness.band.value,
                "mood": mind.needs.mood,
                "needs": mind.needs.as_dict(),
                "personality": mind.personality.describe(),
                "behaviour": mind.behaviour,
                "paused": mind.paused,
                "fallen": client.fallen,
                "sees_a_duck": client.sees_a_duck(),
                "room_xy": self.room.duck(name),
                "pack": self.batteries.fitted.get(name),
                "duckdex": len(mind.duckdex),
                "knows": mind.memory.things_seen(),
                "safety": mind.gate.last_refusal,
                "asleep": mind.asleep,
                "hatched": name in self.hatched,
                "hatched_at": self.hatched.get(name),
                "just_told": any(d.speaker == name and time.time() - d.at < 8
                                 for d in self.bus.log[-5:]),
                "events": [{"at": t, "text": x} for t, x in list(mind.events)[-8:]],
            }
        return {"ducks": ducks, "reachy": {"status": self.reachy_status,
                                           "online": self.reachy_online,
                                           "watching": self.reachy_focus,
                                           "diary": self.reachy_diary()},
                "map": {"calibrated": self.map_calibrated(), "walkthrough": self.walkthrough,
                        **self.floormap.coverage()},
                "quiet": {"now": self.is_quiet(), "text": self.quiet.describe(self.clock())},
                "routines": self.routines.status(),
                "messages": self.bus.recent(), "pending_messages": len(self.bus.pending),
                "batteries": self.batteries.health(), "feed": self.feed[-30:],
                "landmarks": sorted(self.room.landmarks), "sim": self.sim,
                "things": [w for w in self.vocab.words
                           if w not in self.room.landmarks and w not in LANDMARK_WORDS],
                "uptime_s": round(time.time() - self.started)}


def _png(frame: Any) -> bytes:
    """A BGR camera frame as PNG bytes, for the vision model."""
    import cv2

    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise ValueError("could not encode the frame")
    return buf.tobytes()
