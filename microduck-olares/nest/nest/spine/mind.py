"""One duck's mind: needs, tiredness, personality, memory, and what to do next.

Ticks at ~1 Hz. Each tick reads what the robot reported, updates the needs, and, when the duck
is not already doing something, picks a behaviour by weighted chance: needs × personality ×
the tiredness band's preferences. Behaviours are short (seconds to a minute) and always yield
to the duck's own safety: a refused walk becomes a turn away or a sit, never a retry loop.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..duck.rpc import RpcError
from ..world.duckdex import Duckdex
from ..world.room import Navigator
from ..world.scene import Memory
from .needs import Needs
from .personality import Personality
from .safety import SafetyGate, hand_near
from .tiredness import Band, Thresholds, Tiredness

if TYPE_CHECKING:
    from ..duck.client import DuckClient

logger = logging.getLogger(__name__)


@dataclass
class Context:
    """What a mind can see of the rest of the house. Filled in by the app."""

    friend_name: str | None = None
    friend_connected: Callable[[], bool] = lambda: False
    friend_near: Callable[[], bool] = lambda: False
    my_room_xy: Callable[[], tuple[float, float] | None] = lambda: None
    friend_room_xy: Callable[[], tuple[float, float] | None] = lambda: None
    bed_xy: Callable[[], tuple[float, float] | None] = lambda: None
    nest_xy: Callable[[], tuple[float, float] | None] = lambda: None
    tell_friend: Callable[..., Any] = lambda *args, **kwargs: None
    on_event: Callable[[str, str], None] = lambda duck, text: None


class Mind:
    def __init__(self, name: str, client: "DuckClient", personality: Personality,
                 memory: Memory, duckdex: Duckdex, thresholds: Thresholds | None = None,
                 gate: SafetyGate | None = None, rng: random.Random | None = None,
                 walk_speed: float = 0.15):
        self.name = name
        self.client = client
        self.personality = personality
        self.needs = Needs(personality)
        self.tiredness = Tiredness(thresholds)
        self.gate = gate or SafetyGate()
        self.memory = memory
        self.duckdex = duckdex
        self.navigator = Navigator()
        self.rng = rng or random.Random()
        # The duck's best walking speed. Tiredness never lowers it: a tired duck walks less,
        # not worse.
        self.walk_speed = walk_speed
        self.ctx = Context()
        self.behaviour: str | None = None
        self.behaviour_started = 0.0
        self._task: asyncio.Task | None = None
        self.sitting = False
        self.events: deque[tuple[float, str]] = deque(maxlen=50)
        self.paused = False  # set by the Pond ("leave Ah-Ah alone for now")
        self.asleep = False  # between bedtime and good morning: only sleep
        self.in_bed = False  # went to bed and hasn't stood up since
        self.quiet = False   # quiet hours: no motion, no sound, whatever the needs say
        self._checks: dict[str, float] = {}  # rumours already gone to look at
        self._last_tick = time.monotonic()
        self._last_health = 0.0
        self._health: asyncio.Task | None = None
        self._petting_s = 0.0

    # ── bookkeeping ────────────────────────────────────────────────────────────

    async def _poll_health(self) -> None:
        try:
            await self.client.poll_health()
        except Exception as exc:
            logger.debug("%s: health poll failed: %s", self.name, exc)

    def note(self, text: str) -> None:
        self.events.append((time.time(), text))
        self.ctx.on_event(self.name, text)
        logger.info("%s: %s", self.name, text)

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def on_disconnected(self) -> None:
        """The duck was switched off (or the Wi-Fi dropped). Forget what we believed about its
        body: after a power cycle it isn't sitting or in bed, and its battery may be new."""
        self.interrupt()
        self.sitting = False
        self.in_bed = False
        self.tiredness = Tiredness(self.tiredness.t)

    def interrupt(self) -> None:
        if self.busy:
            self._task.cancel()

    # ── the tick ───────────────────────────────────────────────────────────────

    async def tick(self) -> None:
        now = time.monotonic()
        dt = min(5.0, now - self._last_tick)
        self._last_tick = now
        if not self.client.connected:
            return

        if now - self._last_health > 5.0 and not (self._health and not self._health.done()):
            self._last_health = now
            # In the background: a duck whose RPC hangs must not stall the other duck's tick.
            self._health = asyncio.ensure_future(self._poll_health())
        before = self.tiredness.band
        band = self.tiredness.update(now, self.client.health.battery_percent,
                                     self.client.health.hottest_c)
        if band != before and before != Band.UNKNOWN:
            self.note(f"battery {self.tiredness.percent:.0f}%: now {band.value.replace('_', ' ')}")
            if band in (Band.LOW, Band.VERY_LOW) and not self.busy:
                self._start("yawn", self._yawn())

        self.needs.tick(dt, self.tiredness.energy)
        self._sense(dt)
        self._teach_navigator()

        if self.quiet:
            if self.busy:
                self.interrupt()  # quiet hours began mid-behaviour: stop, don't finish it
            return
        if self.paused or self.busy:
            return
        if self.client.fallen:
            return  # the duck gets itself up; the Nest just waits
        choice = self.choose()
        if choice:
            self._start(choice, BEHAVIOURS[choice](self))

    def _sense(self, dt: float) -> None:
        tof = self.client.tof
        if tof and time.monotonic() - self.client.tof_at < 1.0 and hand_near(tof):
            self._petting_s += dt
            self.needs.on_petted(dt)
            if self._petting_s >= 2.0 and self._petting_s - dt < 2.0:
                self.note("a hand! (affection)")
        else:
            self._petting_s = 0.0
        if self.ctx.friend_near():
            self.needs.on_friend_near(dt)

    def _teach_navigator(self) -> None:
        odom, room = self.client.odom, self.ctx.my_room_xy()
        if odom and room and abs(self.client.state.get("move", {}).get("applied", [0])[0]) > 0.02:
            self.navigator.observe((odom[0], odom[1]), room)

    def _start(self, name: str, coroutine: Awaitable[Any]) -> None:
        self.behaviour = name
        self.behaviour_started = time.monotonic()

        async def run():
            try:
                await coroutine
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.note(f"{name} stopped: {exc}")
            finally:
                if asyncio.current_task() is self._task:  # not if a newer behaviour took over
                    self.behaviour = None

        task = asyncio.ensure_future(run())

        def done(t: asyncio.Task) -> None:
            # Cancelled before it ever ran: close the coroutine and clear the name ourselves.
            close = getattr(coroutine, "close", None)
            if close:
                close()
            if self._task is t:
                self.behaviour = None

        task.add_done_callback(done)
        self._task = task

    # ── choosing ───────────────────────────────────────────────────────────────

    def weights(self) -> dict[str, float]:
        u, p = self.needs.urges, self.personality
        band = self.tiredness.band
        w = {
            "rest": 0.1 + 1.2 * u["energy"] ** 2,
            "look_around": 0.35 + 0.6 * u["curiosity"],
            "wander": (0.2 + 1.2 * u["curiosity"] * (0.5 + p.boldness)) * (1 - u["energy"]),
            "seek_friend": 1.6 * u["social"] * (0.5 + p.sociability)
            if self.ctx.friend_connected() and not self.ctx.friend_near() else 0.0,
            "greet_friend": 0.8 * p.sociability if self.ctx.friend_near() else 0.0,
            "call_out": 1.2 * u["affection"] * (0.5 + p.cuddliness),
            "play": 1.4 * u["play"] * (0.5 + p.playfulness) * (1 - u["energy"]),
            "chatter": 0.1 + 0.3 * p.chattiness,
            "investigate": (0.6 + 1.6 * u["curiosity"]) * (1 - u["energy"])
            if self.rumour() is not None else 0.0,
            "go_to_bed": 0.0,
            "nap": 0.0,
        }
        if band == Band.CHARGING or self.asleep:
            w = {k: 0.0 for k in w} | {"nap": 1.0}
        for name, factor in self.tiredness.cues.prefers.items():
            if name in w:
                w[name] *= factor
        if band == Band.VERY_LOW and not self.asleep and not self.in_bed:
            w["go_to_bed"] = 5.0
        if self.tiredness.needs_rest_for_heat:
            w = {k: 0.0 for k in w} | {"rest": 1.0}
        return {k: v for k, v in w.items() if v > 0}

    def rumour(self):
        """Something another duck told us about, somewhere we know how to reach, that we have
        not seen for ourselves yet."""
        for sighting in reversed(self.memory.sightings):
            if (sighting.told_by and not sighting.stale and sighting.obj not in self.duckdex.entries
                    and self._checks.get(sighting.obj, 0) < 2):
                mark = self.memory.resolve(sighting.landmark)
                if mark is not None and mark.xy is not None:
                    return sighting, mark
        return None

    def choose(self) -> str | None:
        weights = self.weights()
        if not weights:
            return None
        names, values = zip(*weights.items())
        return self.rng.choices(names, weights=values)[0]

    # ── primitives ─────────────────────────────────────────────────────────────

    async def say(self, tag: str) -> None:
        try:
            await self.client.sound(tag)
        except Exception as exc:
            logger.debug("%s: sound %s failed: %s", self.name, tag, exc)

    async def _toggle_sit(self, sitting: bool) -> None:
        # The flag follows the request, set before awaiting it: a behaviour cancelled while the
        # call is in flight still sent the toggle, and the robot still carries it out. Only an
        # answered refusal means it did not happen.
        self.sitting = sitting
        try:
            result = await self.client.do("sit_toggle")
        except RpcError:
            self.sitting = not sitting
            raise
        if isinstance(result, dict) and result.get("accepted") is False:
            self.sitting = not sitting
        await asyncio.sleep(2.0)

    async def stand(self) -> None:
        if self.sitting:
            await self._toggle_sit(False)
        self.in_bed = False

    async def sit(self) -> None:
        if not self.sitting:
            await self.client.stop()
            await self._toggle_sit(True)

    async def hold_head(self, pitch: float, seconds: float, yaw: float = 0.0) -> None:
        """Hold a head pose. Head intents are continuous, so resend them."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            await self.client.head(head_pitch=pitch, head_yaw=yaw)
            await asyncio.sleep(0.2)

    async def walk(self, seconds: float, vyaw: float = 0.0) -> bool:
        """Walk forward at full ability, gated. Returns False if the gate refused to start."""
        ok, why = self.gate.may_move(self.client, forward=True)
        if not ok:
            self.note(f"not walking: {why}")
            return False
        await self.stand()
        await self.client.walk_for(self.walk_speed, 0.0, vyaw, seconds,
                                   keep_going=lambda: self.gate.may_move(self.client)[0])
        return True

    async def turn(self, radians: float, rate: float = 0.8) -> None:
        ok, why = self.gate.may_move(self.client, forward=False)
        if not ok:
            self.note(f"not turning: {why}")
            return
        await self.stand()
        await self.client.walk_for(0.0, 0.0, math.copysign(rate, radians), abs(radians) / rate,
                                   keep_going=lambda: self.gate.may_move(self.client,
                                                                         forward=False)[0])

    async def go_to(self, goal: tuple[float, float], budget_s: float = 40.0) -> bool:
        """Head for a room-frame point using Reachy's map. True on arrival."""
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            here, odom = self.ctx.my_room_xy(), self.client.odom
            if here is None or odom is None:
                return False  # out of Reachy's view: go by landmarks instead (later phase)
            step, amount = self.navigator.plan(odom[2], here, goal)
            if step == "arrived":
                return True
            if step == "calibrate":
                if not await self.walk(2.0):
                    await self.turn(self.rng.choice([-1, 1]) * 1.2)
            elif step == "turn":
                await self.turn(amount)
            else:
                if not await self.walk(min(4.0, amount / max(self.walk_speed, 0.05))):
                    await self.turn(self.rng.choice([-1, 1]) * 0.8)
            await asyncio.sleep(0.3)
        return False

    # ── reactions ──────────────────────────────────────────────────────────────

    async def _yawn(self) -> None:
        await self.client.mouth(0.9)
        await self.say("coo")
        await self.hold_head(self.tiredness.cues.head_pitch - 0.1, 1.5)
        await self.client.mouth(0.0)


