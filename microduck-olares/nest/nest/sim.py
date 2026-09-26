"""A simulated living room: two ducks and a Reachy, for `--sim` and the tests.

Not a physics simulator. The microduck repo has one of those (`scripts/duck-sim`, the real
daemons against a MuJoCo body) and the dojo uses it. This is the Nest's test double: it answers
the same JSON-RPC calls a duck answers, with the same shapes, so the Nest's logic can run end to
end with no hardware. Batteries drain, twists move the duck across a floor plan, the ToF sees
walls, and each duck's "detector" sees the other when it is in front of it.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

ROOM_W, ROOM_H = 5.0, 4.0  # metres, the living room


@dataclass
class SimDuck:
    name: str
    x: float
    y: float
    yaw: float
    battery: float = 90.0  # percent
    charging: bool = False
    sitting: bool = False
    fallen: bool = False
    powered: bool = True
    hottest_c: float = 38.0
    vx: float = 0.0
    vyaw: float = 0.0
    twist_at: float = 0.0
    sounds: list[str] = field(default_factory=list)
    skills_done: list[str] = field(default_factory=list)
    subscribed: bool = False
    tof_on: bool = False
    deliver: Callable[[str], None] | None = None


class SimWorld:
    """Owns the simulated ducks and advances them in time."""

    def __init__(self, seed: int = 7, drain_per_min: float = 1.0):
        self.rng = random.Random(seed)
        self.drain_per_min = drain_per_min
        self.ducks: dict[str, SimDuck] = {}
        self.t = 0.0
        self.reachy = SimReachy()

    def add_duck(self, name: str, x: float, y: float, yaw: float = 0.0,
                 battery: float = 90.0) -> SimDuck:
        duck = SimDuck(name=name, x=x, y=y, yaw=yaw, battery=battery)
        self.ducks[name] = duck
        return duck

    # ── the JSON-RPC face of one duck ──────────────────────────────────────────

    def handler(self, name: str) -> Callable[[str, Callable[[str], None]], None]:
        def handle(line: str, deliver: Callable[[str], None]) -> None:
            duck = self.ducks[name]
            duck.deliver = deliver
            request = json.loads(line)
            method = request.get("method")
            params = request.get("params") or {}
            result, error = self._answer(duck, method, params)
            if request.get("id") is None:
                return
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
            if error:
                reply["error"] = {"code": -32601, "message": error}
            else:
                reply["result"] = result
            deliver(json.dumps(reply))
        return handle

    def _answer(self, duck: SimDuck, method: str, params: dict[str, Any]):
        ok = {"accepted": True}
        if method == "hello":
            return {"api_version": 37, "daemon_version": "0.0.0-sim", "revision": None}, None
        if method == "robot.subscribe":
            duck.subscribed = True
            return {"accepted": True, "walk": "sim_walk.onnx",
                    "skills": ["roulade", "kick_left", "kick_right"]}, None
        if method == "tof.stream":
            duck.tof_on = True
            return {"accepted": True, "sensor": "SIM", "rows": 8, "cols": 8, "hz": 15}, None
        if method == "robot.health":
            return {"healthy": True,
                    "battery": {"volts": 6.6 + 1.8 * duck.battery / 100, "percent": duck.battery},
                    "motors": {"hottest": "left_knee", "max_c": duck.hottest_c,
                               "mean_c": duck.hottest_c - 6}}, None
        if not duck.powered:
            return None, "robot is powered off"
        if method == "robot.sound":
            duck.sounds.append(params["tag"])
            return ok, None
        if method == "robot.do":
            skill = params["skill"]
            if skill == "sit_toggle":
                duck.sitting = not duck.sitting
            duck.skills_done.append(skill)
            return ok, None
        if method == "robot.look":
            return {"head": {"neck_pitch": 0, "head_pitch": 0, "head_yaw": 0, "head_roll": 0},
                    "clamped": False}, None
        if method == "robot.move":
            if duck.fallen or duck.sitting:
                duck.vx = duck.vyaw = 0.0
            else:
                duck.vx, duck.vyaw = float(params.get("vx", 0)), float(params.get("vyaw", 0))
            duck.twist_at = self.t
            return ok, None
        if method in ("robot.head", "robot.mouth", "robot.enable", "robot.pose"):
            return ok, None
        if method == "robot.stop":
            duck.vx = duck.vyaw = 0.0
            return ok, None
        if method == "robot.shutdown":
            duck.sitting, duck.powered = True, False
            return ok, None
        return None, f"no such method: {method}"

    # ── time ───────────────────────────────────────────────────────────────────

    def step(self, dt: float) -> None:
        self.t += dt
        for duck in self.ducks.values():
            if self.t - duck.twist_at > 0.5:  # the deadman
                duck.vx = duck.vyaw = 0.0
            duck.yaw = (duck.yaw + duck.vyaw * dt + math.pi) % (2 * math.pi) - math.pi
            nx = duck.x + duck.vx * math.cos(duck.yaw) * dt
            ny = duck.y + duck.vx * math.sin(duck.yaw) * dt
            duck.x, duck.y = min(max(nx, 0.1), ROOM_W - 0.1), min(max(ny, 0.1), ROOM_H - 0.1)
            if duck.charging:
                duck.battery = min(100.0, duck.battery + 2.0 * dt / 60 * 10)
            else:
                work = 1.0 + 2.0 * abs(duck.vx) / 0.15
                duck.battery = max(0.0, duck.battery - self.drain_per_min * work * dt / 60)
            duck.hottest_c += (38.0 + 20.0 * abs(duck.vx) / 0.15 - duck.hottest_c) * min(dt / 60, 1)
        self.reachy.see(self)

    def publish(self) -> None:
        """Push each duck's notifications, the way its daemons would."""
        for duck in self.ducks.values():
            if not duck.deliver:
                continue
            if duck.subscribed:
                duck.deliver(json.dumps({"jsonrpc": "2.0", "method": "robot.state",
                                         "params": self._state(duck)}))
            if duck.tof_on:
                duck.deliver(json.dumps({"jsonrpc": "2.0", "method": "tof.frame",
                                         "params": self._tof(duck)}))
            duck.deliver(json.dumps({"jsonrpc": "2.0", "method": "media.detections",
                                     "params": self._detections(duck)}))

    def _state(self, duck: SimDuck) -> dict[str, Any]:
        return {"t": self.t, "move": {"requested": [duck.vx, 0, duck.vyaw],
                                      "applied": [duck.vx, 0, duck.vyaw]},
                "head": [0, 0, 0, 0], "policy": "walk" if duck.vx else "stand",
                "safety": {"fallen": duck.fallen, "limp": False, "gravity": [0, 0, -1]},
                "loop": {"hz": 50.0, "missed": 0}, "joints": [], "targets": [],
                "odom": {"position": [duck.x, duck.y, 0.12], "yaw": duck.yaw}}

    def _tof(self, duck: SimDuck) -> dict[str, Any]:
        # Distance to the wall straight ahead, the same in every zone: crude, and enough to
        # exercise the obstacle gate.
        # The bottom two rows see the floor half a metre ahead, unless the wall is nearer.
        ahead = self._ray_to_wall(duck)
        mm = int(min(ahead, 4.0) * 1000)
        floor_mm = min(mm, 500)
        distance = [mm] * 48 + [floor_mm] * 16
        status = [5 if ahead < 4.0 else 255] * 48 + [5] * 16
        return {"seq": int(self.t * 15), "at_us": int(self.t * 1e6), "rows": 8, "cols": 8,
                "distance_mm": distance, "status": status}

    def _ray_to_wall(self, duck: SimDuck) -> float:
        c, s = math.cos(duck.yaw), math.sin(duck.yaw)
        hits = []
        if c > 1e-6:
            hits.append((ROOM_W - duck.x) / c)
        if c < -1e-6:
            hits.append(-duck.x / c)
        if s > 1e-6:
            hits.append((ROOM_H - duck.y) / s)
        if s < -1e-6:
            hits.append(-duck.y / s)
        return min(h for h in hits if h >= 0) if hits else 99.0

    def _detections(self, duck: SimDuck) -> dict[str, Any]:
        boxes = []
        for other in self.ducks.values():
            if other is duck:
                continue
            dx, dy = other.x - duck.x, other.y - duck.y
            dist = math.hypot(dx, dy)
            bearing = (math.atan2(dy, dx) - duck.yaw + math.pi) % (2 * math.pi) - math.pi
            if dist < 3.0 and abs(bearing) < 0.5:
                cx = 640 * (0.5 - bearing / 1.08)
                half = max(8.0, 60.0 / max(dist, 0.2))
                boxes.append({"x0": cx - half, "y0": 300 - half, "x1": cx + half,
                              "y1": 300 + half, "score": 0.9})
        return {"width": 640, "height": 480, "took_ms": 25.0, "boxes": boxes}


class SimReachy:
    """Records what the Nest asked Reachy to do, and 'sees' the ducks from the TV stand."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.awake = False
        self.positions: dict[str, tuple[float, float]] = {}
        self.head_yaw = 0.0
        self.antennas = (0.0, 0.0)

    def see(self, world: SimWorld) -> None:
        self.positions = {name: (d.x, d.y) for name, d in world.ducks.items()}

    async def wake_up(self) -> None:
        self.awake = True
        self.calls.append(("wake_up", {}))

    async def goto_sleep(self) -> None:
        self.awake = False
        self.calls.append(("goto_sleep", {}))

    async def goto(self, head: dict[str, float] | None = None,
                   antennas: tuple[float, float] | None = None,
                   body_yaw: float | None = None, duration: float = 1.0) -> None:
        if head is not None:
            self.head_yaw = head.get("yaw", 0.0)
        if antennas is not None:
            self.antennas = antennas
        self.calls.append(("goto", {"head": head, "antennas": antennas, "body_yaw": body_yaw,
                                    "duration": duration}))

    async def doa(self) -> dict[str, Any] | None:
        return None

    async def close(self) -> None:
        return None
