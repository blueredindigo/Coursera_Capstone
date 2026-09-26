"""Before the ducks arrive: Reachy alone, eggs in the beds, the floor map, Reachy's diary."""

import asyncio
import base64
import math
import socket

import numpy as np

from nest.app import Nest
from nest.config import DuckConfig, load
from nest.reachy import ReachyGaze
from nest.sim import SimReachy
from nest.world.floormap import FloorMap, view_polygon


def sim_nest(tmp_path, ducks_on=True):
    cfg = load(None)
    cfg.data_dir = tmp_path
    cfg.tick_hz = 5.0
    cfg.quiet_hours = {"enabled": False}
    cfg.ducks = [DuckConfig("ah-ah", bed="ah-ah's bed"), DuckConfig("tee-tee", bed="tee-tee's bed")]
    cfg.landmarks = {"sofa": (1.0, 3.2), "table": (3.0, 2.0), "ah-ah's bed": (2.2, 0.4),
                     "tee-tee's bed": (2.8, 0.4)}
    nest = Nest(cfg, sim=True)
    nest.watchdog_s = 0.2
    for client in nest.ducks.values():
        client.transport.max_backoff = 0.3
    if not ducks_on:
        for name in nest.world.ducks:
            nest.world.power(name, False)
    return nest


async def settle(check, seconds=8.0):
    for _ in range(int(seconds / 0.1)):
        if check():
            return True
        await asyncio.sleep(0.1)
    return False


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_real_nest_runs_with_ducks_that_have_never_connected(tmp_path):
    """The real transports, pointed at ducks that don't exist yet, and no Reachy either."""
    async def main():
        cfg = load(None)
        cfg.data_dir = tmp_path
        port = free_port()
        cfg.ducks = [DuckConfig("ah-ah", "127.0.0.1", signalling_port=port),
                     DuckConfig("tee-tee", "127.0.0.1", signalling_port=port)]
        cfg.reachy.url = f"http://127.0.0.1:{free_port()}"
        nest = Nest(cfg)
        nest.watchdog_s = 0.2
        await nest.start()
        try:
            await asyncio.sleep(2.0)
            status = nest.status()
            assert not any(d["connected"] for d in status["ducks"].values())
            assert not any(d["hatched"] for d in status["ducks"].values())
            assert status["reachy"]["status"] == "off"
            ok, message = await nest.direct("ah-ah", "play")
            assert not ok and "isn't connected" in message
            assert not any("crashed" in e["text"] for e in nest.feed)
            state = nest.map_state()
            assert state["calibrated"] is False  # no floor calibration yet: nothing to map
        finally:
            await nest.stop()

    asyncio.run(main())


def test_reachy_alone_maps_the_lounge_and_peeks_at_the_eggs(tmp_path):
    async def main():
        nest = sim_nest(tmp_path, ducks_on=False)
        await nest.start()
        try:
            assert await settle(lambda: nest.reachy_status == "awake")
            assert await settle(lambda: nest.floormap.coverage()["seen"] > 10)
            state = nest.map_state()
            cells = np.frombuffer(base64.b64decode(state["cells"]), np.uint8)
            assert (cells == 255).any()          # some of the floor is in view right now
            assert (cells == 0).any()            # and some has never been seen
            assert not state["ducks"]["ah-ah"]["hatched"]
            assert state["ducks"]["ah-ah"]["bed"] == [2.2, 0.4]

            reachy = nest.world.reachy
            before = len(reachy.calls)
            await nest._routine_peek_at_eggs()
            assert len(reachy.calls) >= before + 2  # leaned over, then back to watching
            assert nest.gaze.moved_at is not None
            assert any("peeked at" in e["text"] for e in nest.feed)
        finally:
            await nest.stop()

    asyncio.run(main())