# ── behaviours ──────────────────────────────────────────────────────────────────


async def rest(m: Mind) -> None:
    await m.sit()
    cues = m.tiredness.cues
    for _ in range(m.rng.randint(3, 8)):
        await m.hold_head(cues.head_pitch - 0.05, 4.0)
        if m.rng.random() < 0.3 * m.personality.chattiness and cues.sounds:
            await m.say(m.rng.choice(cues.sounds))


async def nap(m: Mind) -> None:
    await m.sit()
    if m.behaviour_started - getattr(m, "_last_nap_note", -1e9) > 600:
        m._last_nap_note = m.behaviour_started
        m.note("asleep" if m.asleep else "napping on the charger")
    await m.hold_head(m.tiredness.cues.head_pitch, 60.0)


async def go_to_bed(m: Mind) -> None:
    m.note("tired: heading to bed")
    await m.say("coo")
    bed = m.ctx.bed_xy()
    if bed is not None:
        arrived = await m.go_to(bed)
        m.note("in bed" if arrived else "couldn't reach the bed: sleeping where it is")
    await m.sit()
    m.in_bed = True
    await m.hold_head(-0.35, 30.0)


async def look_around(m: Mind) -> None:
    await m.stand()
    for _ in range(m.rng.randint(3, 5)):
        bearing = m.rng.uniform(-1.0, 1.0)
        x, y = math.cos(bearing), math.sin(bearing)
        await m.client.look(x, y, m.rng.uniform(-0.1, 0.25))
        await asyncio.sleep(m.rng.uniform(1.0, 2.5))
        if m.rng.random() < 0.25 * m.personality.chattiness:
            await m.say("inquire")
    m.needs.satisfy("curiosity", 0.04)


