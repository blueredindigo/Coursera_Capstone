"""The floor map: fog of war over the flat, in 5 cm cells.

Every cell remembers **when it was last seen and by whom**: never (black on the Pond's Map),
some time ago (fog, darker the older it is), or right now. Nothing is forgotten; old
information just gets older until someone looks again. That's the plan's layer 1 (§15).

Who can see what:

* **Reachy**, from the TV stand: the part of the floor its camera covers, worked out from the
  floor calibration (the same homography the room eye uses). Only while its head is at the
  pose the calibration was taken in, because the homography is only true there.
* **A duck**, where Reachy (or later its own anchors) says it is: a small disc around it.
* **Your walkthrough**, once the Olares pipeline has turned it into a floor plan: everything it
  saw counts as seen at the time you filmed, and its walls and furniture are drawn in.

Stored as one small `.npz` file under the Nest's data directory, written atomically.
"""

from __future__ import annotations

import base64
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

WALKTHROUGH = "your walkthrough"
REACHY = "reachy"
BUCKET_MIN = 30.0  # one age step on the wire: half an hour
FRESH_S = 12 * 3600.0  # "fresh" in the coverage summary


class FloorMap:
    def __init__(self, bounds: tuple[float, float, float, float], cell: float = 0.05,
                 who: list[str] | None = None, path: str | Path | None = None):
        x0, y0, x1, y1 = bounds
        self.cell = float(cell)
        self.x0, self.y0 = float(x0), float(y0)
        self.w = max(1, int(math.ceil((x1 - x0) / self.cell)))
        self.h = max(1, int(math.ceil((y1 - y0) / self.cell)))
        self.who = ["", WALKTHROUGH, REACHY] + [w for w in (who or [])
                                                 if w not in (WALKTHROUGH, REACHY)]
        self.seen_at = np.full((self.h, self.w), np.nan)   # epoch seconds
        self.seen_by = np.zeros((self.h, self.w), np.uint8)
        self.visible_at = np.full((self.h, self.w), np.nan)
        self.floor = np.ones((self.h, self.w), bool)        # part of the flat (walkthrough)
        self.blocked = np.zeros((self.h, self.w), bool)     # walls and furniture (walkthrough)
        self.path = Path(path) if path else None
        self._saved = 0.0
        # cell centres, for rasterising shapes
        xs = self.x0 + (np.arange(self.w) + 0.5) * self.cell
        ys = self.y0 + (np.arange(self.h) + 0.5) * self.cell
        self._cx, self._cy = np.meshgrid(xs, ys)
        if self.path and self.path.exists():
            self._load()

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x0 + self.w * self.cell, self.y0 + self.h * self.cell)

    def code(self, who: str) -> int:
        if who not in self.who:
            self.who.append(who)
        return self.who.index(who)

    # ── seeing ─────────────────────────────────────────────────────────────────

    def mark(self, mask: np.ndarray, who: str, now: float | None = None) -> int:
        """Cells in `mask` are seen right now by `who`. Returns how many."""
        now = time.time() if now is None else now
        mask = mask & self.floor
        self.seen_at[mask] = now
        self.visible_at[mask] = now
        self.seen_by[mask] = self.code(who)
        return int(mask.sum())

    def polygon_mask(self, polygon: list[tuple[float, float]]) -> np.ndarray:
        """Cells whose centre is inside the polygon (even-odd rule)."""
        inside = np.zeros((self.h, self.w), bool)
        if len(polygon) < 3:
            return inside
        px, py = self._cx, self._cy
        n = len(polygon)
        for i in range(n):
            (xa, ya), (xb, yb) = polygon[i], polygon[(i + 1) % n]
            crosses = (ya > py) != (yb > py)
            with np.errstate(divide="ignore", invalid="ignore"):
                x_at = xa + (py - ya) * (xb - xa) / (yb - ya)
            inside ^= crosses & (px < x_at)
        return inside

    def disc_mask(self, x: float, y: float, r: float) -> np.ndarray:
        return (self._cx - x) ** 2 + (self._cy - y) ** 2 <= r * r

    def see_polygon(self, polygon: list[tuple[float, float]], who: str,
                    now: float | None = None) -> int:
        return self.mark(self.polygon_mask(polygon), who, now)

    def see_disc(self, x: float, y: float, r: float, who: str, now: float | None = None) -> int:
        return self.mark(self.disc_mask(x, y, r), who, now)

    def seed(self, floor: np.ndarray, blocked: np.ndarray, at: float) -> None:
        """The walkthrough's floor plan, already resampled onto this grid."""
        self.floor = floor.astype(bool) | blocked.astype(bool)
        self.blocked = blocked.astype(bool)
        never = np.isnan(self.seen_at) & self.floor
        self.seen_at[never] = at
        self.seen_by[never] = self.code(WALKTHROUGH)

    # ── reading ────────────────────────────────────────────────────────────────

    def cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        i, j = int((y - self.y0) // self.cell), int((x - self.x0) // self.cell)
        return (i, j) if 0 <= i < self.h and 0 <= j < self.w else None

    def at(self, x: float, y: float, now: float | None = None) -> dict[str, Any]:
        """What's known about one spot."""
        now = time.time() if now is None else now
        ij = self.cell_of(x, y)
        if ij is None or not self.floor[ij]:
            return {"known": False, "inside": False}
        seen = self.seen_at[ij]
        if np.isnan(seen):
            return {"known": False, "inside": True}
        return {"known": True, "inside": True, "age_s": float(now - seen),
                "by": self.who[self.seen_by[ij]],
                "now": bool(now - self.visible_at[ij] < 3.0) if not np.isnan(
                    self.visible_at[ij]) else False}

    def coverage(self, now: float | None = None) -> dict[str, float]:
        """Share of the floor seen by a robot at all, and in the last 12 hours."""
        now = time.time() if now is None else now
        open_floor = self.floor & ~self.blocked
        total = max(1, int(open_floor.sum()))
        robot = open_floor & ~np.isnan(self.seen_at) & (self.seen_by != self.code(WALKTHROUGH))
        fresh = robot & (now - np.nan_to_num(self.seen_at, nan=-1e18) < FRESH_S)
        return {"seen": round(100.0 * robot.sum() / total, 1),
                "fresh": round(100.0 * fresh.sum() / total, 1)}

    def encode(self, now: float | None = None) -> dict[str, Any]:
        """The map for the Pond, compactly: one byte per cell.

        `cells`: 0 never seen, 255 in view right now, otherwise 1 + age in half hours (capped at
        254, about five days). 253 marks a wall or furniture from the walkthrough. `who`: index
        into `legend` of whoever saw each cell last."""
        now = time.time() if now is None else now
        age_min = (now - self.seen_at) / 60.0
        cells = np.zeros((self.h, self.w), np.uint8)
        seen = ~np.isnan(self.seen_at)
        cells[seen] = np.clip(1 + age_min[seen] // BUCKET_MIN, 1, 252).astype(np.uint8)
        vis = ~np.isnan(self.visible_at) & (now - np.nan_to_num(self.visible_at, nan=-1e18) < 3.0)
        cells[vis] = 255
        cells[self.blocked] = 253
        cells[~self.floor] = 0
        b64 = lambda a: base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
        return {"w": self.w, "h": self.h, "cell": self.cell, "x0": self.x0, "y0": self.y0,
                "bucket_min": BUCKET_MIN, "cells": b64(cells), "who": b64(self.seen_by),
                "legend": self.who, "outside": b64(~self.floor),
                "coverage": self.coverage(now)}

    # ── persistence ────────────────────────────────────────────────────────────

    def save(self, force: bool = False, every_s: float = 60.0) -> None:
        if not self.path or (not force and time.monotonic() - self._saved < every_s):
            return
        self._saved = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".floormap-", suffix=".npz")
        with os.fdopen(fd, "wb") as handle:
            np.savez_compressed(handle, seen_at=self.seen_at, seen_by=self.seen_by,
                                floor=self.floor, blocked=self.blocked,
                                who=np.array(self.who), geometry=np.array(
                                    [self.x0, self.y0, self.cell, self.w, self.h]))
        os.replace(tmp, self.path)

    def _load(self) -> None:
        try:
            data = np.load(self.path, allow_pickle=False)
            x0, y0, cell, w, h = data["geometry"]
        except Exception:
            return  # unreadable: start a fresh map rather than refuse to start
        if (int(w), int(h)) != (self.w, self.h) or not np.allclose([x0, y0, cell],
                                                                   [self.x0, self.y0, self.cell]):
            return  # the map's bounds changed in config: start fresh
        saved_who = [str(s) for s in data["who"]]
        remap = np.array([self.code(name) for name in saved_who], np.uint8)
        self.seen_at = data["seen_at"]
        self.seen_by = remap[data["seen_by"]]
        self.floor = data["floor"]
        self.blocked = data["blocked"]


def view_polygon(homography, width: int, height: int, origin: tuple[float, float],
                 max_range: float = 6.0, steps: int = 24) -> list[tuple[float, float]]:
    """The floor Reachy's camera covers: the image's outline, mapped onto the floor.

    Rows high in the image look at the far wall or above the horizon, where the floor
    homography means nothing, so the outline is walked up each side of the image and stops
    where the floor is more than `max_range` metres away or the mapping turns over."""
    h = np.asarray(homography, float)

    def floor(u: float, v: float) -> tuple[float, float] | None:
        x, y, w = h @ np.array([u, v, 1.0])
        if w <= 1e-9:
            return None
        p = (float(x / w), float(y / w))
        return p if math.dist(p, origin) <= max_range else None

    left, right = [], []
    for k in range(steps + 1):
        v = (height - 1) * (1 - k / steps)  # bottom row up
        a, b = floor(0, v), floor(width - 1, v)
        if a is None or b is None:
            break
        left.append(a)
        right.append(b)
    if len(left) < 2:
        return []
    top = []
    v_top = (height - 1) * (1 - (len(left) - 1) / steps)
    for k in range(1, steps):
        p = floor((width - 1) * k / steps, v_top)
        if p is not None:
            top.append(p)
    bottom = []
    for k in range(steps - 1, 0, -1):
        p = floor((width - 1) * k / steps, height - 1)
        if p is not None:
            bottom.append(p)
    return left + top + right[::-1] + bottom


def cone_polygon(origin: tuple[float, float], facing: float, half: float, reach: float,
                 steps: int = 12) -> list[tuple[float, float]]:
    """A simple view cone (the simulated Reachy's eyes)."""
    x, y = origin
    return [(x, y)] + [(x + reach * math.cos(facing - half + 2 * half * k / steps),
                        y + reach * math.sin(facing - half + 2 * half * k / steps))
                       for k in range(steps + 1)]