def test_hatch_day(tmp_path):
    async def main():
        nest = sim_nest(tmp_path, ducks_on=False)
        # Reachy has been keeping an eye on the lounge for weeks.
        nest.reachy_memory.saw("yellow ball", "under", "table", "sofa", confidence=0.8)
        await nest.start()
        try:
            assert await settle(lambda: nest.reachy_status == "awake")
            wiggles_before = sum(1 for c, a in nest.world.reachy.calls
                                 if c == "goto" and a["antennas"] == (0.4, -0.4))
            nest.world.power("ah-ah", True)  # Christmas morning
            assert await settle(lambda: "ah-ah" in nest.hatched)
            assert await settle(lambda: any("hatched!" in e["text"] for e in nest.feed))
            assert "tee-tee" not in nest.hatched
            answer = nest.where_is("ah-ah", "yellow ball")
            assert answer["known"] and answer["told_by"] == "reachy"
            assert await settle(lambda: sum(1 for c, a in nest.world.reachy.calls
                                            if c == "goto" and a["antennas"] == (0.4, -0.4))
                                >= wiggles_before + 3)
        finally:
            await nest.stop()
        # Hatching happens once, ever: it's remembered across restarts.
        again = sim_nest(tmp_path, ducks_on=False)
        assert "ah-ah" in again.hatched

    asyncio.run(main())


def test_floor_map_marks_ages_encodes_and_persists(tmp_path):
    fm = FloorMap((0, 0, 2, 1), cell=0.1, who=["ah-ah"], path=tmp_path / "map.npz")
    assert (fm.h, fm.w) == (10, 20)
    n = fm.see_polygon([(0, 0), (1, 0), (1, 1), (0, 1)], "reachy", now=1000.0)
    assert n == 100
    fm.see_disc(1.5, 0.5, 0.15, "ah-ah", now=1000.0 + 3600)
    enc = fm.encode(now=1000.0 + 3600)
    cells = np.frombuffer(base64.b64decode(enc["cells"]), np.uint8).reshape(10, 20)
    assert cells[5, 2] == 1 + 60 // 30          # seen an hour ago by Reachy: bucket 3
    assert cells[5, 15] == 255                  # Ah-Ah is looking right now
    assert cells[5, 19] == 0                    # never seen
    assert fm.at(0.5, 0.5, now=1000.0 + 3600)["by"] == "reachy"
    assert fm.coverage(now=1000.0 + 3600)["seen"] > 50
    fm.save(force=True)
    back = FloorMap((0, 0, 2, 1), cell=0.1, who=["ah-ah"], path=tmp_path / "map.npz")
    assert back.at(1.5, 0.5, now=5000)["by"] == "ah-ah"
    # Changed bounds: start fresh rather than misplace everything.
    fresh = FloorMap((0, 0, 3, 1), cell=0.1, path=tmp_path / "map.npz")
    assert not fresh.at(0.5, 0.5)["known"]


def test_reachys_view_polygon_from_the_floor_calibration():
    # A camera 1 m up at the room origin, looking along +x, tipped down 30 degrees.
    f, w, h = 600.0, 1280, 720
    k = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]])
    tilt = math.radians(30)
    # camera axes in the room frame: right = -y, down, forward
    fwd = np.array([math.cos(tilt), 0, -math.sin(tilt)])
    right = np.array([0, -1.0, 0])
    down = np.cross(fwd, right)
    r = np.stack([right, down, fwd])          # room → camera rotation
    t = -r @ np.array([0, 0, 1.0])
    p = k @ np.column_stack([r[:, 0], r[:, 1], t])  # floor (z=0) → pixels
    homography = np.linalg.inv(p)
    poly = view_polygon(homography, w, h, (0.0, 0.0), max_range=5.0)
    assert len(poly) > 6
    fm = FloorMap((-1, -4, 6, 4), cell=0.1)
    mask = fm.polygon_mask(poly)
    ij = fm.cell_of(2.0, 0.0)
    assert mask[ij]                                     # straight ahead, on the floor
    assert not mask[fm.cell_of(-0.5, 0.0)]              # behind the camera
    assert not mask[fm.cell_of(5.8, 0.0)]               # beyond the range it trusts
    assert all(math.dist(q, (0, 0)) <= 5.0 + 1e-6 for q in poly)


