"""Landmark memory: "a yellow ball, behind the green chair near the sofa".

Each duck keeps its **own** memory. That is the point: Ah-Ah knows where the ball is because it
saw it; Tee-Tee only knows once Ah-Ah tells it, and then only second-hand, with less confidence,
until it goes and looks.

* **Landmarks** are big things that do not move: sofa, chairs, table, doorways. They can have a
  room-frame position (from Reachy's map) and aliases ("Grandma's chair").
* **Objects** move: balls, socks, keys, the cat. An object can carry an NFC tag id, which makes
  it unambiguous once Pollen exposes the head/beak antennas to software.
* **Sightings** pin an object to landmarks with a relation, a time, who saw it and how sure.

Stored as one JSON file per duck, written atomically, so a power cut never leaves half a memory.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Spatial relations the vision model is asked for, in a fixed order so they encode as one byte.
RELATIONS = ("behind", "under", "on", "next to", "in front of", "left of", "right of", "inside",
             "near")


@dataclass
class Landmark:
    name: str                      # "green chair"
    aliases: list[str] = field(default_factory=list)
    xy: tuple[float, float] | None = None  # room frame, metres, when Reachy can see it
    room: str = "living room"


@dataclass
class Sighting:
    obj: str                        # "yellow ball"
    relation: str                   # one of RELATIONS
    landmark: str                   # "green chair"
    near: str | None                # "sofa": a second landmark for context
    seen_by: str                    # which duck saw it with its own eyes
    told_by: str | None = None      # set when this duck only heard about it
    at: float = field(default_factory=time.time)
    confidence: float = 1.0
    photo: str | None = None        # path under the Nest's data directory
    tag_id: str | None = None       # NFC id, when the beak read one
    stale: bool = False             # went to look, it was not there

    def describe(self) -> str:
        text = f"{self.relation} the {self.landmark}"
        if self.near and self.near != self.landmark:
            text += f" near the {self.near}"
        return text


class Memory:
    """One duck's picture of the house."""

    def __init__(self, owner: str, path: str | Path | None = None):
        self.owner = owner
        self.path = Path(path) if path else None
        self.landmarks: dict[str, Landmark] = {}
        self.sightings: list[Sighting] = []
        if self.path and self.path.exists():
            self._load()

    # ── landmarks ──────────────────────────────────────────────────────────────

    def add_landmark(self, name: str, xy: tuple[float, float] | None = None,
                     aliases: list[str] | None = None) -> Landmark:
        name = name.lower().strip()
        mark = self.landmarks.get(name) or Landmark(name=name)
        if xy is not None:
            mark.xy = xy
        for alias in aliases or []:
            if alias.lower() not in mark.aliases:
                mark.aliases.append(alias.lower())
        self.landmarks[name] = mark
        self.save()
        return mark

    def resolve(self, words: str) -> Landmark | None:
        """Find a landmark by name or alias: "grandma's chair" → the green chair."""
        key = words.lower().strip()
        if key in self.landmarks:
            return self.landmarks[key]
        for mark in self.landmarks.values():
            if key in mark.aliases:
                return mark
        return None

    def knows(self, landmark: str) -> bool:
        return self.resolve(landmark) is not None

    # ── sightings ──────────────────────────────────────────────────────────────

    def saw(self, obj: str, relation: str, landmark: str, near: str | None = None,
            confidence: float = 1.0, photo: str | None = None,
            tag_id: str | None = None) -> Sighting:
        if relation not in RELATIONS:
            relation = "near"
        sighting = Sighting(obj=obj.lower(), relation=relation, landmark=landmark.lower(),
                            near=near.lower() if near else None, seen_by=self.owner,
                            confidence=confidence, photo=photo, tag_id=tag_id)
        self._retire(sighting.obj)
        self.sightings.append(sighting)
        self.save()
        return sighting

    def heard(self, sighting: Sighting, from_duck: str) -> Sighting:
        """Second-hand knowledge: less sure, and credited to whoever told us."""
        copy = Sighting(**{**asdict(sighting), "told_by": from_duck,
                           "confidence": round(sighting.confidence * 0.7, 2),
                           "at": time.time()})
        self._retire(copy.obj)
        self.sightings.append(copy)
        self.save()
        return copy

    def gone(self, obj: str) -> None:
        """Went to look and it was not there."""
        for s in self.sightings:
            if s.obj == obj.lower() and not s.stale:
                s.stale = True
        self.save()

    def where_is(self, obj: str) -> Sighting | None:
        """The best live guess: freshest first, first-hand beats hearsay at equal age."""
        live = [s for s in self.sightings if s.obj == obj.lower() and not s.stale]
        if not live:
            return None
        # Seconds of age are the currency: hearsay counts as ten minutes older, and each 0.1 of
        # missing confidence as another minute. Newer news of the ball beats older news.
        return max(live, key=lambda s: s.at - (600 if s.told_by else 0)
                   - 600 * (1.0 - s.confidence))

    def things_seen(self) -> list[str]:
        return sorted({s.obj for s in self.sightings if not s.stale})

    def _retire(self, obj: str) -> None:
        # Keep history bounded: at most 20 sightings per object.
        same = [s for s in self.sightings if s.obj == obj]
        for old in same[:-19]:
            self.sightings.remove(old)

    # ── persistence ────────────────────────────────────────────────────────────

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"owner": self.owner,
                "landmarks": [asdict(m) for m in self.landmarks.values()],
                "sightings": [asdict(s) for s in self.sightings]}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".memory-")
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=1)
        os.replace(tmp, self.path)

    def _load(self) -> None:
        data = json.loads(self.path.read_text())
        for m in data.get("landmarks", []):
            xy = tuple(m["xy"]) if m.get("xy") else None
            self.landmarks[m["name"]] = Landmark(name=m["name"], aliases=m.get("aliases", []),
                                                 xy=xy, room=m.get("room", "living room"))
        self.sightings = [Sighting(**s) for s in data.get("sightings", [])]