async def wander(m: Mind) -> None:
    await m.turn(m.rng.uniform(-1.5, 1.5))
    walked = await m.walk(m.rng.uniform(2.0, 5.0) * (0.6 + m.personality.boldness))
    if walked:
        m.needs.satisfy("curiosity", 0.08)
        m.needs.satisfy("play", 0.02)
    else:
        await m.turn(m.rng.choice([-1, 1]) * 2.0)


async def seek_friend(m: Mind) -> None:
    friend = (m.ctx.friend_name or "its friend").title()
    goal = m.ctx.friend_room_xy()
    if goal is not None:
        m.note(f"going to find {friend}")
        await m.go_to(goal, budget_s=30.0)
    else:
        # Out of Reachy's view: turn and look, like a duck scanning the room.
        for _ in range(6):
            if m.client.sees_a_duck():
                break
            await m.turn(0.8)
            await asyncio.sleep(0.8)
    if m.client.sees_a_duck() or m.ctx.friend_near():
        await greet_friend(m)
    else:
        await m.say("inquire")


async def greet_friend(m: Mind) -> None:
    sighting = m.client.sighting
    if sighting and sighting.boxes:
        bearing = sighting.bearing(max(sighting.boxes, key=lambda b: b.get("score", 0)))
        await m.client.look(math.cos(bearing), math.sin(bearing), 0.0)
    await m.say("greet")
    m.needs.on_friend_near(10.0)
    m.note(f"greeted {(m.ctx.friend_name or 'its friend').title()}")


