"""The living room as Reachy sees it from the TV stand: a drift-free floor map.

Three pieces:

* **FloorHomography.** Reachy's camera looks down at the floor from a fixed spot, so a pixel on
  the floor maps to a floor point by one 3×3 homography. Calibrate it once: put a duck (or a
  printed tag) on four or more marked spots whose floor positions you measured, click them in
  the Pond, and `fit()`.
* **DuckTracker.** The detector finds Microducks but cannot tell Ah-Ah from Tee-Tee. Tracks keep
  their identity by continuity; a **roll call** (one duck takes a small step while the other
  stands still) assigns names when continuity is lost.
* **Navigator.** Each duck's odometry lives in a frame that starts wherever it faced at boot.
  Watching it walk in Reachy's map gives the offset between the two, so "go to the green chair"
  becomes a heading and a distance the duck understands.

Room frame: metres on the floor, origin and axes wherever you measured them from (a corner of
the rug is a good choice), `x` and `y` as you drew them. Keep one convention and write it down.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np


class FloorHomography:
    def __init__(self, matrix: list[list[float]] | None = None):
        self.h = np.array(matrix, dtype=float) if matrix is not None else None

    @property
    def ready(self) -> bool:
        return self.h is not None

    def fit(self, pixels: list[tuple[float, float]], floor: list[tuple[float, float]]) -> float:
        """Direct linear transform from ≥4 pixel↔floor pairs. Returns mean reprojection error, m."""
        if len(pixels) != len(floor) or len(pixels) < 4:
            raise ValueError("need at least four pixel/floor pairs")
        rows = []
        for (u, v), (x, y) in zip(pixels, floor):
            rows.append([-u, -v, -1, 0, 0, 0, u * x, v * x, x])
            rows.append([0, 0, 0, -u, -v, -1, u * y, v * y, y])
        _, _, vt = np.linalg.svd(np.array(rows, dtype=float))
        h = vt[-1].reshape(3, 3)
        self.h = h / h[2, 2]
        errors = [math.dist(self.to_floor(u, v), p) for (u, v), p in zip(pixels, floor)]
        return float(np.mean(errors))

    def to_floor(self, u: float, v: float) -> tuple[float, float]:
        if self.h is None:
            raise RuntimeError("floor homography not calibrated")
        x, y, w = self.h @ np.array([u, v, 1.0])
        return float(x / w), float(y / w)

    def as_list(self) -> list[list[float]] | None:
        return self.h.tolist() if self.h is not None else None


@dataclass
class Track:
    x: float
    y: float
    at: float
    name: str | None = None


class DuckTracker:
    """Keeps two identical ducks apart in Reachy's view."""

    def __init__(self, names: list[str], gate_m: float = 0.6, forget_s: float = 5.0):
        self.names = names
        self.gate_m = gate_m
        self.forget_s = forget_s
        self.tracks: list[Track] = []

    def update(self, points: list[tuple[float, float]], now: float | None = None) -> None:
        """Feed this frame's floor positions of every detected duck."""
        now = time.monotonic() if now is None else now
        self.tracks = [t for t in self.tracks if now - t.at <= self.forget_s]
        unmatched = list(points)
        # Greedy nearest neighbour: with two ducks this is the optimal assignment in practice.
        pairs = sorted(((math.dist((t.x, t.y), p), i, j)
                        for i, t in enumerate(self.tracks) for j, p in enumerate(points)))
        used_tracks, used_points = set(), set()
        for distance, i, j in pairs:
            if i in used_tracks or j in used_points or distance > self.gate_m:
                continue
            track = self.tracks[i]
            track.x, track.y = points[j]
            track.at = now
            used_tracks.add(i)
            used_points.add(j)
        for j, p in enumerate(points):
            if j not in used_points:
                self.tracks.append(Track(p[0], p[1], now))
        # One named track left and one unnamed track: the unnamed one is the other duck.
        named = {t.name for t in self.tracks if t.name}
        unnamed = [t for t in self.tracks if not t.name]
        missing = [n for n in self.names if n not in named]
        if len(unnamed) == 1 and len(missing) == 1 and len(self.tracks) == len(self.names):
            unnamed[0].name = missing[0]

    def snapshot(self) -> dict[int, tuple[float, float]]:
        """Where every live track is now, keyed by track identity. Take one before a roll call."""
        return {id(t): (t.x, t.y) for t in self.tracks}

    def roll_call(self, mover: str, before: dict[int, tuple[float, float]]) -> bool:
        """Name tracks by motion: `mover` took a step, the other stood still. True if resolved.

        Usage: `snap = tracker.snapshot()`, make `mover` step ~10 cm, feed a few frames, then
        `tracker.roll_call(mover, snap)`."""
        moved = [(math.dist(before[id(t)], (t.x, t.y)), t) for t in self.tracks if id(t) in before]
        if not moved:
            return False
        moved.sort(key=lambda item: item[0], reverse=True)
        top, runner_up = moved[0][0], (moved[1][0] if len(moved) > 1 else 0.0)
        if top < 0.05 or runner_up > top * 0.5:
            return False  # nobody clearly moved, or both did
        others = [n for n in self.names if n != mover]
        moved[0][1].name = mover
        for _, track in moved[1:]:
            track.name = others[0] if len(others) == 1 else None
        return True

    def position(self, name: str, max_age_s: float = 3.0) -> tuple[float, float] | None:
        now = time.monotonic()
        for t in self.tracks:
            if t.name == name and now - t.at <= max_age_s:
                return t.x, t.y
        return None


