"""Turning a duck's snapshot into sightings, with a small vision model running locally.

Phase 5 groundwork. The model runs on the Jetson (or Olares by day) under Ollama, reached over
the LAN; nothing leaves the house. It is asked for a fixed JSON shape and only the landmark
names the duck already knows, so an answer maps straight onto `Memory.saw()`:

    [{"object": "yellow ball", "relation": "behind", "landmark": "green chair",
      "near": "sofa", "confidence": 0.8}]

Small models are wrong fairly often. Everything they say is a *guess* until a duck confirms it
(a second look, or an NFC read once the beak antenna is exposed), which is why sightings carry a
confidence and why hearsay decays.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from ..world.scene import RELATIONS

logger = logging.getLogger(__name__)

PROMPT = """You are the eyes of a small duck robot at floor level in a home.
List small movable objects you can clearly see (toys, balls, socks, keys, shoes, leaves).
For each, say where it is relative to ONE of these landmarks, if one is visible: {landmarks}.
Use one relation from: {relations}.
Reply with only a JSON array like:
[{{"object": "yellow ball", "relation": "behind", "landmark": "green chair", "near": "sofa",
"confidence": 0.8}}]
Use lowercase. Leave "near" empty if there is no second landmark. Reply [] if you see nothing."""

# Reachy looks down over the lounge from the TV stand: a different view, the same answer shape.
REACHY_PROMPT = """You are the eyes of a small robot sitting on a TV stand, looking over a
living room. List small movable objects you can clearly see on the floor or furniture (toys,
balls, socks, keys, shoes, cushions). Ignore people and large furniture.
For each, say where it is relative to ONE of these landmarks, if one is visible: {landmarks}.
Use one relation from: {relations}.
Reply with only a JSON array like:
[{{"object": "yellow ball", "relation": "under", "landmark": "table", "near": "sofa",
"confidence": 0.8}}]
Use lowercase. Leave "near" empty if there is no second landmark. Reply [] if you see nothing."""


class Captioner:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5vl:3b",
                 timeout: float = 60.0, transport: Any = None):
        import httpx

        self.base = base_url.rstrip("/")
        self.model = model
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._http.aclose()

    async def describe(self, png: bytes, landmarks: list[str],
                       viewer: str = "duck") -> list[dict[str, Any]]:
        prompt = (REACHY_PROMPT if viewer == "reachy" else PROMPT).format(landmarks=", ".join(landmarks) or "none known yet",
                               relations=", ".join(RELATIONS))
        response = await self._http.post(f"{self.base}/api/chat", json={
            "model": self.model, "stream": False, "format": "json",
            "options": {"temperature": 0.1},
            "messages": [{"role": "user", "content": prompt,
                          "images": [base64.b64encode(png).decode()]}],
        })
        response.raise_for_status()
        text = response.json().get("message", {}).get("content", "")
        return self.parse(text, landmarks)

    @staticmethod
    def parse(text: str, landmarks: list[str]) -> list[dict[str, Any]]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.info("captioner said something that is not JSON: %.120s", text)
            return []
        if isinstance(data, dict):  # some models wrap the list: {"objects": [...]}
            data = next((v for v in data.values() if isinstance(v, list)), [])
        known = {name.lower() for name in landmarks}
        out = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            obj = str(item.get("object", "")).strip().lower()
            landmark = str(item.get("landmark", "")).strip().lower()
            if not obj or landmark not in known:
                continue  # a place the duck does not know is not a place it can go back to
            relation = str(item.get("relation", "near")).strip().lower()
            near = str(item.get("near") or "").strip().lower() or None
            try:
                confidence = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            out.append({"object": obj, "relation": relation if relation in RELATIONS else "near",
                        "landmark": landmark, "near": near if near in known else None,
                        "confidence": max(0.0, min(1.0, confidence))})
        return out
