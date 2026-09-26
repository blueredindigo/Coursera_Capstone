"""The Duckdex: every thing a duck has discovered, and the rule that curiosity is food.

Something never seen before is a **meal** (a happy `wheee`, a little dance, a new entry).
Something seen before is a **snack**. The same thing over and over is **boring**. Things from
field trips go in the **Wild** section, which only going outside can fill. An entry learned from
the other duck and then confirmed is credited to whoever told.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Entry:
    label: str
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    times_seen: int = 1
    wild: bool = False
    credited_to: str | None = None   # "told by Ah-Ah"
    photo: str | None = None
    tag_id: str | None = None
    recent: list[float] = field(default_factory=list)


class Duckdex:
    BORED_AFTER = 4          # this many looks ...
    BORED_WINDOW_S = 600.0   # ... within ten minutes is boring

    def __init__(self, owner: str, path: str | Path | None = None):
        self.owner = owner
        self.path = Path(path) if path else None
        self.entries: dict[str, Entry] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self.entries = {e["label"]: Entry(**e) for e in data.get("entries", [])}

    def feed(self, label: str, wild: bool = False, photo: str | None = None,
             credited_to: str | None = None, tag_id: str | None = None,
             now: float | None = None) -> str:
        """Record a look at something. Returns 'meal', 'snack' or 'boring'."""
        now = time.time() if now is None else now
        label = label.lower().strip()
        entry = self.entries.get(label)
        if entry is None:
            self.entries[label] = Entry(label=label, first_seen=now, last_seen=now, wild=wild,
                                        credited_to=credited_to, photo=photo, tag_id=tag_id,
                                        recent=[now])
            self.save()
            return "meal"
        entry.times_seen += 1
        entry.last_seen = now
        entry.recent = [t for t in entry.recent if now - t <= self.BORED_WINDOW_S] + [now]
        if tag_id and not entry.tag_id:
            entry.tag_id = tag_id
        self.save()
        return "boring" if len(entry.recent) >= self.BORED_AFTER else "snack"

    def wild(self) -> list[Entry]:
        return [e for e in self.entries.values() if e.wild]

    def __len__(self) -> int:
        return len(self.entries)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".duckdex-")
        with os.fdopen(fd, "w") as handle:
            json.dump({"owner": self.owner,
                       "entries": [asdict(e) for e in self.entries.values()]}, handle, indent=1)
        os.replace(tmp, self.path)
