import asyncio
import json
import math

import numpy as np
import pytest

from nest.batteries import BatteryDiary
from nest.bus import DuckBus
from nest.duck.rpc import Rpc, RpcClosed, RpcError
from nest.spine.needs import Needs
from nest.spine.personality import Personality
from nest.spine.safety import edge_ahead, hand_near, obstacle_ahead
from nest.spine.tiredness import Band, Tiredness
from nest.vision.duck_detector import Letterbox, decode, letterbox
from nest.world.duckdex import Duckdex
from nest.world.messages import MAX_GGWAVE_BYTES, DuckMessage, Vocabulary
from nest.world.room import DuckTracker, FloorHomography, Navigator
from nest.world.scene import Memory


# ── JSON-RPC ──────────────────────────────────────────────────────────────────


def test_rpc_matches_answers_and_errors_and_notifications():
    async def main():
        rpc = Rpc(timeout=1)
        sent = []

        async def send(line):
            sent.append(json.loads(line))
            message = sent[-1]
            if message["method"] == "hello":
                rpc.feed(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                     "result": {"api_version": 37}}))
            elif message["method"] == "nope":
                rpc.feed(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                     "error": {"code": -32601, "message": "no such method"}}))

        rpc.bind(send)
        states = []
        rpc.on("robot.state", states.append)
        assert (await rpc.call("hello", {"api_version": 37}))["api_version"] == 37
        with pytest.raises(RpcError, match="no such method"):
            await rpc.call("nope")
        rpc.feed('{"jsonrpc":"2.0","method":"robot.state","params":{"t":1}}\n')
        assert states == [{"t": 1}] and rpc.latest["robot.state"] == {"t": 1}
        await rpc.tell("robot.move", {"vx": 0.1, "vy": 0, "vyaw": 0})
        assert "id" in sent[-1]  # continuous intents carry an id, like the console's `tell`

    asyncio.run(main())


def test_rpc_fails_pending_calls_when_the_channel_closes():
    async def main():
        rpc = Rpc(timeout=5)

        async def send(line):
            return None

        rpc.bind(send)
        call = asyncio.ensure_future(rpc.call("robot.health"))
        await asyncio.sleep(0)
        rpc.unbind()
        with pytest.raises(RpcClosed):
            await call

    asyncio.run(main())


# ── tiredness: the battery gauge you can see ───────────────────────────────────


def test_tiredness_bands_with_hysteresis():
    t = Tiredness()
    assert t.update(0, 80) == Band.HIGH
    assert t.update(10, 69) == Band.MEDIUM
    assert t.update(20, 71) == Band.MEDIUM   # needs 73 to climb back: no flicker
    assert t.update(30, 74) == Band.HIGH
    assert t.update(40, 30) == Band.LOW
    assert t.update(50, 12) == Band.VERY_LOW
    assert t.update(60, None) == Band.VERY_LOW  # absent is not known yet, never empty


def test_tiredness_detects_the_charger_from_a_rising_percentage():
    t = Tiredness()
    for i, pct in enumerate([10, 10.5, 11.2, 11.8]):
        band = t.update(i * 60.0, pct)
    assert band == Band.CHARGING
    assert t.energy < 0.2


def test_tiredness_never_touches_walking_speed():
    from nest.spine.tiredness import CUES

    for cue in CUES.values():
        assert "speed" not in cue.prefers and "walk_speed" not in cue.prefers


def test_needs_rise_and_are_satisfied():
    needs = Needs(Personality.from_seed("ah-ah"))
    before = needs.urges["curiosity"]
    needs.tick(600, energy=0.8)
    assert needs.urges["curiosity"] > before
    assert needs.urges["energy"] == pytest.approx(0.2)
    needs.on_fed("meal")
    assert needs.urges["curiosity"] < before
    assert needs.urges["wanderlust"] == 0.0


def test_personalities_differ_and_are_stable():
    a, b = Personality.from_seed("ah-ah"), Personality.from_seed("tee-tee")
    assert a == Personality.from_seed("ah-ah")
    assert a != b
    assert Personality.from_seed("ah-ah", {"boldness": 0.9}).boldness == 0.9