@dataclass
class RoomMap:
    """Where things are in the room frame, as far as Reachy can tell."""

    reachy_xy: tuple[float, float] = (0.0, 0.0)
    reachy_facing: float = 0.0  # room-frame heading Reachy's body_yaw=0 points along, rad
    landmarks: dict[str, tuple[float, float]] = field(default_factory=dict)
    duck_positions: dict[str, tuple[float, float, float]] = field(default_factory=dict)

    def set_duck(self, name: str, x: float, y: float, at: float | None = None) -> None:
        self.duck_positions[name] = (x, y, time.monotonic() if at is None else at)

    def duck(self, name: str, max_age_s: float = 3.0) -> tuple[float, float] | None:
        entry = self.duck_positions.get(name)
        if entry is None or time.monotonic() - entry[2] > max_age_s:
            return None
        return entry[0], entry[1]

    def distance(self, a: str, b: str, max_age_s: float = 3.0) -> float | None:
        pa, pb = self.duck(a, max_age_s), self.duck(b, max_age_s)
        if pa is None or pb is None:
            return None
        return math.dist(pa, pb)

    def bearing_from_reachy(self, x: float, y: float) -> float:
        """The body/head yaw Reachy needs to face a floor point, rad, positive = its left."""
        angle = math.atan2(y - self.reachy_xy[1], x - self.reachy_xy[0]) - self.reachy_facing
        return (angle + math.pi) % (2 * math.pi) - math.pi


def wrap(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


class Navigator:
    """Turns a room-frame goal into turn-then-walk twists, using Reachy's map to learn how each
    duck's odometry frame sits in the room."""

    def __init__(self, min_baseline_m: float = 0.2):
        self.min_baseline_m = min_baseline_m
        self.offset: float | None = None  # room_heading = odom_yaw + offset
        self._anchor: tuple[tuple[float, float], tuple[float, float]] | None = None

    def observe(self, odom_xy: tuple[float, float], room_xy: tuple[float, float]) -> None:
        """Call while the duck walks. Once it has covered a baseline in both frames, the angle
        between the two displacement vectors is the frame offset."""
        if self._anchor is None:
            self._anchor = (odom_xy, room_xy)
            return
        (ox0, oy0), (rx0, ry0) = self._anchor
        dox, doy = odom_xy[0] - ox0, odom_xy[1] - oy0
        drx, dry = room_xy[0] - rx0, room_xy[1] - ry0
        if math.hypot(dox, doy) < self.min_baseline_m or math.hypot(drx, dry) < self.min_baseline_m:
            return
        measured = wrap(math.atan2(dry, drx) - math.atan2(doy, dox))
        # Smooth: odometry and the camera are both noisy, and the offset drifts slowly.
        self.offset = measured if self.offset is None else wrap(
            self.offset + 0.3 * wrap(measured - self.offset))
        self._anchor = (odom_xy, room_xy)

    @property
    def calibrated(self) -> bool:
        return self.offset is not None

    def plan(self, odom_yaw: float, here: tuple[float, float], goal: tuple[float, float],
             arrive_m: float = 0.25) -> tuple[str, float]:
        """Next step toward `goal`: ('arrived', 0) | ('turn', radians) | ('walk', metres).

        Before calibration the answer is ('calibrate', 0): walk a short straight line first."""
        distance = math.dist(here, goal)
        if distance <= arrive_m:
            return "arrived", 0.0
        if self.offset is None:
            return "calibrate", 0.0
        heading = wrap(odom_yaw + self.offset)
        error = wrap(math.atan2(goal[1] - here[1], goal[0] - here[0]) - heading)
        if abs(error) > 0.35:
            return "turn", error
        return "walk", distance
