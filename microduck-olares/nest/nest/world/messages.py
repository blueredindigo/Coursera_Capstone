"""What one duck tells another, small enough for a Bluetooth beacon or a burst of ggwave.

Meaning travels as a few bytes; the performance (turning to face the listener, a chirp phrase
shaped like the message) is what you see and hear. The same bytes can ride three carriers:

* the Nest's duck bus on the LAN (today);
* the ~245 spare bytes of the chorale beacon's extended advertising (once reachable);
* ggwave audio, up to ~140 bytes, for the acoustic stretch goal.

Words are sent as indexes into a shared vocabulary both ducks hold, so "yellow ball behind green
chair near sofa" is 9 bytes rather than 40. A word neither has numbered yet goes as text.

Layout (big-endian):

    0      version (1)
    1      kind: 1 found, 2 gone, 3 ask, 4 answer-unknown
    2      sender id (index in the flock: 0 = ah-ah, 1 = tee-tee)
    3      sequence number
    4      confidence, 0..255
    5      relation index (RELATIONS)
    6..    three words: object, landmark, near. Each is either
               0x00-0xEF  vocabulary index
               0xF0 n …   n bytes of UTF-8 text
               0xFF       absent
"""

from __future__ import annotations

from dataclasses import dataclass

from .scene import RELATIONS

VERSION = 1
KINDS = {"found": 1, "gone": 2, "ask": 3, "unknown": 4}
KIND_NAMES = {v: k for k, v in KINDS.items()}
MAX_BLE_BYTES = 245
MAX_GGWAVE_BYTES = 140


class Vocabulary:
    """Numbered words both ducks share. New words are appended, never renumbered."""

    def __init__(self, words: list[str] | None = None):
        self.words: list[str] = []
        for word in words or []:
            self.add(word)

    def add(self, word: str) -> int:
        word = word.lower().strip()
        if word in self.words:
            return self.words.index(word)
        if len(self.words) >= 0xF0:
            raise OverflowError("vocabulary full")
        self.words.append(word)
        return len(self.words) - 1

    def index(self, word: str) -> int | None:
        word = word.lower().strip()
        return self.words.index(word) if word in self.words else None


@dataclass
class DuckMessage:
    kind: str                 # found | gone | ask | unknown
    sender: int
    seq: int
    obj: str
    relation: str = "near"
    landmark: str | None = None
    near: str | None = None
    confidence: float = 1.0

    def encode(self, vocab: Vocabulary) -> bytes:
        out = bytearray([VERSION, KINDS[self.kind], self.sender & 0xFF, self.seq & 0xFF,
                         max(0, min(255, round(self.confidence * 255))),
                         RELATIONS.index(self.relation) if self.relation in RELATIONS else 8])
        for word in (self.obj, self.landmark, self.near):
            if not word:
                out.append(0xFF)
                continue
            index = vocab.index(word)
            if index is not None:
                out.append(index)
            else:
                text = word.encode("utf-8")[:60]
                out += bytes([0xF0, len(text)]) + text
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes, vocab: Vocabulary) -> "DuckMessage":
        if len(data) < 7 or data[0] != VERSION:
            raise ValueError("not a duck message this Nest understands")
        words: list[str | None] = []
        i = 6
        while len(words) < 3:
            marker = data[i]
            if marker == 0xFF:
                words.append(None)
                i += 1
            elif marker == 0xF0:
                n = data[i + 1]
                words.append(data[i + 2:i + 2 + n].decode("utf-8"))
                i += 2 + n
            else:
                words.append(vocab.words[marker])
                i += 1
        relation = RELATIONS[data[5]] if data[5] < len(RELATIONS) else "near"
        return cls(kind=KIND_NAMES[data[1]], sender=data[2], seq=data[3], obj=words[0] or "",
                   relation=relation, landmark=words[1], near=words[2],
                   confidence=data[4] / 255)

    def subtitle(self) -> str:
        """What the Pond shows under the chirps."""
        where = ""
        if self.landmark:
            where = f"{self.relation} the {self.landmark}"
            if self.near and self.near != self.landmark:
                where += f" near the {self.near}"
        if self.kind == "found":
            return f"I found a {self.obj}! It's {where}." if where else f"I found a {self.obj}!"
        if self.kind == "gone":
            return f"The {self.obj} isn't {where} any more." if where else \
                f"The {self.obj} is gone."
        if self.kind == "ask":
            return f"Have you seen the {self.obj}?"
        return f"I don't know where the {self.obj} is."

    def chirp_phrase(self) -> list[str]:
        """Voice tags that perform the message: its length and shape, not its words."""
        opener = {"found": ["greet"], "gone": ["peck"], "ask": ["inquire"],
                  "unknown": ["peck"]}[self.kind]
        body = ["chirp"] * (1 + (self.landmark is not None) + (self.near is not None))
        closer = ["wheee"] if self.kind == "found" and self.confidence > 0.8 else []
        return opener + body + closer