# ── safety ─────────────────────────────────────────────────────────────────────


def frame(middle_mm=2000, floor_mm=500, floor_status=5):
    return {"rows": 8, "cols": 8, "distance_mm": [middle_mm] * 48 + [floor_mm] * 16,
            "status": [5] * 48 + [floor_status] * 16}


def test_obstacles_edges_and_hands():
    assert not obstacle_ahead(frame(), 0.25)
    assert obstacle_ahead(frame(middle_mm=150), 0.25)
    assert not edge_ahead(frame(), 1.2, 3)
    assert edge_ahead(frame(floor_status=255), 1.2, 3)       # the floor vanished
    assert edge_ahead(frame(floor_mm=1800), 1.2, 3)          # the floor dropped away
    assert hand_near({"rows": 8, "cols": 8, "distance_mm": [90] * 64, "status": [5] * 64})
    assert not hand_near(frame())


# ── what ducks say to each other ───────────────────────────────────────────────


def test_found_message_round_trips_in_a_handful_of_bytes():
    vocab = Vocabulary(["yellow ball", "green chair", "sofa"])
    message = DuckMessage("found", 0, 7, "yellow ball", "behind", "green chair", "sofa", 0.9)
    wire = message.encode(vocab)
    assert len(wire) == 9 and len(wire) <= MAX_GGWAVE_BYTES
    back = DuckMessage.decode(wire, vocab)
    assert (back.obj, back.relation, back.landmark, back.near) == \
        ("yellow ball", "behind", "green chair", "sofa")
    assert back.subtitle() == "I found a yellow ball! It's behind the green chair near the sofa."
    assert back.chirp_phrase()[0] == "greet" and back.chirp_phrase()[-1] == "wheee"


def test_unknown_words_travel_as_text():
    vocab = Vocabulary(["sofa"])
    message = DuckMessage("found", 1, 1, "snail shell", "under", "sofa")
    back = DuckMessage.decode(message.encode(vocab), vocab)
    assert back.obj == "snail shell" and back.landmark == "sofa" and back.near is None


def test_bus_waits_for_earshot_then_delivers():
    async def main():
        vocab = Vocabulary(["yellow ball", "green chair"])
        delivered = []
        bus = DuckBus(vocab, on_delivered=delivered.append)
        apart = {"d": 4.0}
        bus.distance = lambda a, b: apart["d"]
        bus.say("ah-ah", "tee-tee", DuckMessage("found", 0, 1, "yellow ball", "behind",
                                                "green chair"))
        assert await bus.pump() == [] and len(bus.pending) == 1   # other room: it waits
        apart["d"] = 0.8
        out = await bus.pump()
        assert len(out) == 1 and not bus.pending
        assert "0.8 m apart" in delivered[0].evidence

    asyncio.run(main())


# ── memory and the Duckdex ─────────────────────────────────────────────────────


def test_memory_first_hand_and_hearsay(tmp_path):
    ah = Memory("ah-ah", tmp_path / "ah.json")
    ah.add_landmark("green chair", (3.4, 2.6), aliases=["grandma's chair"])
    s = ah.saw("Yellow Ball", "behind", "green chair", "sofa")
    assert ah.resolve("grandma's chair").name == "green chair"
    tee = Memory("tee-tee", tmp_path / "tee.json")
    heard = tee.heard(s, "ah-ah")
    assert heard.told_by == "ah-ah" and heard.confidence < s.confidence
    assert tee.where_is("yellow ball").describe() == "behind the green chair near the sofa"
    tee.gone("yellow ball")
    assert tee.where_is("yellow ball") is None
    reloaded = Memory("ah-ah", tmp_path / "ah.json")
    assert reloaded.where_is("yellow ball").landmark == "green chair"


def test_curiosity_is_food(tmp_path):
    dex = Duckdex("tee-tee", tmp_path / "dex.json")
    assert dex.feed("pinecone", now=0) == "meal"
    assert dex.feed("pinecone", now=10) == "snack"
    dex.feed("pinecone", now=20)
    assert dex.feed("pinecone", now=30) == "boring"
    assert dex.feed("pinecone", now=5000) == "snack"  # after a while it's interesting again
    assert len(Duckdex("tee-tee", tmp_path / "dex.json")) == 1


