"""The tamagotchi core: needs that come from real sensors.

Each need is an *urge* from 0 (satisfied) to 1 (desperate). Urges rise on their own over time,
at rates the duck's personality sets, and fall when the real world satisfies them:

| need       | satisfied by                                                        |
|------------|---------------------------------------------------------------------|
| energy     | the battery itself (via Tiredness), the charger, a nap              |
| curiosity  | seeing something new: a Duckdex meal or snack, exploring            |
| affection  | a hand near its face (ToF), head scratches, you being close         |
| social     | the other duck nearby (Reachy's map, the duck detector)             |
| play       | a game, a ball, a chase, the other duck                             |
| wanderlust | a field trip (only rises after hearing about one)                   |

Nothing dies and nothing is lost for good: a neglected duck is mopey, and delighted when you
come back.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .personality import Personality

NEEDS = ("energy", "curiosity", "affection", "social", "play", "wanderlust")

# Urge rise per minute at trait 0.5, before personality scaling.
BASE_RISE_PER_MIN = {
    "curiosity": 0.012,
    "affection": 0.008,
    "social": 0.010,
    "play": 0.009,
    "wanderlust": 0.0,
}


@dataclass
class Needs:
    personality: Personality
    urges: dict[str, float] = field(default_factory=lambda: {n: 0.3 for n in NEEDS})

    def __post_init__(self) -> None:
        self.urges["wanderlust"] = 0.0

    def _rate(self, need: str) -> float:
        p = self.personality
        scale = {
            "curiosity": 0.5 + p.curiosity,
            "affection": 0.5 + p.cuddliness,
            "social": 0.5 + p.sociability,
            "play": 0.5 + p.playfulness,
            "wanderlust": 1.0,
        }[need]
        return BASE_RISE_PER_MIN[need] * scale

    def tick(self, dt_s: float, energy: float) -> None:
        """Advance time. `energy` is 0..1 from Tiredness: the energy urge is its complement."""
        minutes = dt_s / 60.0
        for need in BASE_RISE_PER_MIN:
            self.urges[need] = min(1.0, self.urges[need] + self._rate(need) * minutes)
        self.urges["energy"] = round(1.0 - max(0.0, min(1.0, energy)), 3)

    def satisfy(self, need: str, amount: float) -> None:
        self.urges[need] = max(0.0, self.urges[need] - amount)

    def stir(self, need: str, amount: float) -> None:
        self.urges[need] = min(1.0, self.urges[need] + amount)

    # ── what the world did ──────────────────────────────────────────────────────

    def on_petted(self, seconds: float) -> None:
        self.satisfy("affection", 0.08 * seconds)

    def on_friend_near(self, seconds: float) -> None:
        self.satisfy("social", 0.03 * seconds)

    def on_fed(self, meal: str) -> None:
        self.satisfy("curiosity", {"meal": 0.6, "snack": 0.15, "boring": 0.0}.get(meal, 0.0))
        if meal == "boring":
            self.stir("play", 0.05)

    def on_played(self, minutes: float) -> None:
        self.satisfy("play", 0.25 * minutes)

    def on_heard_trip_story(self, wonder_count: int) -> None:
        self.stir("wanderlust", min(0.8, 0.2 + 0.15 * wonder_count))

    def on_went_on_trip(self) -> None:
        self.urges["wanderlust"] = 0.0
        self.satisfy("curiosity", 0.8)

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 3) for k, v in self.urges.items()}

    @property
    def mood(self) -> str:
        """One word for the Pond and the TV pond."""
        u = self.urges
        if u["energy"] > 0.8:
            return "sleepy"
        worst = max((n for n in NEEDS if n != "energy"), key=lambda n: u[n])
        if u[worst] < 0.35:
            return "content"
        return {"curiosity": "bored", "affection": "lonely for you", "social": "missing its friend",
                "play": "restless", "wanderlust": "dreaming of outside"}[worst]


def needs_state(needs: Needs) -> dict[str, object]:
    return {"urges": needs.as_dict(), "mood": needs.mood,
            "personality": asdict(needs.personality)}
