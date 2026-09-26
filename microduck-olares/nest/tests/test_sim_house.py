"""The whole Nest against the simulated living room: two ducks, Reachy, the bus."""

import asyncio

from nest.app import Nest
from nest.config import DuckConfig, load


def make_nest(tmp_path):
    cfg = load(None)
    cfg.data_dir = tmp_path
    cfg.tick_hz = 5.0
    cfg.ducks = [DuckConfig("ah-ah", bed="ah-ah's bed"), DuckConfig("tee-tee", bed="tee-tee's bed")]
    cfg.landmarks = {"sofa": (1.0, 3.2), "green chair": (3.4, 2.6), "ah-ah's bed": (2.2, 0.4),
                     "tee-tee's bed": (2.8, 0.4)}
    return Nest(cfg, sim=True)


def test_the_yellow_ball_story(tmp_path):
    async def main():
        nest = make_nest(tmp_path)
        await nest.start()
        try:
            for _ in range(50):
                if all(c.connected and c.state for c in nest.ducks.values()):
                    break
                await asyncio.sleep(0.1)
            status = nest.status()
            assert all(d["connected"] for d in status["ducks"].values())
            assert status["reachy"]["status"] == "awake"

            # Ah-Ah finds the ball: a meal, and a message queued for Tee-Tee.
            result = nest.record_sighting("ah-ah", "yellow ball", "behind", "green chair", "sofa")
            assert result["meal"] == "meal" and result["queued_for_friend"]

            # Put them side by side so they are within earshot, and let the bus deliver.
            for name, xy in (("ah-ah", (2.0, 2.0)), ("tee-tee", (2.5, 2.0))):
                duck = nest.world.ducks[name]
                duck.x, duck.y = xy
            for _ in range(60):
                if nest.bus.log:
                    break
                await asyncio.sleep(0.1)
            assert nest.bus.log, "the message was never delivered"
            answer = nest.where_is("tee-tee", "yellow ball")
            assert answer["known"] and answer["told_by"] == "ah-ah"

            # Tee-Tee now has a rumour worth investigating.
            assert nest.minds["tee-tee"].rumour() is not None
            assert "investigate" in nest.minds["tee-tee"].weights()

            # Tee-Tee sees it for itself: the Duckdex entry is credited to Ah-Ah.
            nest.record_sighting("tee-tee", "yellow ball", "behind", "green chair", "sofa")
            entry = nest.minds["tee-tee"].duckdex.entries["yellow ball"]
            assert entry.credited_to == "ah-ah"
        finally:
            await nest.stop()

    asyncio.run(main())


def test_a_very_tired_duck_goes_to_bed_and_reachy_droops(tmp_path):
    async def main():
        nest = make_nest(tmp_path)
        nest.world.ducks["tee-tee"].battery = 9.0
        await nest.start()
        try:
            for _ in range(80):
                mind = nest.minds["tee-tee"]
                if mind.tiredness.band.value == "very_low" and nest.reachy_focus == "tee-tee":
                    break
                await asyncio.sleep(0.1)
            assert nest.minds["tee-tee"].tiredness.band.value == "very_low"
            assert nest.minds["tee-tee"].weights().get("go_to_bed", 0) >= 5.0
            focus, expression = nest._choose_focus()
            assert focus == "tee-tee" and expression == "droop"
        finally:
            await nest.stop()

    asyncio.run(main())


def test_interrupting_a_walk_stops_the_behaviour(tmp_path):
    async def main():
        nest = make_nest(tmp_path)
        await nest.start()
        try:
            mind = nest.minds["ah-ah"]
            for _ in range(50):
                if mind.client.connected and mind.client.tof:
                    break
                await asyncio.sleep(0.1)
            mind.paused = True
            mind.interrupt()
            reached_the_end = []

            async def long_walk(m):
                await m.client.walk_for(0.15, 0.0, 0.0, 10.0)
                reached_the_end.append(True)

            mind._start("test_walk", long_walk(mind))
            await asyncio.sleep(0.5)
            mind.interrupt()
            await asyncio.sleep(0.3)
            assert not mind.busy and not reached_the_end
        finally:
            await nest.stop()

    asyncio.run(main())


def test_bedtime_keeps_them_asleep_until_morning(tmp_path):
    async def main():
        nest = make_nest(tmp_path)
        await nest.start()
        try:
            await asyncio.sleep(1.0)
            await nest.bedtime()
            assert nest.reachy_status == "asleep"
            assert all(m.weights() == {"nap": 1.0} for m in nest.minds.values())
            await nest.wake()
            assert nest.reachy_status == "awake"
            assert all(not m.asleep for m in nest.minds.values())
        finally:
            await nest.stop()

    asyncio.run(main())


def test_a_new_behaviour_keeps_its_name_when_the_old_one_is_interrupted(tmp_path):
    async def main():
        nest = make_nest(tmp_path)
        mind = nest.minds["ah-ah"]

        async def forever(m):
            await asyncio.sleep(60)

        mind._start("first", forever(mind))
        await asyncio.sleep(0.05)
        mind.interrupt()
        mind._start("second", forever(mind))
        await asyncio.sleep(0.05)
        assert mind.behaviour == "second"
        mind.interrupt()
        await asyncio.sleep(0.05)
        assert mind.behaviour is None

    asyncio.run(main())
