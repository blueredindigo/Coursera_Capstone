"""Tiredness is the battery gauge you can see.

The battery percentage (`robot.health` → `battery.percent`, the robot's own mapping of pack
voltage) is sorted into bands, with hysteresis so a duck hovering at a boundary does not flicker
between looking tired and looking fine. Each band has cues: how the duck holds its head, which
sounds it makes, and which behaviours it prefers.

**Tiredness never changes how well a duck walks.** No band lowers a speed or swaps a gait. A
tired duck starts fewer long games and sits more often; when it does walk, it walks with its
best policy at full ability.

Charging is inferred, not reported: the duck has no charging flag on the wire, so a percentage
that keeps rising over a couple of minutes means the duck is on the charger.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum


class Band(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    VERY_LOW = "very_low"
    CHARGING = "charging"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Cues:
    """How a band looks. `head_pitch` is added to the neutral head pose (radians, negative
    looks down); `sounds` are voice tags the duck may make idly; `prefers` biases the behaviour
    picker; `reachy` is Reachy's antenna expression when it looks at this duck."""

    head_pitch: float
    head_move_s: float
    sounds: tuple[str, ...]
    prefers: dict[str, float]
    reachy: str | None
    sit_between_activities: bool
    goes_to_bed: bool


CUES = {
    Band.HIGH: Cues(0.08, 0.6, ("chirp", "greet"), {"play": 1.3, "wander": 1.2}, None,
                    False, False),
    Band.MEDIUM: Cues(0.0, 0.8, ("chirp",), {}, None, False, False),
    Band.LOW: Cues(-0.12, 1.4, ("coo",), {"rest": 1.8, "look_around": 1.3, "play": 0.5,
                                          "wander": 0.7}, None, True, False),
    Band.VERY_LOW: Cues(-0.3, 2.0, ("coo",), {"go_to_bed": 6.0, "rest": 3.0, "play": 0.05,
                                              "wander": 0.1, "seek_friend": 0.3}, "droop",
                        True, True),
    Band.CHARGING: Cues(-0.35, 2.5, ("coo",), {"nap": 10.0}, "tired_look", True, False),
    Band.UNKNOWN: Cues(0.0, 0.8, (), {}, None, False, False),
}


@dataclass
class Thresholds:
    """Band edges in percent. `hysteresis` is how far past an edge the reading must go before
    the band changes back. Yours to tune once you know how long your packs last."""

    high: float = 70.0
    low: float = 35.0
    very_low: float = 15.0
    hysteresis: float = 3.0
    charging_rise: float = 1.0      # percent gained ...
    charging_window_s: float = 120.0  # ... within this long means "on the charger"
    hot_servo_c: float = 60.0       # a hot servo reads as "needs a rest" too


class Tiredness:
    def __init__(self, thresholds: Thresholds | None = None):
        self.t = thresholds or Thresholds()
        self.band = Band.UNKNOWN
        self.percent: float | None = None
        self.needs_rest_for_heat = False
        self._history: deque[tuple[float, float]] = deque()

    def update(self, now: float, percent: float | None, hottest_c: float | None = None) -> Band:
        self.needs_rest_for_heat = hottest_c is not None and hottest_c >= self.t.hot_servo_c
        if percent is None:
            return self.band  # absent is "not known yet", never an empty battery
        self.percent = percent
        self._history.append((now, percent))
        while self._history and now - self._history[0][0] > self.t.charging_window_s:
            self._history.popleft()

        if self._charging(now):
            self.band = Band.CHARGING
            return self.band
        if self.band == Band.CHARGING:
            self.band = Band.UNKNOWN  # off the charger: re-derive from the number

        self.band = self._band_for(percent)
        return self.band

    def _charging(self, now: float) -> bool:
        if len(self._history) < 2:
            return False
        t0, p0 = self._history[0]
        t1, p1 = self._history[-1]
        if t1 - t0 < self.t.charging_window_s * 0.5:
            return self.band == Band.CHARGING and p1 >= p0
        return p1 - p0 >= self.t.charging_rise

    def _band_for(self, p: float) -> Band:
        h, t = self.t.hysteresis, self.t
        current = self.band
        # Moving down a band needs the plain edge; moving back up needs edge + hysteresis.
        if current in (Band.UNKNOWN,):
            if p >= t.high:
                return Band.HIGH
            if p >= t.low:
                return Band.MEDIUM
            if p >= t.very_low:
                return Band.LOW
            return Band.VERY_LOW
        order = [Band.VERY_LOW, Band.LOW, Band.MEDIUM, Band.HIGH]
        edges = {Band.LOW: t.very_low, Band.MEDIUM: t.low, Band.HIGH: t.high}
        index = order.index(current)
        # Down?
        while index > 0 and p < edges[order[index]]:
            index -= 1
        # Up?
        while index < len(order) - 1 and p >= edges[order[index + 1]] + h:
            index += 1
        return order[index]

    @property
    def cues(self) -> Cues:
        return CUES[self.band]

    @property
    def energy(self) -> float:
        """0..1, for the needs model: how much get-up-and-go the battery allows."""
        if self.band == Band.CHARGING:
            return 0.1
        if self.percent is None:
            return 0.6
        energy = self.percent / 100.0
        return energy * (0.6 if self.needs_rest_for_heat else 1.0)
