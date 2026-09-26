"""Devices switched off at night, and routines that don't break because of it."""

import asyncio
from datetime import datetime, time as dtime, timedelta

from nest.app import Nest
from nest.config import DuckConfig, load
from nest.routines import QuietHours, Routine, Routines, daily_at


def test_quiet_hours_span_midnight_and_can_be_lifted():
    q = QuietHours(start=dtime(20, 0), end=dtime(9, 0))
    assert q.is_quiet(datetime(2026, 9, 26, 21, 30))
    assert q.is_quiet(datetime(2026, 9, 27, 7, 0))
    assert not q.is_quiet(datetime(2026, 9, 27, 9, 0))
    assert not q.is_quiet(datetime(2026, 9, 27, 19, 59))
    q.lift_until_bedtime(datetime(2026, 9, 27, 7, 0))  # you tapped Good morning at 7
    assert not q.is_quiet(datetime(2026, 9, 27, 7, 30))
    assert q.is_quiet(datetime(2026, 9, 27, 20, 30))    # the lift ends at the next bedtime
    assert not QuietHours(enabled=False).is_quiet(datetime(2026, 9, 27, 23, 0))


def test_routines_wait_for_devices_then_catch_up_or_skip():
    async def main():
        now = {"t": datetime(2026, 9, 27, 9, 0)}
        on = {"reachy": False}
        ran, boom = [], {"left": 1}

        async def wave():
            ran.append(now["t"])

        async def flaky():
            if boom["left"]:
                boom["left"] -= 1
                raise RuntimeError("device went away mid-job")
            ran.append("flaky ok")

        routines = Routines(lambda d: on.get(d, True), clock=lambda: now["t"])
        r = routines.add(Routine("wave", wave, daily_at(dtime(9, 0)), needs=("reachy",),
                                 catch_up=timedelta(hours=2)), run_first_at=now["t"])
        f = routines.add(Routine("flaky", flaky, daily_at(dtime(9, 0))), run_first_at=now["t"])

        await routines.step()
        assert r.last_result == "waiting for reachy" and not ran
        assert f.last_result.startswith("failed")          # raised, recorded, loop still fine
        now["t"] += timedelta(minutes=30)
        on["reachy"] = True                                  # switched on at 9:30
        await routines.step()
        assert r.last_result == "ok" and ran[0] == now["t"]  # caught up
        assert f.last_result == "ok" and "flaky ok" in ran   # retried after backoff
        assert r.due.date() == datetime(2026, 9, 28).date()  # next one tomorrow

        on["reachy"] = False                                 # off all the next day
        now["t"] = datetime(2026, 9, 28, 12, 0)
        await routines.step()
        assert r.last_result == "skipped: reachy stayed off"  # past its catch-up window
        assert r.due.date() == datetime(2026, 9, 29).date()

    asyncio.run(main())


def test_a_night_with_everything_switched_off(tmp_path):
    cfg = load(None)
    cfg.data_dir = tmp_path
    cfg.tick_hz = 5.0
    cfg.ducks = [DuckConfig("ah-ah", bed="ah-ah's bed"), DuckConfig("tee-tee", bed="tee-tee's bed")]
    cfg.landmarks = {"sofa": (1.0, 3.2), "ah-ah's bed": (2.2, 0.4), "tee-tee's bed": (2.8, 0.4)}
    nest = Nest(cfg, sim=True)
    world, reachy = nest.world, nest.world.reachy
    clock = {"t": datetime(2026, 9, 26, 19, 40)}
    nest.clock = lambda: clock["t"]
    nest.watchdog_s = 0.2
    for client in nest.ducks.values():
        client.transport.max_backoff = 0.5

    async def settle(check, seconds=8.0):
        for _ in range(int(seconds / 0.1)):
            if check():
                return True
            await asyncio.sleep(0.1)
        return False

    async def main():
        await nest.start()
        try:
            assert await settle(lambda: all(c.connected for c in nest.ducks.values())
                                and reachy.awake)

            # 19:46 — the goodnight routine runs, a quarter of an hour before quiet hours.
            clock["t"] = datetime(2026, 9, 26, 19, 46)
            await nest.routines.step()
            assert all(m.asleep for m in nest.minds.values())
            assert nest.reachy_status == "asleep"

            # 20:05 — you switch everything off. Nothing breaks; it's just "off".
            clock["t"] = datetime(2026, 9, 26, 20, 5)
            reachy.on = False
            for name in world.ducks:
                world.power(name, False)
            assert await settle(lambda: not any(c.connected for c in nest.ducks.values())
                                and not nest.reachy_online)
            assert nest.reachy_status == "off"
            await nest.routines.step()  # the battery diary etc. just skip the absent ducks
            status = nest.status()
            assert status["quiet"]["now"] and not status["reachy"]["online"]

            # 07:00 — Tee-Tee and Reachy are switched on early. Still quiet: no wake-up, no sound.
            clock["t"] = datetime(2026, 9, 27, 7, 0)
            reachy.on = True
            wakes_before = sum(1 for c, _ in reachy.calls if c == "wake_up")
            world.ducks["tee-tee"].sounds.clear()  # forget what it said yesterday evening
            world.power("tee-tee", True)
            assert await settle(lambda: nest.ducks["tee-tee"].connected and nest.reachy_online)
            await asyncio.sleep(1.0)
            assert world.ducks["tee-tee"].sounds == []
            assert sum(1 for c, _ in reachy.calls if c == "wake_up") == wakes_before
            assert nest.reachy_status.startswith("resting")
            ok, message = await nest.direct("tee-tee", "play")
            assert not ok and "Quiet hours" in message

            # 09:01 — morning. Tee-Tee (already on) says good morning; Reachy wakes.
            clock["t"] = datetime(2026, 9, 27, 9, 1)
            await nest.routines.step()
            assert not any(m.asleep for m in nest.minds.values())
            assert await settle(lambda: "greet" in world.ducks["tee-tee"].sounds
                                and nest.reachy_status == "awake")

            # 10:15 — Ah-Ah is switched on late and gets its own good morning, once.
            clock["t"] = datetime(2026, 9, 27, 10, 15)
            world.ducks["ah-ah"].sounds.clear()
            world.power("ah-ah", True)
            assert await settle(lambda: "greet" in world.ducks["ah-ah"].sounds)
            assert world.ducks["ah-ah"].sounds.count("greet") == 1

            # Every loop is still alive after the whole night.
            assert not any(task.done() for task in nest._tasks)
        finally:
            await nest.stop()

    asyncio.run(main())