# ── the room ───────────────────────────────────────────────────────────────────


def test_floor_homography_recovers_a_known_mapping():
    true = np.array([[0.01, 0.002, -1.0], [0.0005, 0.012, -0.5], [0.0001, 0.0004, 1.0]])
    pixels = [(100, 400), (540, 410), (500, 250), (140, 240), (320, 300)]
    floor = []
    for u, v in pixels:
        x, y, w = true @ np.array([u, v, 1.0])
        floor.append((x / w, y / w))
    h = FloorHomography()
    assert h.fit(pixels, floor) < 1e-6
    x, y, w = true @ np.array([200.0, 350.0, 1.0])
    assert np.allclose(h.to_floor(200, 350), (x / w, y / w), atol=1e-6)


def test_tracker_keeps_identities_and_roll_call_names_them():
    tracker = DuckTracker(["ah-ah", "tee-tee"])
    tracker.update([(1.0, 1.0), (3.0, 1.0)], now=0)
    snap = tracker.snapshot()
    tracker.update([(1.2, 1.0), (3.0, 1.0)], now=1)   # the left one stepped
    assert tracker.roll_call("tee-tee", snap)
    tracker.update([(1.25, 1.05), (3.02, 0.98)], now=2)
    names = {t.name: (t.x, t.y) for t in tracker.tracks}
    assert names["tee-tee"][0] < 2 and names["ah-ah"][0] > 2


def test_navigator_learns_the_odometry_offset():
    nav = Navigator()
    offset = 1.0  # the duck booted facing 1 rad away from the room's x axis
    for i in range(5):
        d = 0.1 * i
        odom = (d, 0.0)
        room = (1 + d * math.cos(offset), 1 + d * math.sin(offset))
        nav.observe(odom, room)
    assert nav.calibrated and abs(nav.offset - offset) < 1e-6
    step, amount = nav.plan(0.0, (1.0, 1.0), (1.0 + math.cos(1.0), 1.0 + math.sin(1.0)))
    assert step == "walk"
    step, amount = nav.plan(0.0, (1.0, 1.0), (0.0, 1.0))
    assert step == "turn"


# ── the duck detector's decode matches the robot's ─────────────────────────────


def test_detector_decode_reads_planar_output_and_undoes_the_letterbox():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    canvas, box = letterbox(frame)
    assert canvas.shape == (320, 320, 3) and box.pad_y == 40 and box.scale == 0.5
    n = 2100
    raw = np.zeros((1, 5, n), dtype=np.float32)          # [1, 5, N], planar, like the export
    raw[0, :, 0] = [160, 160, 40, 40, 0.9]                 # one duck in the middle
    raw[0, :, 1] = [162, 161, 40, 40, 0.8]                 # a near-duplicate NMS must drop
    raw[0, :, 2] = [60, 100, 20, 20, 0.2]                  # below threshold
    boxes = decode(raw, box, threshold=0.4)
    assert len(boxes) == 1
    b = boxes[0]
    assert (b.x0, b.y0, b.x1, b.y1) == pytest.approx((280, 200, 360, 280))
    assert b.foot == pytest.approx((320, 280))


# ── batteries ──────────────────────────────────────────────────────────────────


def test_battery_diary_tracks_packs_and_drain(tmp_path):
    diary = BatteryDiary(tmp_path / "b.json")
    diary.pit_stop("ah-ah", "a", now=0)
    for minute, pct in [(0, 100), (30, 80), (60, 60)]:
        diary.sample("ah-ah", pct, now=minute * 60)
    diary.pit_stop("tee-tee", "A", now=4000)   # moving pack A to the other duck
    assert diary.fitted == {"tee-tee": "A"}
    assert diary.health()["A"]["drain_pct_per_hour"] == 40.0
    with pytest.raises(ValueError):
        diary.pit_stop("ah-ah", "Z")
