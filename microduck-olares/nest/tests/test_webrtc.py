"""The LAN WebRTC path, against a stand-in for the duck's mediad.

The stand-in speaks the same signalling as the robot's own server (welcome, list, startSession,
sessionStarted, peer sdp/ice) and, like mediad, the *robot* offers and creates the `control`
datachannel. It answers JSON-RPC on that channel. What this proves is that the Nest's side of
the handshake is right for the protocol as the console page documents it; the real robot is
still the final test.
"""

import asyncio
import json

import pytest

aiortc = pytest.importorskip("aiortc")
websockets = pytest.importorskip("websockets")

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack  # noqa: E402

from nest.duck.client import DuckClient  # noqa: E402
from nest.duck.transport import WebRtcTransport  # noqa: E402


async def fake_mediad(ws):
    robot: RTCPeerConnection | None = None
    session = "s-1"
    await ws.send(json.dumps({"type": "welcome", "peerId": "consumer-1"}))
    async for raw in ws:
        message = json.loads(raw)
        if message["type"] == "list":
            await ws.send(json.dumps({"type": "list", "producers": [
                {"id": "duck-producer", "meta": {"name": "ah-ah"}}]}))
        elif message["type"] == "startSession":
            assert message["peerId"] == "duck-producer"
            await ws.send(json.dumps({"type": "sessionStarted", "peerId": "duck-producer",
                                      "sessionId": session}))
            robot = RTCPeerConnection()
            robot.addTrack(VideoStreamTrack())
            channel = robot.createDataChannel("control")

            @channel.on("message")
            def on_message(line):
                request = json.loads(line)
                if request["method"] == "hello":
                    result = {"api_version": 37, "daemon_version": "0.9.0", "revision": None}
                elif request["method"] == "robot.sound":
                    result = {"accepted": True}
                else:
                    channel.send(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                             "error": {"code": -32601, "message": "not here"}}))
                    return
                channel.send(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                         "result": result}))

            offer = await robot.createOffer()
            await robot.setLocalDescription(offer)
            await ws.send(json.dumps({"type": "peer", "sessionId": session, "sdp": {
                "type": "offer", "sdp": robot.localDescription.sdp}}))
            # A trickled candidate too, the way webrtcsink sends them.
            for line in robot.localDescription.sdp.splitlines():
                if line.startswith("a=candidate:"):
                    await ws.send(json.dumps({"type": "peer", "sessionId": session, "ice": {
                        "candidate": line[2:], "sdpMLineIndex": 0}}))
                    break
        elif message["type"] == "peer" and message.get("sdp"):
            assert message["sdp"]["type"] == "answer"
            await robot.setRemoteDescription(RTCSessionDescription(
                sdp=message["sdp"]["sdp"], type="answer"))
        elif message["type"] == "endSession":
            break
    if robot:
        await robot.close()


def test_webrtc_transport_reaches_the_control_channel():
    async def main():
        server = await websockets.serve(fake_mediad, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        transport = WebRtcTransport("ah-ah", "127.0.0.1", port, keep_video=True)
        duck = DuckClient("ah-ah", transport, timeout=5)
        runner = asyncio.ensure_future(transport.run())
        try:
            await asyncio.wait_for(transport.connected.wait(), 20)
            hello = await duck.rpc.call("hello", {"api_version": 37})
            assert hello["api_version"] == 37
            assert (await duck.sound("greet"))["accepted"]
            assert transport.producer_meta == {"name": "ah-ah"}
        finally:
            await transport.stop()
            runner.cancel()
            server.close()
            await server.wait_closed()

    asyncio.run(main())