async def call_out(m: Mind) -> None:
    await m.stand()
    await m.client.look(1.0, 0.0, 0.6)  # look up, toward where people's faces are
    await m.say(m.rng.choice(["greet", "inquire"]))
    await asyncio.sleep(3.0)


async def play(m: Mind) -> None:
    if m.ctx.friend_near():
        m.note("head-bobbing with a friend")
        for _ in range(8):
            await m.hold_head(0.15, 0.3)
            await m.hold_head(-0.1, 0.3)
        m.needs.on_played(1.0)
        return
    skills = [s for s in ("roulade", "kick_left", "kick_right") if s in m.client.skills]
    ok, why = m.gate.may_move(m.client, forward=True)
    if skills and ok:
        await m.stand()
        await m.client.do(m.rng.choice(skills))
        await asyncio.sleep(3.0)
        await m.say("wheee" if m.rng.random() < 0.5 else "chirp")
    else:
        await m.say("chirp")
    m.needs.on_played(0.5)


async def investigate(m: Mind) -> None:
    """Go and see the thing the other duck talked about."""
    found = m.rumour()
    if found is None:
        return
    sighting, mark = found
    m.note(f"going to look {sighting.describe()} for the {sighting.obj} "
           f"({sighting.told_by.title()} said so)")
    arrived = await m.go_to(mark.xy, budget_s=45.0)
    await m.client.look(0.6, 0.0, -0.1)  # peer at the floor around it
    await m.say("inquire")
    if not arrived:
        m.note(f"couldn't get to the {mark.name}")
    # Whether it is really there is decided by what the duck sees: the captioner (Phase 5) or
    # you, in the Pond, recording the sighting. Either credits the Duckdex entry to the teller.
    # Give the eyes (the captioner, or you in the Pond) a moment to report it.
    await asyncio.sleep(8.0)
    if sighting.obj in m.duckdex.entries:
        return  # found: record_sighting has credited the teller
    tries = m._checks.get(sighting.obj, 0) + (1 if arrived else 0.5)
    m._checks[sighting.obj] = tries
    if arrived:
        m.memory.gone(sighting.obj)
        m.note(f"the {sighting.obj} wasn't {sighting.describe()}")
        m.ctx.tell_friend("gone", sighting.obj, sighting.relation, sighting.landmark,
                          sighting.near)


