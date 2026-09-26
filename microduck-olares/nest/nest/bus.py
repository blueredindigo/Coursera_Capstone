"""The duck bus: messages between Ah-Ah and Tee-Tee, delivered only within earshot.

A message is delivered only if the two ducks could plausibly hear each other. If Tee-Tee is in
another room it finds out when they meet, so gossip spreads physically, the way it would between
real animals. Until then the message waits in the speaker's pocket (and goes stale after a day).

**Earshot evidence**, any one of which is enough:

1. **Reachy's map** puts both ducks within `max_m` of each other;
2. **either duck's own detector** has just seen a duck: with two ducks in the house, a duck that
   Ah-Ah sees is Tee-Tee;
3. a Bluetooth RSSI reading above a threshold, when one is available. The WebRTC control
   channel refuses `chorale.*`, so this is not reachable from the Nest today; the hook is here
   for when it is (or for a Jetson-side BLE scan).

Delivery is performed: the speaker turns to face the listener, plays the chirp phrase, and the
listener answers. The Pond shows the subtitle.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .world.messages import DuckMessage, Vocabulary

logger = logging.getLogger(__name__)

Perform = Callable[[str, str, DuckMessage], Awaitable[None]]


@dataclass
class Earshot:
    max_m: float = 1.5
    detector_window_s: float = 3.0
    rssi_dbm: float = -65.0


@dataclass
class Pending:
    speaker: str
    listener: str
    message: DuckMessage
    wire: bytes
    queued_at: float = field(default_factory=time.time)


@dataclass
class Delivery:
    speaker: str
    listener: str
    message: DuckMessage
    at: float
    evidence: str
    size_bytes: int


class DuckBus:
    def __init__(self, vocab: Vocabulary, earshot: Earshot | None = None,
                 perform: Perform | None = None, on_delivered: Callable[[Delivery], None] | None
                 = None, expiry_s: float = 86400.0):
        self.vocab = vocab
        self.earshot = earshot or Earshot()
        self.perform = perform
        self.on_delivered = on_delivered
        self.expiry_s = expiry_s
        self.pending: list[Pending] = []
        self.log: list[Delivery] = []
        # Evidence sources, set by the app: name -> callables.
        self.distance: Callable[[str, str], float | None] = lambda a, b: None
        self.sees_a_duck: Callable[[str], bool] = lambda name: False
        self.rssi: Callable[[str, str], float | None] = lambda a, b: None
        self._seq: dict[str, int] = {}

    def next_seq(self, speaker: str) -> int:
        self._seq[speaker] = (self._seq.get(speaker, 0) + 1) % 256
        return self._seq[speaker]

    def in_earshot(self, a: str, b: str) -> str | None:
        """Why these two could hear each other right now, or None if they could not."""
        distance = self.distance(a, b)
        if distance is not None and distance <= self.earshot.max_m:
            return f"{distance:.1f} m apart in Reachy's view"
        if self.sees_a_duck(a) or self.sees_a_duck(b):
            return "one can see the other"
        rssi = self.rssi(a, b)
        if rssi is not None and rssi >= self.earshot.rssi_dbm:
            return f"radio says close ({rssi:.0f} dBm)"
        return None

    def say(self, speaker: str, listener: str, message: DuckMessage) -> Pending:
        wire = message.encode(self.vocab)
        item = Pending(speaker, listener, message, wire)
        self.pending.append(item)
        return item

    async def pump(self) -> list[Delivery]:
        """Deliver whatever can be delivered now. Call it every spine tick."""
        now = time.time()
        self.pending = [p for p in self.pending if now - p.queued_at <= self.expiry_s]
        delivered: list[Delivery] = []
        for item in list(self.pending):
            evidence = self.in_earshot(item.speaker, item.listener)
            if evidence is None:
                continue
            self.pending.remove(item)
            # What arrives is what the wire carried, decoded: the same bytes a beacon or ggwave
            # would carry, so the round trip is exercised on every message.
            received = DuckMessage.decode(item.wire, self.vocab)
            if self.perform:
                try:
                    await self.perform(item.speaker, item.listener, received)
                except Exception:
                    logger.exception("performing a message failed; delivered anyway")
            delivery = Delivery(item.speaker, item.listener, received, now, evidence,
                                len(item.wire))
            self.log.append(delivery)
            self.log = self.log[-200:]
            delivered.append(delivery)
            if self.on_delivered:
                self.on_delivered(delivery)
        return delivered

    def recent(self, n: int = 20) -> list[dict[str, Any]]:
        return [{"speaker": d.speaker, "listener": d.listener, "subtitle": d.message.subtitle(),
                 "chirps": d.message.chirp_phrase(), "evidence": d.evidence, "bytes": d.size_bytes,
                 "at": d.at} for d in self.log[-n:]]
