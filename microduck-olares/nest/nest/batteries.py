"""Pit stops and the battery health diary.

Four packs (A–D) for two ducks. Each swap is logged (a phone tap on the pack's NFC sticker
through Home Assistant, or a button in the Pond), and from then on the Nest knows which pack is
in which duck. It samples that duck's battery percentage and learns each pack's drain rate, so
"Pack C is getting tired" arrives as a trend, not a surprise.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class PackRun:
    pack: str
    duck: str
    started: float
    samples: list[tuple[float, float]] = field(default_factory=list)  # (time, percent)

    def drain_per_hour(self) -> float | None:
        """Percent per hour while in use, from the first and last sample."""
        if len(self.samples) < 2:
            return None
        (t0, p0), (t1, p1) = self.samples[0], self.samples[-1]
        hours = (t1 - t0) / 3600
        if hours < 0.1 or p1 >= p0:
            return None
        return (p0 - p1) / hours


class BatteryDiary:
    def __init__(self, path: str | Path | None = None, packs: tuple[str, ...] = ("A", "B", "C", "D")):
        self.path = Path(path) if path else None
        self.packs = packs
        self.fitted: dict[str, str] = {}       # duck -> pack
        self.runs: list[PackRun] = []
        self.pit_stops: list[dict[str, object]] = []
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self.fitted = data.get("fitted", {})
            self.runs = [PackRun(r["pack"], r["duck"], r["started"],
                                 [tuple(s) for s in r["samples"]]) for r in data.get("runs", [])]
            self.pit_stops = data.get("pit_stops", [])

    def pit_stop(self, duck: str, pack: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        pack = pack.upper()
        if pack not in self.packs:
            raise ValueError(f"unknown pack {pack!r}; packs are {self.packs}")
        for other, fitted in list(self.fitted.items()):
            if fitted == pack and other != duck:
                del self.fitted[other]  # a pack can only be in one duck
        self.fitted[duck] = pack
        self.runs.append(PackRun(pack, duck, now))
        self.pit_stops.append({"duck": duck, "pack": pack, "at": now})
        self.save()

    def sample(self, duck: str, percent: float | None, now: float | None = None) -> None:
        """Called every few minutes with the duck's battery reading."""
        if percent is None or duck not in self.fitted:
            return
        now = time.time() if now is None else now
        run = next((r for r in reversed(self.runs) if r.duck == duck), None)
        if run is None or run.pack != self.fitted[duck]:
            return
        if run.samples and now - run.samples[-1][0] < 240:
            return
        run.samples.append((now, percent))
        run.samples = run.samples[-200:]
        self.save()

    def health(self) -> dict[str, dict[str, object]]:
        """Per pack: runs, mean drain, and whether it is draining faster than the others."""
        report: dict[str, dict[str, object]] = {}
        rates: dict[str, list[float]] = {p: [] for p in self.packs}
        for run in self.runs:
            rate = run.drain_per_hour()
            if rate is not None:
                rates[run.pack].append(rate)
        means = {p: sum(v) / len(v) for p, v in rates.items() if v}
        fleet = sum(means.values()) / len(means) if means else None
        for pack in self.packs:
            mean = means.get(pack)
            report[pack] = {
                "in": next((d for d, p in self.fitted.items() if p == pack), None),
                "runs": sum(1 for r in self.runs if r.pack == pack),
                "drain_pct_per_hour": round(mean, 1) if mean else None,
                "getting_tired": bool(mean and fleet and mean > fleet * 1.2 and len(means) > 1),
            }
        return report

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".batteries-")
        with os.fdopen(fd, "w") as handle:
            json.dump({"fitted": self.fitted, "runs": [asdict(r) for r in self.runs],
                       "pit_stops": self.pit_stops[-500:]}, handle, indent=1)
        os.replace(tmp, self.path)