async def come_here(m: Mind) -> None:
    """Walk to the Nest (in front of the TV stand, where people sit with Reachy)."""
    goal = m.ctx.nest_xy()
    if goal is None:
        await m.stand()
        await m.client.look(1.0, 0.0, 0.5)
        await m.say("greet")
        m.note("can't see the way to the Nest: greeted from where it is")
        return
    m.note("coming over")
    arrived = await m.go_to(goal, budget_s=45.0)
    await m.client.look(1.0, 0.0, 0.5)
    await m.say("greet" if arrived else "inquire")
    m.note("here!" if arrived else "couldn't get all the way")


async def show_something(m: Mind) -> None:
    """Stand still and look ahead, ready to be shown a thing. What it sees becomes a sighting
    (the captioner, or you in the Pond's "Saw & find" tab)."""
    await m.stand()
    await m.client.look(0.5, 0.0, 0.05)
    await m.say("inquire")
    m.note("waiting to be shown something")
    await m.hold_head(0.0, 12.0)


async def chatter(m: Mind) -> None:
    tags = m.tiredness.cues.sounds or ("chirp",)
    await m.say(m.rng.choice(tags))
    await asyncio.sleep(2.0)


BEHAVIOURS: dict[str, Callable[[Mind], Awaitable[None]]] = {
    "rest": rest, "nap": nap, "go_to_bed": go_to_bed, "look_around": look_around,
    "wander": wander, "seek_friend": seek_friend, "greet_friend": greet_friend,
    "call_out": call_out, "play": play, "chatter": chatter, "investigate": investigate,
    "come_here": come_here, "show_something": show_something,
}
