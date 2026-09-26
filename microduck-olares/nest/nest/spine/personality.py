"""Temperament from a seed, so each duck is recognisably itself.

The duck's voice already has a per-robot personality seed (`sounds/src/personality.rs`); it is
not on the wire, so the Nest rolls temperament from the duck's name instead, with the same idea:
a stable function of the seed, different enough between two seeds to feel like two creatures.
Override any trait in the config if the dice give you a duck you did not expect.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import asdict, dataclass

TRAITS = ("boldness", "chattiness", "playfulness", "sociability", "curiosity", "cuddliness")


@dataclass
class Personality:
    boldness: float      # explores far, approaches new things fast
    chattiness: float    # sounds per minute
    playfulness: float   # starts games
    sociability: float   # seeks the other duck
    curiosity: float     # hunger for novelty rises faster
    cuddliness: float    # seeks you out, loves petting

    @classmethod
    def from_seed(cls, name: str, overrides: dict[str, float] | None = None) -> "Personality":
        rng = random.Random(zlib.crc32(name.encode()))
        # Keep every trait away from the extremes: a duck at 0.0 curiosity is a broken duck.
        values = {trait: round(0.2 + 0.6 * rng.random(), 2) for trait in TRAITS}
        values.update(overrides or {})
        return cls(**values)

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def describe(self) -> str:
        words = []
        words.append("bold" if self.boldness > 0.55 else "careful")
        words.append("chatty" if self.chattiness > 0.55 else "quiet")
        if self.playfulness > 0.6:
            words.append("playful")
        if self.sociability > 0.6:
            words.append("sociable")
        if self.curiosity > 0.6:
            words.append("curious")
        if self.cuddliness > 0.6:
            words.append("cuddly")
        return ", ".join(words)
