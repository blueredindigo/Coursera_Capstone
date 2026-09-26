"""Quiet hours and routines that survive devices being switched off.

The house has a rhythm: from 8 pm a toddler is getting ready for sleep, so Reachy and both ducks
are often simply switched off, and they come back some time after 9 am. None of that is a fault,
and the Nest must treat it as normal:

* **Quiet hours** (default 20:00–09:00, the Jetson's local time). While quiet, the Nest sends no
  motion and no sound to any robot, even one that is switched on early; the Pond's actions say
  "quiet hours" instead of doing anything. Tapping **Good morning** is you deciding otherwise,
  and lifts quiet hours until the next bedtime.
* **Routines** are the Nest's repeating jobs (bedtime, morning, battery samples, the Reachy
  watchdog…). Each one declares which devices it needs. A routine whose device is off does not
  fail: it waits, retries every minute, and runs when the device comes back, as long as it is
  still within its catch-up window; after that it is recorded as skipped, with the reason. A
  routine that raises is recorded as failed and retried with backoff; it never takes the loop
  down. Every routine's last result is shown in the Pond.

Nothing here keeps state that a restart would lose: whether it is quiet is worked out from the
clock, so a Jetson that reboots at 11 pm comes back up quiet.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def parse_hhmm(text: str) -> dtime:
    hours, minutes = (int(part) for part in text.strip().split(":"))
    return dtime(hours, minutes)


@dataclass
class QuietHours:
    start: dtime = dtime(20, 0)
    end: dtime = dtime(9, 0)
    enabled: bool = True
    override_until: datetime | None = None  # "Good morning" tapped during quiet hours

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "QuietHours":
        return cls(start=parse_hhmm(raw.get("start", "20:00")),
                   end=parse_hhmm(raw.get("end", "09:00")),
                   enabled=bool(raw.get("enabled", True)))

    def in_window(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        t = now.time()
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end  # spans midnight

    def is_quiet(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self.override_until and now < self.override_until:
            return False
        return self.in_window(now)

    def next_start(self, now: datetime) -> datetime:
        candidate = now.replace(hour=self.start.hour, minute=self.start.minute, second=0,
                                microsecond=0)
        return candidate if candidate > now else candidate + timedelta(days=1)

    def next_end(self, now: datetime) -> datetime:
        candidate = now.replace(hour=self.end.hour, minute=self.end.minute, second=0,
                                microsecond=0)
        return candidate if candidate > now else candidate + timedelta(days=1)

    def lift_until_bedtime(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self.override_until = self.next_start(now)

    def describe(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        if not self.enabled:
            return "quiet hours off"
        if self.is_quiet(now):
            return f"quiet hours until {self.end.strftime('%H:%M')}"
        if self.override_until and now < self.override_until and self.in_window(now):
            return "quiet hours lifted by you until bedtime"
        return f"quiet hours from {self.start.strftime('%H:%M')}"


# ── routines ─────────────────────────────────────────────────────────────────────

Available = Callable[[str], bool]


@dataclass
class Routine:
    name: str
    run: Callable[[], Awaitable[Any]]
    next_due: Callable[[datetime], datetime]   # given "now", when is it next due?
    needs: tuple[str, ...] = ()                # device names that must be on
    catch_up: timedelta = timedelta(hours=3)   # how late it may still run after a miss
    retry: timedelta = timedelta(minutes=1)    # how often to look for its devices again
    # state, shown in the Pond
    due: datetime | None = None
    last_run: datetime | None = None
    last_result: str = "not run yet"
    failures: int = 0
    _retry_at: datetime | None = field(default=None, repr=False)


def daily_at(hhmm: dtime) -> Callable[[datetime], datetime]:
    def next_due(now: datetime) -> datetime:
        candidate = now.replace(hour=hhmm.hour, minute=hhmm.minute, second=0, microsecond=0)
        return candidate if candidate > now else candidate + timedelta(days=1)
    return next_due


def every(period: timedelta) -> Callable[[datetime], datetime]:
    return lambda now: now + period


class Routines:
    """Runs routines on time, waits for their devices, and never lets one failure spread."""

    def __init__(self, available: Available, clock: Callable[[], datetime] = datetime.now):
        self.available = available
        self.clock = clock
        self.routines: list[Routine] = []

    def add(self, routine: Routine, run_first_at: datetime | None = None) -> Routine:
        now = self.clock()
        routine.due = run_first_at or routine.next_due(now)
        self.routines.append(routine)
        return routine

    async def step(self) -> None:
        """Run whatever is due. Call it every few seconds."""
        now = self.clock()
        for routine in self.routines:
            if routine.due is None or now < routine.due:
                continue
            if routine._retry_at and now < routine._retry_at:
                continue
            missing = [d for d in routine.needs if not self.available(d)]
            if missing:
                if now - routine.due > routine.catch_up:
                    routine.last_result = f"skipped: {', '.join(missing)} stayed off"
                    self._reschedule(routine, now)
                else:
                    routine.last_result = f"waiting for {', '.join(missing)}"
                    routine._retry_at = now + routine.retry
                continue
            try:
                await routine.run()
            except Exception as exc:  # a routine must never take the loop down
                routine.failures += 1
                backoff = min(timedelta(minutes=30), routine.retry * 2 ** min(routine.failures, 5))
                routine.last_result = f"failed ({exc}); retrying"
                routine._retry_at = now + backoff
                logger.warning("routine %s failed: %s", routine.name, exc)
                if now - routine.due > routine.catch_up:
                    self._reschedule(routine, now)
                continue
            routine.failures = 0
            routine.last_run = now
            routine.last_result = "ok"
            self._reschedule(routine, now)

    def _reschedule(self, routine: Routine, now: datetime) -> None:
        routine.due = routine.next_due(now)
        routine._retry_at = None

    async def run_forever(self, period_s: float = 5.0) -> None:
        while True:
            try:
                await self.step()
            except Exception:
                logger.exception("routines step failed")
            await asyncio.sleep(period_s)

    def status(self) -> list[dict[str, Any]]:
        return [{"name": r.name, "last_result": r.last_result,
                 "last_run": r.last_run.isoformat(timespec="minutes") if r.last_run else None,
                 "next": r.due.isoformat(timespec="minutes") if r.due else None,
                 "needs": list(r.needs)} for r in self.routines]