def test_the_map_only_uses_reachys_eyes_in_the_watch_pose():
    async def main():
        reachy = SimReachy()
        gaze = ReachyGaze(reachy)
        assert not gaze.at_watch_pose()          # unknown until the Nest has moved it
        await gaze.watch(duration=0.5)
        assert not gaze.at_watch_pose(settle_s=0.2)   # still moving
        await asyncio.sleep(0.8)
        assert gaze.at_watch_pose(settle_s=0.2)
        await gaze.look_at_bearing(0.8, duration=0.5)
        await asyncio.sleep(0.8)
        assert not gaze.at_watch_pose(settle_s=0.2)   # looking at a duck
        await gaze.watch(duration=0.5)
        await asyncio.sleep(0.8)
        assert gaze.at_watch_pose(settle_s=0.2)
        gaze.forget_pose()                       # switched off: pose unknown again
        assert not gaze.at_watch_pose(settle_s=0.0)

    asyncio.run(main())


class ScriptedCaptioner:
    def __init__(self, answers):
        self.answers = list(answers)
        self.viewers = []

    async def describe(self, png, landmarks, viewer="duck"):
        self.viewers.append(viewer)
        return self.answers.pop(0)


def test_reachys_diary_notices_new_moved_and_gone_things(tmp_path, monkeypatch):
    async def main():
        import nest.app as app_module

        monkeypatch.setattr(app_module, "_png", lambda frame: b"png")
        nest = sim_nest(tmp_path)
        nest.reachy_online, nest.reachy_status = True, "awake"
        nest.gaze = ReachyGaze(nest.world.reachy)
        await nest.gaze.watch(duration=0.0)
        nest.gaze.moved_at -= 5
        ball = {"object": "yellow ball", "relation": "under", "landmark": "table", "near": None,
                "confidence": 0.9}
        moved = {**ball, "relation": "next to", "landmark": "sofa"}
        cap = ScriptedCaptioner([[ball], [moved], [], [], []])

        async def look():
            nest.reachy_frame, nest.reachy_frame_at = np.zeros((4, 4, 3), np.uint8), \
                __import__("time").time()
            return await nest.diary_step(cap)

        assert await look() == ["spotted a yellow ball under the table"]
        assert await look() == ["the yellow ball has moved: it was under the table, "
                                "now it's next to the sofa"]
        assert await look() == [] and await look() == []  # small models miss things
        assert await look() == ["the yellow ball isn't next to the sofa any more"]
        assert cap.viewers == ["reachy"] * 5
        assert nest.reachy_diary() == []

    asyncio.run(main())


def test_the_walkthrough_floor_plan_seeds_the_map(tmp_path):
    floor = np.zeros((40, 60), bool)
    floor[5:35, 5:55] = True
    blocked = np.zeros_like(floor)
    blocked[5:35, 5] = True                      # a wall
    np.savez(tmp_path / "floorplan.npz", x0=-0.5, y0=-0.5, cell=0.1, floor=floor,
             blocked=blocked, filmed_at=1_000_000.0)
    nest = sim_nest(tmp_path)
    fm = nest.floormap
    assert fm.bounds == (-0.5, -0.5, 5.5, 3.5) and fm.cell == 0.1
    spot = fm.at(2.0, 1.5, now=1_000_000.0 + 3600)
    assert spot["known"] and spot["by"] == "your walkthrough"
    assert not fm.at(0.0, -0.3)["inside"]        # outside the flat
    assert fm.coverage()["seen"] == 0            # the walkthrough isn't a robot looking
    enc = nest.map_state()
    assert enc["walkthrough"]
    cells = np.frombuffer(base64.b64decode(enc["cells"]), np.uint8).reshape(40, 60)
    assert cells[20, 5] == 253                   # the wall
