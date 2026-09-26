"""Reachy Mini Lite, plugged into the Jetson by USB: the room's lighthouse.

The Lite has no computer of its own. Its daemon runs on the Jetson (`reachy-mini-daemon`, from
the `reachy_mini` Python SDK; the desktop app may not run on ARM64, per Pollen's docs) and serves
a REST API on `http://localhost:8000/api`. This module speaks that API directly, so the Nest does
not need the SDK's GStreamer stack just to move a head:

* `POST /api/move/goto` — `{head_pose:{x,y,z,roll,pitch,yaw}, antennas:[l,r], body_yaw, duration}`,
  metres and radians;
* `POST /api/move/play/wake_up`, `POST /api/move/play/goto_sleep`;
* `GET /api/state/doa` — `{angle, speech_detected}`, angle 0 = left, π/2 = front, π = right.

The camera is the exception: frames come from the SDK's LOCAL media backend (the daemon owns the
camera and shares frames over IPC on the same machine). See `ReachyCamera`.

Reachy is a calm, older presence in this house, not a third duck. Its expressions are small.
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Conservative ranges: large turns go to the body, small ones to the head.
HEAD_YAW_LIMIT = 0.6      # rad
HEAD_PITCH_LIMIT = 0.35   # rad
BODY_YAW_LIMIT = 2.6      # rad

# Antenna poses, radians (left, right). Positive raises, by Reachy's convention for these demos;
# check on your robot and flip ANTENNA_SIGN if "perk" looks like "droop".
ANTENNA_SIGN = 1.0
ANTENNAS = {
    "neutral": (0.0, 0.0),
    "perk": (0.5, 0.5),
    "droop": (-0.9, -0.9),
    "curious": (0.5, -0.2),
    "tired_look": (-0.5, -0.5),
}


def clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


class ReachyClient:
    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 5.0):
        import httpx

        self.base = base_url.rstrip("/") + "/api"
        self._http = httpx.AsyncClient(timeout=timeout)
        self.head_yaw = 0.0
        self.body_yaw = 0.0

    async def close(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        response = await self._http.post(self.base + path, json=body)
        response.raise_for_status()
        return response.json() if response.content else None

    async def _get(self, path: str) -> Any:
        response = await self._http.get(self.base + path)
        response.raise_for_status()
        return response.json() if response.content else None

    async def ping(self) -> bool:
        """Is the daemon answering? Reachy being switched off is normal, not an error."""
        try:
            await self._get("/daemon/status")
            return True
        except Exception:
            return False

    async def wake_up(self) -> None:
        await self._post("/move/play/wake_up")

    async def goto_sleep(self) -> None:
        await self._post("/move/play/goto_sleep")

    async def goto(self, head: dict[str, float] | None = None,
                   antennas: tuple[float, float] | None = None,
                   body_yaw: float | None = None, duration: float = 1.0) -> None:
        body: dict[str, Any] = {"duration": max(0.5, duration), "interpolation": "minjerk"}
        if head is not None:
            body["head_pose"] = {k: float(head.get(k, 0.0))
                                 for k in ("x", "y", "z", "roll", "pitch", "yaw")}
        if antennas is not None:
            body["antennas"] = [ANTENNA_SIGN * antennas[0], ANTENNA_SIGN * antennas[1]]
        if body_yaw is not None:
            body["body_yaw"] = float(body_yaw)
        await self._post("/move/goto", body)

    async def doa(self) -> dict[str, Any] | None:
        return await self._get("/state/doa")

    async def state(self) -> dict[str, Any]:
        return await self._get("/state/full")


class ReachyGaze:
    """Turns a bearing into a head-and-body move, and names expressions.

    Works with ReachyClient or nest.sim.SimReachy: both have `goto()`.
    """

    def __init__(self, reachy: Any):
        self.reachy = reachy
        self.body_yaw = 0.0

    async def look_at_bearing(self, yaw: float, pitch: float = -0.15,
                              antennas: str | None = None, duration: float = 1.0) -> None:
        """Face a direction in the room. Yaw in radians, 0 = straight out from the TV stand,
        positive = Reachy's left. Big turns rotate the body; the head does the last bit."""
        yaw = clamp(yaw, BODY_YAW_LIMIT + HEAD_YAW_LIMIT)
        head_yaw = clamp(yaw - self.body_yaw, HEAD_YAW_LIMIT)
        body_yaw = self.body_yaw
        if abs(yaw - self.body_yaw) > HEAD_YAW_LIMIT:
            body_yaw = clamp(yaw - math.copysign(HEAD_YAW_LIMIT * 0.5, yaw - self.body_yaw),
                             BODY_YAW_LIMIT)
            head_yaw = clamp(yaw - body_yaw, HEAD_YAW_LIMIT)
        self.body_yaw = body_yaw
        await self.reachy.goto(head={"pitch": clamp(pitch, HEAD_PITCH_LIMIT), "yaw": head_yaw},
                               antennas=ANTENNAS[antennas] if antennas else None,
                               body_yaw=body_yaw, duration=duration)

    async def express(self, name: str, duration: float = 0.8) -> None:
        await self.reachy.goto(antennas=ANTENNAS[name], duration=duration)

    async def wiggle(self, times: int = 2) -> None:
        for _ in range(times):
            await self.reachy.goto(antennas=(0.4, -0.4), duration=0.5)
            await self.reachy.goto(antennas=(-0.4, 0.4), duration=0.5)
        await self.reachy.goto(antennas=ANTENNAS["neutral"], duration=0.5)


class ReachyCamera:
    """Frames from Reachy Lite's 120° camera, through the SDK's LOCAL media backend.

    Optional: needs `pip install reachy-mini` and GStreamer on the Jetson. Returns BGR numpy
    arrays, or None when no frame is ready.
    """

    def __init__(self):
        from reachy_mini import ReachyMini  # imported lazily; heavy

        self._mini = ReachyMini(media_backend="local", connection_mode="auto")

    def frame(self):
        return self._mini.media.get_frame()

    def close(self) -> None:
        try:
            self._mini.__exit__(None, None, None)
        except Exception:
            pass
