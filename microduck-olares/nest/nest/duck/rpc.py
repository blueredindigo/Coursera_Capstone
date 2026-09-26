"""JSON-RPC 2.0, one object per line, over whatever carries it.

The duck's wire (`duck-ipc-proto`) is the same everywhere: `robotctl` sends these lines over a
unix socket, the robot's console page sends them over the WebRTC `control` datachannel, and this
module sends them over whichever transport it is bound to. Ids are handed out here and answers
matched to them; a line without an id is a notification (`robot.state`, `tof.frame`,
`media.detections`) and goes to the listeners registered for its method.

Modelled on `spaces/shared/control.py` in the microduck repo, but asyncio-native, because the
Nest runs one event loop for two ducks and a Reachy.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

Send = Callable[[str], Awaitable[None]]
Listener = Callable[[dict[str, Any]], None]


class RpcError(Exception):
    """A refusal from the robot, carrying the JSON-RPC error it refused with.

    Distinct from a transport failure on purpose: a robot answering "no such method" is the
    robot working correctly, while no answer at all means something else.
    """

    def __init__(self, method: str, error: dict[str, Any]):
        self.method = method
        self.code = error.get("code")
        self.message = error.get("message") or "refused, with nothing said about why"
        super().__init__(f"{method}: {self.message}")


class RpcTimeout(Exception):
    """No answer arrived in time. The transport may be down, or the call may be slow."""


class RpcClosed(Exception):
    """The transport closed while the call was waiting."""


class Rpc:
    """Requests out, answers and notifications in, over one line-oriented channel.

    Not a connection: the transport owns that. This owns the id space, the pending table and
    the notification listeners, and it survives a transport closing: pending calls are failed
    at once rather than left to time out one by one.
    """

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[str, asyncio.Future[Any]]] = {}
        self._send: Send | None = None
        self._listeners: dict[str, list[Listener]] = {}
        # The last notification of each method. `robot.state` streams at whatever rate was asked
        # for; keeping them all would be a leak, and the last one is what a behaviour reads.
        self.latest: dict[str, dict[str, Any]] = {}

    # ── binding to a transport ──────────────────────────────────────────────────

    def bind(self, send: Send) -> None:
        self._send = send

    def unbind(self, reason: str = "transport closed") -> None:
        self._send = None
        pending, self._pending = self._pending, {}
        for method, future in pending.values():
            if not future.done():
                future.set_exception(RpcClosed(f"{method}: {reason}"))

    @property
    def is_open(self) -> bool:
        return self._send is not None

    # ── outbound ────────────────────────────────────────────────────────────────

    async def call(self, method: str, params: dict[str, Any] | None = None,
                   timeout: float | None = None) -> Any:
        """Send a request and wait for its answer. Raises RpcError on a refusal."""
        if self._send is None:
            raise RpcClosed(f"{method}: not connected")
        request_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        line = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            line["params"] = params
        try:
            await self._send(json.dumps(line, separators=(",", ":")))
            return await asyncio.wait_for(future, timeout or self.timeout)
        except asyncio.TimeoutError as exc:
            raise RpcTimeout(f"{method}: no answer in {timeout or self.timeout:.0f}s") from exc
        finally:
            self._pending.pop(request_id, None)

    async def tell(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Fire-and-forget, for continuous intents like `robot.move` resent at 10 Hz.

        Sent with an id, the way the robot's own console does it (`tell` in
        `mediad/webclient/index.html`): the robot answers every request, and an answer to an id
        nobody is waiting for is simply dropped by `feed`.
        """
        if self._send is None:
            return
        line = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            line["params"] = params
        await self._send(json.dumps(line, separators=(",", ":")))

    # ── inbound ─────────────────────────────────────────────────────────────────

    def on(self, method: str, listener: Listener) -> None:
        """Call `listener(params)` for every notification of `method`."""
        self._listeners.setdefault(method, []).append(listener)

    def feed(self, raw: str | bytes) -> None:
        """Hand one received line (or several, newline-separated) to the protocol."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if line:
                self._feed_one(line)

    def _feed_one(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("unparseable line from robot: %.200s", line)
            return
        if not isinstance(message, dict):
            return

        request_id = message.get("id")
        if request_id is None:
            method = message.get("method")
            if not method:
                return
            params = message.get("params") or {}
            self.latest[method] = params
            for listener in self._listeners.get(method, []):
                try:
                    listener(params)
                except Exception:  # a broken listener must not take the channel down
                    logger.exception("listener for %s failed", method)
            return

        entry = self._pending.get(request_id)
        if entry is None:
            return
        method, future = entry
        if future.done():
            return
        if "error" in message and message["error"] is not None:
            future.set_exception(RpcError(method, message["error"]))
        else:
            future.set_result(message.get("result"))
