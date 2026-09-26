"""How JSON-RPC lines reach a duck on the LAN.

**WebRtcTransport is the real one.** It does exactly what the robot's own console page does
(`mediad/webclient/index.html`): open the robot's local signalling server on `:8443`, ask for the
producer, start a session, answer the robot's offer, and receive the `control` datachannel the
robot creates. JSON-RPC lines then travel on that channel. Nothing leaves the LAN: no
rendezvous, no relay, no STUN.

Signalling messages, as the console reads them off `net/webrtc/protocol`:

    ← {"type":"welcome","peerId":…}
    → {"type":"list"}
    ← {"type":"list","producers":[{"id":…,"meta":{…}}]}
    → {"type":"startSession","peerId":<producer id>}
    ← {"type":"sessionStarted","sessionId":…}
    ← {"type":"peer","sessionId":…,"sdp":{"type":"offer","sdp":…}}
    → {"type":"peer","sessionId":…,"sdp":{"type":"answer","sdp":…}}
    ← {"type":"peer","sessionId":…,"ice":{"candidate":…,"sdpMLineIndex":…}}
    ← {"type":"endSession",…}

What a WebRTC peer may call is `mediad/src/route.rs`: move, head, look, pose, mouth, do, sound,
enable, init, relax, stop, subscribe, health, `tof.stream` and more. **Not** the `chorale.*`
namespace, so Bluetooth presence between ducks is not visible from here.

**UnixSocketTransport is for development.** Forward a duck's `robotd` socket over ssh
(`ssh -N -L /tmp/ah-ah.sock:/run/robotd.sock microduck@ah-ah.local`) and point this at it. It
reaches `robotd` only: no `media.detections`, no `tof.stream`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from .rpc import Rpc

logger = logging.getLogger(__name__)


class Transport:
    """Keeps an Rpc bound to a live channel, reconnecting when the channel drops."""

    def __init__(self, name: str):
        self.name = name
        self.rpc: Rpc | None = None
        self.connected = asyncio.Event()
        self.status = "idle"
        self._stopping = False
        self.max_backoff = 30.0
        self._last_error: str | None = None

    def attach(self, rpc: Rpc) -> None:
        self.rpc = rpc

    async def run(self) -> None:
        """Connect, stay connected, reconnect with backoff. Returns only when stopped."""
        backoff = 1.0
        while not self._stopping:
            try:
                self.status = "connecting"
                await self._session()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the network is allowed to fail; the Nest is not
                self.status = f"down: {exc}"
                # Once per change, not once per retry: a duck off for the night would otherwise
                # write a warning every 30 s until morning.
                was_up = self.connected.is_set()  # a drop after a good session always warns
                (logger.warning if was_up or self.status != self._last_error else logger.debug)(
                    "%s: %s", self.name, exc)
                self._last_error = self.status
            finally:
                self.connected.clear()
                if self.rpc:
                    self.rpc.unbind("channel closed")
            if self._stopping:
                break
            await asyncio.sleep(backoff)
            # A duck switched off for the night is retried at most every 30 s, forever: when it
            # is switched back on, it is picked up within half a minute.
            backoff = min(backoff * 2, self.max_backoff)

    async def stop(self) -> None:
        self._stopping = True

    async def _session(self) -> None:
        raise NotImplementedError


class WebRtcTransport(Transport):
    """The duck's `control` datachannel, negotiated through its own LAN signalling server."""

    def __init__(self, name: str, host: str, signalling_port: int = 8443,
                 keep_video: bool = True):
        super().__init__(name)
        self.host = host
        self.url = f"ws://{host}:{signalling_port}"
        self.keep_video = keep_video
        self.latest_video_frame: Any = None  # an av.VideoFrame, when keep_video
        self.producer_meta: dict[str, Any] = {}

    async def _session(self) -> None:
        # Imported here so the rest of the Nest (and its tests) never needs a WebRTC stack.
        import websockets
        from aiortc import RTCPeerConnection, RTCSessionDescription
        from aiortc.sdp import candidate_from_sdp

        pc: RTCPeerConnection | None = None
        session_id: str | None = None
        closed = asyncio.Event()
        consumers: list[asyncio.Task] = []

        async with websockets.connect(self.url, open_timeout=10, ping_interval=20) as ws:
            self.status = "signalling"

            async def send(message: dict[str, Any]) -> None:
                await ws.send(json.dumps(message))

            def new_peer_connection() -> RTCPeerConnection:
                # No ICE servers: this is a LAN session, host candidates are all it needs.
                peer = RTCPeerConnection()

                @peer.on("datachannel")
                def on_datachannel(channel):  # the robot creates `control`; we receive it
                    logger.info("%s: datachannel %s", self.name, channel.label)
                    if channel.label != "control":
                        return

                    async def send_line(line: str) -> None:
                        channel.send(line)

                    def bind() -> None:
                        if self.rpc:
                            self.rpc.bind(send_line)
                        self.status = "connected"
                        self.connected.set()

                    @channel.on("message")
                    def on_message(data):
                        if self.rpc:
                            self.rpc.feed(data)

                    @channel.on("close")
                    def on_close():
                        closed.set()

                    if channel.readyState == "open":
                        bind()
                    else:
                        channel.on("open", bind)

                @peer.on("track")
                def on_track(track):
                    # aiortc decodes every received frame into an unbounded queue, so a track
                    # nobody reads is a memory leak. Read it, keep only the newest frame.
                    consumers.append(asyncio.ensure_future(self._drain(track)))

                @peer.on("connectionstatechange")
                async def on_state():
                    if peer.connectionState in ("failed", "closed"):
                        closed.set()

                return peer

            async def signalling() -> None:
                nonlocal pc, session_id
                async for raw in ws:
                    message = json.loads(raw)
                    kind = message.get("type")
                    if kind == "welcome":
                        await send({"type": "list"})
                    elif kind == "list":
                        producers = message.get("producers") or []
                        if not producers:
                            raise ConnectionError(
                                "no producer: mediad registers once its pipeline is PLAYING")
                        if session_id is None:
                            producer = producers[0]
                            self.producer_meta = producer.get("meta") or {}
                            await send({"type": "startSession", "peerId": producer["id"]})
                    elif kind == "sessionStarted":
                        session_id = message["sessionId"]
                        pc = new_peer_connection()
                        self.status = "negotiating"
                    elif kind == "peer" and pc is not None:
                        if message.get("sdp"):
                            offer = message["sdp"]
                            await pc.setRemoteDescription(
                                RTCSessionDescription(sdp=offer["sdp"], type=offer["type"]))
                            if not self.keep_video:
                                for transceiver in pc.getTransceivers():
                                    if transceiver.kind == "video":
                                        transceiver.direction = "inactive"
                            answer = await pc.createAnswer()
                            # aiortc gathers every candidate before this returns, so the answer
                            # carries them all and nothing needs trickling from this side.
                            await pc.setLocalDescription(answer)
                            await send({"type": "peer", "sessionId": session_id,
                                        "sdp": {"type": "answer",
                                                "sdp": pc.localDescription.sdp}})
                        elif message.get("ice"):
                            ice = message["ice"]
                            text = (ice.get("candidate") or "").strip()
                            if not text:
                                continue  # end of candidates
                            if text.startswith("candidate:"):
                                text = text[len("candidate:"):]
                            candidate = candidate_from_sdp(text)
                            candidate.sdpMLineIndex = ice.get("sdpMLineIndex")
                            await pc.addIceCandidate(candidate)
                    elif kind == "endSession":
                        raise ConnectionError(f"session ended by robot: {message.get('reason')}")
                    elif kind == "error":
                        raise ConnectionError(f"signalling error: {message.get('details')}")

            signalling_task = asyncio.ensure_future(signalling())
            closed_task = asyncio.ensure_future(closed.wait())
            try:
                done, _ = await asyncio.wait({signalling_task, closed_task},
                                             return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task is signalling_task and task.exception():
                        raise task.exception()  # type: ignore[misc]
            finally:
                for task in (signalling_task, closed_task, *consumers):
                    task.cancel()
                if pc is not None:
                    await pc.close()
                if session_id is not None:
                    try:
                        await send({"type": "endSession", "sessionId": session_id})
                    except Exception:
                        pass

    async def _drain(self, track) -> None:
        from aiortc.mediastreams import MediaStreamError

        try:
            while True:
                frame = await track.recv()
                if track.kind == "video":
                    self.latest_video_frame = frame
        except (MediaStreamError, asyncio.CancelledError):
            return


class UnixSocketTransport(Transport):
    """NDJSON over a unix socket: `robotd`'s own socket, forwarded over ssh for development."""

    def __init__(self, name: str, path: str):
        super().__init__(name)
        self.path = path

    async def _session(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.path)

        async def send_line(line: str) -> None:
            writer.write(line.encode() + b"\n")
            await writer.drain()

        if self.rpc:
            self.rpc.bind(send_line)
        self.status = "connected"
        self.connected.set()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("socket closed")
                if self.rpc:
                    self.rpc.feed(line)
        finally:
            writer.close()


class LoopbackTransport(Transport):
    """An in-process channel to a simulated duck (`nest.sim`). Used by `--sim` and the tests."""

    def __init__(self, name: str, handler: Callable[[str, Callable[[str], None]], None],
                 powered: Callable[[], bool] = lambda: True):
        super().__init__(name)
        self._handler = handler
        self._powered = powered  # the simulated power switch
        self._stop = asyncio.Event()

    async def _session(self) -> None:
        if not self._powered():
            raise ConnectionError("switched off")
        loop = asyncio.get_running_loop()

        def deliver(line: str) -> None:
            if self.rpc:
                loop.call_soon(self.rpc.feed, line)

        async def send_line(line: str) -> None:
            self._handler(line, deliver)

        if self.rpc:
            self.rpc.bind(send_line)
        self.status = "connected"
        self.connected.set()
        while not self._stop.is_set():
            if not self._powered():
                raise ConnectionError("switched off")
            await asyncio.sleep(0.1)

    async def stop(self) -> None:
        await super().stop()
        self._stop.set()
