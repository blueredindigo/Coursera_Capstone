"""The Nest's own safety gate, in front of every walk it asks for.

This is a second line, not the first. The duck's `robotd` runs its own safety every tick
(fall detection, the deadman, limits) and **always wins**; nothing here can override it. What
this adds is the judgement a 50 Hz loop on the duck does not have: do not *ask* for a walk
toward a wall or off an edge, do not ask a hot or fallen duck to move, and stop asking the
moment the picture goes stale.

ToF zones follow the `tof` crate: status 5 or 9 is a range, 255 is "nothing in range", anything
else is unusable. Rows are top to bottom, columns the sensor's left to right.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

VALID = (5, 9)
NO_TARGET = 255


@dataclass
class SafetyLimits:
    stale_state_s: float = 1.0      # no robot.state for this long: do not ask for motion
    stale_tof_s: float = 0.6        # no depth frame for this long: no forward motion
    obstacle_m: float = 0.25        # something this close ahead: stop
    edge_far_m: float = 1.2         # the floor rows read farther than this, or nothing ...
    edge_zones: int = 3             # ... in this many of the 4 central floor zones: an edge
    hot_servo_c: float = 65.0       # hottest servo at or above this: rest, no walking
    require_tof: bool = True        # without a ToF stream, refuse forward motion


def zones(frame: dict[str, Any]) -> list[list[float | None | str]]:
    """The frame as rows of metres, None (unusable) or 'far' (nothing in range)."""
    rows, cols = int(frame.get("rows", 8)), int(frame.get("cols", 8))
    distances, statuses = frame.get("distance_mm") or [], frame.get("status") or []
    grid: list[list[float | None | str]] = []
    for r in range(rows):
        row: list[float | None | str] = []
        for c in range(cols):
            i = r * cols + c
            status = statuses[i] if i < len(statuses) else NO_TARGET
            mm = distances[i] if i < len(distances) else 0
            if status in VALID and mm >= 0:
                row.append(mm / 1000.0)
            elif status == NO_TARGET:
                row.append("far")
            else:
                row.append(None)
        grid.append(row)
    return grid


def obstacle_ahead(frame: dict[str, Any], limit_m: float) -> bool:
    grid = zones(frame)
    rows, cols = len(grid), len(grid[0]) if grid else 0
    for r in range(rows // 4, rows - rows // 4):          # the middle band ...
        for c in range(cols // 4, cols - cols // 4):      # ... the central columns
            value = grid[r][c]
            if isinstance(value, float) and value < limit_m:
                return True
    return False


def edge_ahead(frame: dict[str, Any], far_m: float, needed: int) -> bool:
    """The bottom rows look down at the floor ahead. If most central floor zones see nothing,
    or see much farther than a floor can be, the floor has gone: a table edge or a step.

    Conservative on purpose: a head tilted up also makes the floor vanish, and that stops a walk
    too. A false stop costs a moment; a missed edge costs a duck."""
    grid = zones(frame)
    if not grid:
        return True
    rows, cols = len(grid), len(grid[0])
    floor = [grid[r][c] for r in (rows - 2, rows - 1)
             for c in range(cols // 2 - 1, cols // 2 + 1)]
    missing = sum(1 for v in floor if v == "far" or (isinstance(v, float) and v > far_m))
    return missing >= needed


def hand_near(frame: dict[str, Any], within_m: float = 0.15) -> bool:
    """Something close in front of the beak: a hand, usually. The affection sensor."""
    grid = zones(frame)
    close = sum(1 for row in grid[: len(grid) // 2] for v in row
                if isinstance(v, float) and v < within_m)
    return close >= 3


class SafetyGate:
    def __init__(self, limits: SafetyLimits | None = None):
        self.limits = limits or SafetyLimits()
        self.last_refusal: str | None = None

    def may_move(self, duck: Any, forward: bool = True) -> tuple[bool, str | None]:
        """May the Nest ask this duck to walk right now? (answer, reason if not)"""
        lim = self.limits
        now = time.monotonic()
        reason = None
        if not duck.connected:
            reason = "not connected"
        elif duck.state is None or now - duck.state_at > lim.stale_state_s:
            reason = "robot.state is stale"
        elif duck.fallen:
            reason = "fallen: the duck gets itself up; the Nest waits"
        elif duck.health.hottest_c is not None and duck.health.hottest_c >= lim.hot_servo_c:
            reason = f"{duck.health.hottest_joint} is at {duck.health.hottest_c:.0f}°C: resting"
        elif forward:
            if duck.tof is None or now - duck.tof_at > lim.stale_tof_s:
                if lim.require_tof:
                    reason = "no fresh depth frame: not walking blind"
            elif obstacle_ahead(duck.tof, lim.obstacle_m):
                reason = "obstacle ahead"
            elif edge_ahead(duck.tof, lim.edge_far_m, lim.edge_zones):
                reason = "the floor ahead disappears: possible edge"
        self.last_refusal = reason
        return reason is None, reason
