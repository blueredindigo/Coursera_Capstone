"""The Nest: wires the ducks, Reachy, the spine, memory and the duck bus together.

    python -m nest --config config.toml     # the real house
    python -m nest --sim                    # a simulated living room, no hardware

Loops, all on one asyncio event loop:

* one **transport** per duck, reconnecting forever;
* the **spine** at ~1 Hz: every mind ticks, the bus delivers what is in earshot;
* the **overseer**: Reachy watches whichever duck is doing something worth watching, and droops
  its antennas at a duck that is very tired;
* the **room eye**: Reachy's camera → duck detector → floor positions (when enabled);
* the **Pond**: a small web page on `:8090` with every duck's state and a few buttons.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path
from typing import Any

from .batteries import BatteryDiary
from .bus import DuckBus, Earshot
from .config import LANDMARK_WORDS, Config, DuckConfig
from .duck.client import DuckClient
from .duck.transport import LoopbackTransport, Transport, UnixSocketTransport, WebRtcTransport
from .reachy import ReachyGaze
from .spine.mind import BEHAVIOURS, Mind, go_to_bed
from .spine.personality import Personality
from .spine.safety import SafetyGate, SafetyLimits
from .spine.tiredness import Band, Thresholds
from .world.duckdex import Duckdex
from .world.messages import DuckMessage, Vocabulary
from .world.room import DuckTracker, FloorHomography, RoomMap
from .world.scene import Memory, Sighting

logger = logging.getLogger(__name__)

INTERESTING = {"wander", "play", "seek_friend", "greet_friend", "go_to_bed", "call_out"}


class Nest:
    def __init__(self, config: Config, sim: bool = False):
        self.config = config
        self.sim = sim
        self.data = Path(config.data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.world = None
        if sim:
            from .sim import SimWorld

            self.world = SimWorld()
            for i, duck in enumerate(config.ducks):
                self.world.add_duck(duck.name, 1.5 + 2.0 * i, 1.5 + 0.8 * i, yaw=0.6 * i,
                                    battery=80.0 - 45.0 * i)

        self.vocab = Vocabulary(config.vocabulary)
        self.room = RoomMap(reachy_xy=config.reachy.position, reachy_facing=config.reachy.facing,
                            landmarks=dict(config.landmarks))
        self.tracker = DuckTracker([d.name for d in config.ducks])
        self.homography = FloorHomography(config.reachy.homography)
        self.batteries = BatteryDiary(self.data / "batteries.json")
        self.ducks: dict[str, DuckClient] = {}
        self.minds: dict[str, Mind] = {}
        self.feed: list[dict[str, Any]] = []  # the Pond's event feed
        self.reachy: Any = None
        self.gaze: ReachyGaze | None = None
        self.reachy_status = "off"
        self.reachy_focus: str | None = None
        self._reachy_moved = 0.0
        self._tasks: list[asyncio.Task] = []

        limits = SafetyLimits(**config.safety)
        thresholds = Thresholds(**config.tiredness)
        for index, duck in enumerate(config.ducks):
            client = DuckClient(duck.name, self._transport(duck),
                                http_base=None if sim else f"http://{duck.host}:{duck.http_port}")
            memory = Memory(duck.name, self.data / f"{duck.name}-memory.json")
            for name, xy in config.landmarks.items():
                if not memory.knows(name):
                    memory.add_landmark(name, xy)
            mind = Mind(duck.name, client, Personality.from_seed(duck.name, duck.personality),
                        memory, Duckdex(duck.name, self.data / f"{duck.name}-duckdex.json"),
                        thresholds=thresholds, gate=SafetyGate(limits), walk_speed=duck.walk_speed)
            self.ducks[duck.name] = client
            self.minds[duck.name] = mind
        self._wire_minds()

        self.bus = DuckBus(self.vocab, Earshot(max_m=config.earshot_m), perform=self._perform,
                           on_delivered=self._delivered)
        self.bus.distance = lambda a, b: self.room.distance(a, b)
        self.bus.sees_a_duck = lambda name: self.ducks[name].sees_a_duck()

    # ── construction helpers ───────────────────────────────────────────────────

    def _transport(self, duck: DuckConfig) -> Transport:
        if self.sim:
            return LoopbackTransport(duck.name, self.world.handler(duck.name))
        if duck.transport == "unix":
            return UnixSocketTransport(duck.name, duck.socket)
        return WebRtcTransport(duck.name, duck.host, duck.signalling_port, duck.keep_video)

    def _wire_minds(self) -> None:
        names = list(self.minds)
        for name, mind in self.minds.items():
            friend = next((n for n in names if n != name), None)
            bed = next((d.bed for d in self.config.ducks if d.name == name), None)
            ctx = mind.ctx
            ctx.friend_name = friend
            ctx.on_event = self._event
            if friend:
                ctx.friend_connected = lambda f=friend: self.ducks[f].connected
                ctx.friend_near = lambda a=name, b=friend: self.bus_in_earshot(a, b)
                ctx.friend_room_xy = lambda f=friend: self.room.duck(f)
            ctx.my_room_xy = lambda n=name: self.room.duck(n)
            ctx.bed_xy = lambda b=bed: self.room.landmarks.get(b) if b else None
            ctx.nest_xy = self._nest_spot
            ctx.tell_friend = lambda *a, n=name, **k: self.tell_friend(n, *a, **k)

    def _nest_spot(self) -> tuple[float, float] | None:
        """Where "come here" leads: the `nest` landmark if you set one, else 0.8 m out in front
        of Reachy on the TV stand."""
        if "nest" in self.room.landmarks:
            return self.room.landmarks["nest"]
        if not self.config.reachy.enabled and not self.sim:
            return None
        (x, y), facing = self.room.reachy_xy, self.room.reachy_facing
        return x + 0.8 * math.cos(facing), y + 0.8 * math.sin(facing)

    def bus_in_earshot(self, a: str, b: str) -> bool:
        return self.bus.in_earshot(a, b) is not None if hasattr(self, "bus") else False

    def _event(self, duck: str, text: str) -> None:
        self.feed.append({"at": time.time(), "who": duck, "text": text})
        self.feed = self.feed[-100:]

    # ── running ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        for name, client in self.ducks.items():
            self._tasks.append(asyncio.ensure_future(client.transport.run()))
            self._tasks.append(asyncio.ensure_future(self._on_connect_loop(name)))
        await self._start_reachy()
        self._tasks.append(asyncio.ensure_future(self._spine_loop()))
        self._tasks.append(asyncio.ensure_future(self._overseer_loop()))
        if self.config.captioner.enabled and not self.sim:
            self._tasks.append(asyncio.ensure_future(self._eyes_loop()))
        if self.sim:
            self._tasks.append(asyncio.ensure_future(self._sim_loop()))
        elif self.config.reachy.camera:
            self._tasks.append(asyncio.ensure_future(self._room_eye_loop()))

    async def stop(self) -> None:
        for mind in self.minds.values():
            mind.interrupt()
        for client in self.ducks.values():
            await client.transport.stop()
        for task in self._tasks:
            task.cancel()
        if self.reachy is not None:
            await self.reachy.close()

    async def _on_connect_loop(self, name: str) -> None:
        client = self.ducks[name]
        while True:
            await client.transport.connected.wait()
            delay = 2.0
            while client.transport.connected.is_set():
                try:
                    await client.on_connected()
                    self._event(name, f"connected (API v{client.api_version})")
                    break
                except Exception as exc:
                    # Without robot.subscribe nothing streams and every walk is refused, so keep
                    # trying rather than idling on a half-open channel.
                    self._event(name, f"connected, but setup failed ({exc}); retrying")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)
            while client.transport.connected.is_set():
                await asyncio.sleep(0.5)
            self._event(name, "disconnected")

    async def _start_reachy(self) -> None:
        if self.sim:
            self.reachy = self.world.reachy
        elif self.config.reachy.enabled:
            from .reachy import ReachyClient

            self.reachy = ReachyClient(self.config.reachy.url)
        if self.reachy is None:
            return
        self.gaze = ReachyGaze(self.reachy)
        try:
            await self.reachy.wake_up()
            self.reachy_status = "awake"
        except Exception as exc:
            self.reachy_status = f"unreachable: {exc}"
            logger.warning("Reachy: %s", exc)

    async def _spine_loop(self) -> None:
        period = 1.0 / max(self.config.tick_hz, 0.1)
        last_sample = 0.0
        while True:
            for mind in self.minds.values():
                try:
                    await mind.tick()
                except Exception:
                    logger.exception("%s: tick failed", mind.name)
            try:
                await self.bus.pump()
            except Exception:
                logger.exception("bus pump failed")
            if time.time() - last_sample > 240:
                last_sample = time.time()
                for name, client in self.ducks.items():
                    self.batteries.sample(name, client.health.battery_percent)
            await asyncio.sleep(period)

    async def _overseer_loop(self) -> None:
        """Reachy watches the ducks: the tired one first, then whoever is up to something."""
        while True:
            await asyncio.sleep(2.0)
            if self.gaze is None or not self.reachy_status.startswith("awake"):
                continue
            focus, expression = self._choose_focus()
            now = time.monotonic()
            if focus == self.reachy_focus and now - self._reachy_moved < 6.0:
                continue
            xy = self.room.duck(focus) if focus else None
            try:
                if xy is not None:
                    await self.gaze.look_at_bearing(self.room.bearing_from_reachy(*xy),
                                                    antennas=expression, duration=1.2)
                elif focus is None and self.reachy_focus is not None:
                    await self.gaze.look_at_bearing(0.0, antennas="neutral", duration=1.5)
                self.reachy_focus = focus
                self._reachy_moved = now
            except Exception as exc:
                self.reachy_status = f"awake (last move failed: {exc})"

    def _choose_focus(self) -> tuple[str | None, str | None]:
        for name, mind in self.minds.items():
            if mind.tiredness.band == Band.VERY_LOW:
                return name, "droop"
        for name, mind in self.minds.items():
            if mind.tiredness.band == Band.CHARGING and mind.behaviour == "nap":
                continue
            if mind.behaviour in INTERESTING:
                return name, "curious" if mind.behaviour != "go_to_bed" else "tired_look"
        return None, None

    async def _sim_loop(self) -> None:
        while True:
            self.world.step(0.1)
            self.world.publish()
            for name, (x, y) in self.world.reachy.positions.items():
                self.room.set_duck(name, x, y)
            await asyncio.sleep(0.1)

    async def _room_eye_loop(self) -> None:
        """Reachy's camera → duck detector → floor positions → named tracks."""
        from .reachy import ReachyCamera
        from .vision.duck_detector import DuckDetector

        if not self.config.reachy.detector_model or not self.homography.ready:
            self._event("reachy", "room eye off: needs detector_model and a floor calibration")
            return
        loop = asyncio.get_running_loop()
        camera = await loop.run_in_executor(None, ReachyCamera)
        detector = await loop.run_in_executor(None, DuckDetector,
                                              self.config.reachy.detector_model,
                                              self.config.reachy.detector_threshold)
        while True:
            frame = await loop.run_in_executor(None, camera.frame)
            if frame is not None:
                boxes = await loop.run_in_executor(None, detector.detect, frame)
                points = [self.homography.to_floor(*b.foot) for b in boxes]
                self.tracker.update(points)
                for track in self.tracker.tracks:
                    if track.name:
                        self.room.set_duck(track.name, track.x, track.y, at=track.at)
            await asyncio.sleep(0.33)

    async def _eyes_loop(self) -> None:
        """While a duck is looking around, ask the local vision model what it sees."""
        from .vision.captioner import Captioner

        cfg = self.config.captioner
        captioner = Captioner(cfg.url, cfg.model)
        last: dict[str, float] = {}
        try:
            while True:
                await asyncio.sleep(2.0)
                for name, mind in self.minds.items():
                    if mind.behaviour not in ("look_around", "investigate", "wander"):
                        continue
                    if not mind.client.connected or time.monotonic() - last.get(name, 0) < \
                            cfg.every_s:
                        continue
                    last[name] = time.monotonic()
                    try:
                        png = await mind.client.frame_png()
                        seen = await captioner.describe(png, sorted(mind.memory.landmarks))
                    except Exception as exc:
                        logger.info("%s: captioning failed: %s", name, exc)
                        continue
                    for item in seen:
                        if item["confidence"] >= cfg.min_confidence:
                            self.record_sighting(name, item["object"], item["relation"],
                                                 item["landmark"], item["near"],
                                                 confidence=item["confidence"])
        finally:
            await captioner.close()

    async def roll_call(self, mover: str) -> bool:
        """Tell the two ducks apart in Reachy's view: `mover` takes one small step."""
        snapshot = self.tracker.snapshot()
        mind = self.minds[mover]
        ok, why = mind.gate.may_move(mind.client)
        if not ok:
            self._event(mover, f"roll call skipped: {why}")
            return False
        await mind.client.walk_for(0.1, 0.0, 0.0, 1.5)
        await asyncio.sleep(1.0)
        resolved = self.tracker.roll_call(mover, snapshot)
        self._event("reachy", f"roll call {'worked' if resolved else 'was inconclusive'}")
        return resolved

    # ── the duck bus ───────────────────────────────────────────────────────────

    def sender_index(self, name: str) -> int:
        return list(self.minds).index(name)

    def tell_friend(self, speaker: str, kind: str, obj: str, relation: str = "near",
                    landmark: str | None = None, near: str | None = None,
                    confidence: float = 1.0) -> str | None:
        listener = self.minds[speaker].ctx.friend_name
        if listener is None:
            return None
        message = DuckMessage(kind, self.sender_index(speaker), self.bus.next_seq(speaker), obj,
                              relation, landmark, near, confidence)
        self.bus.say(speaker, listener, message)
        return message.subtitle()

    async def _perform(self, speaker: str, listener: str, message: DuckMessage) -> None:
        """Turn to face the listener, chirp the phrase, and let the listener answer."""
        talker, hearer = self.minds[speaker], self.minds[listener]
        talker.interrupt()
        sighting = talker.client.sighting
        if sighting and sighting.boxes and talker.client.sees_a_duck():
            bearing = sighting.bearing(sighting.boxes[0])
            await talker.client.look(math.cos(bearing), math.sin(bearing), 0.0)
        for tag in message.chirp_phrase():
            await talker.say(tag)
            await asyncio.sleep(0.6)
        await hearer.say("inquire" if message.kind == "ask" else "greet")

    def _delivered(self, delivery) -> None:
        listener = self.minds[delivery.listener]
        m = delivery.message
        self._event(delivery.speaker, f"told {delivery.listener.title()}: “{m.subtitle()}” "
                                      f"({delivery.size_bytes} bytes; {delivery.evidence})")
        if m.kind == "found" and m.landmark:
            known = listener.memory.where_is(m.obj)
            listener.memory.heard(Sighting(obj=m.obj, relation=m.relation, landmark=m.landmark,
                                           near=m.near, seen_by=delivery.speaker,
                                           confidence=m.confidence), delivery.speaker)
            if known is None:
                listener.needs.stir("curiosity", 0.3)  # it wants to go and see
        elif m.kind == "gone":
            listener.memory.gone(m.obj)
        elif m.kind == "ask":
            where = listener.memory.where_is(m.obj)
            if where:
                self.tell_friend(delivery.listener, "found", m.obj, where.relation,
                                 where.landmark, where.near, where.confidence)
            else:
                self.tell_friend(delivery.listener, "unknown", m.obj)

    # ── things the Pond (or Home Assistant) can ask for ────────────────────────

    def record_sighting(self, duck: str, obj: str, relation: str, landmark: str,
                        near: str | None = None, tell: bool = True,
                        confidence: float = 1.0) -> dict[str, Any]:
        """A duck saw something: into its memory, into its Duckdex, and (maybe) to its friend.

        Called by the Pond today; by the vision captioner once Phase 5's model is running."""
        mind = self.minds[duck]
        rumour = mind.memory.where_is(obj)
        credited = rumour.told_by if rumour and rumour.told_by else None
        sighting = mind.memory.saw(obj, relation, landmark, near, confidence=confidence)
        meal = mind.duckdex.feed(obj, credited_to=credited)
        if credited and meal == "meal":
            self._event(duck, f"found the {obj}, just where {credited.title()} said!")
            mind.needs.on_friend_near(20.0)  # a good feeling about its friend
        mind.needs.on_fed(meal)
        self._event(duck, f"saw a {obj} {sighting.describe()} ({meal})")
        told = None
        if tell and meal == "meal" and not credited:
            told = self.tell_friend(duck, "found", obj, relation, landmark, near, confidence)
        return {"meal": meal, "where": sighting.describe(), "queued_for_friend": told}

    def where_is(self, duck: str, obj: str) -> dict[str, Any]:
        sighting = self.minds[duck].memory.where_is(obj)
        if sighting is None:
            return {"known": False}
        return {"known": True, "where": sighting.describe(), "seen_by": sighting.seen_by,
                "told_by": sighting.told_by, "confidence": sighting.confidence,
                "at": sighting.at}

    async def call(self, duck: str) -> None:
        mind = self.minds[duck]
        mind.interrupt()
        await mind.stand()
        await mind.client.look(1.0, 0.0, 0.5)
        await mind.say("greet")
        if self.gaze and self.room.duck(duck):
            await self._reachy_try("look", self.gaze.look_at_bearing(
                self.room.bearing_from_reachy(*self.room.duck(duck)), antennas="perk"))

    ACTIONS = {
        "come_here": ("come_here", "{duck} is coming over."),
        "look_at_me": (None, "{duck} looked up and said hello."),
        "play": ("play", "{duck} is playing."),
        "find_friend": ("seek_friend", "{duck} is off to find {friend}."),
        "show_something": ("show_something", "Hold the thing in front of {duck}'s beak."),
        "go_to_bed": ("go_to_bed", "{duck} is heading to bed."),
    }

    async def direct(self, duck: str, action: str) -> tuple[bool, str]:
        """Something you asked a duck to do from the Pond. (accepted, what to tell you)

        Your request replaces whatever the duck was doing. Its safety still applies: a walk the
        gate refuses turns into a turn-away or a greeting, and the reason shows in the Pond."""
        mind = self.minds[duck]
        name = duck.title()
        friend = (mind.ctx.friend_name or "its friend").title()
        if action not in self.ACTIONS:
            return False, f"Unknown action {action!r}."
        if not mind.client.connected:
            return False, f"{name} isn't connected right now."
        if mind.paused:
            return False, f"{name} is paused. Tap Resume first."
        if mind.asleep and action != "go_to_bed":
            return False, f"{name} is asleep. Say good morning first."
        behaviour, message = self.ACTIONS[action]
        if behaviour is None:
            await self.call(duck)
        else:
            mind.interrupt()
            mind._start(behaviour, BEHAVIOURS[behaviour](mind))
        self._event(duck, f"you asked: {action.replace('_', ' ')}")
        return True, message.format(duck=name, friend=friend)

    async def bedtime(self) -> None:
        """Reachy looks at each duck in turn, wiggles goodnight, then sleeps itself."""
        for name, mind in self.minds.items():
            mind.interrupt()
            mind.asleep = True
            if mind.tiredness.band != Band.CHARGING:
                mind._start("go_to_bed", go_to_bed(mind))
        for name in self.minds:
            xy = self.room.duck(name)
            if self.gaze and xy:
                await self._reachy_try("goodnight", self.gaze.look_at_bearing(
                    self.room.bearing_from_reachy(*xy)))
                await self._reachy_try("goodnight", self.gaze.wiggle(1))
        if self.reachy and await self._reachy_try("sleep", self.reachy.goto_sleep()):
            self.reachy_status = "asleep"
        self._event("reachy", "bedtime: goodnight, Ah-Ah and Tee-Tee")

    async def _reachy_try(self, what: str, coroutine) -> bool:
        """Reachy is a nice extra, never a dependency: a failed move is logged, not raised."""
        try:
            await coroutine
            return True
        except Exception as exc:
            self.reachy_status = f"{self.reachy_status.split(' ')[0]} (last {what} failed: {exc})"
            return False

    async def wake(self) -> None:
        for mind in self.minds.values():
            mind.asleep = False
            mind.interrupt()
        if self.reachy and await self._reachy_try("wake-up", self.reachy.wake_up()):
            self.reachy_status = "awake"
        best = max(self.minds.values(), key=lambda m: m.tiredness.percent or 0)
        await best.stand()
        await best.say("greet")
        self._event(best.name, "first up: good morning")

    def status(self) -> dict[str, Any]:
        ducks = {}
        for name, mind in self.minds.items():
            client = mind.client
            ducks[name] = {
                "connected": client.connected,
                "transport": client.transport.status,
                "battery_percent": client.health.battery_percent,
                "hottest": [client.health.hottest_joint, client.health.hottest_c],
                "tiredness": mind.tiredness.band.value,
                "mood": mind.needs.mood,
                "needs": mind.needs.as_dict(),
                "personality": mind.personality.describe(),
                "behaviour": mind.behaviour,
                "paused": mind.paused,
                "fallen": client.fallen,
                "sees_a_duck": client.sees_a_duck(),
                "room_xy": self.room.duck(name),
                "pack": self.batteries.fitted.get(name),
                "duckdex": len(mind.duckdex),
                "knows": mind.memory.things_seen(),
                "safety": mind.gate.last_refusal,
                "asleep": mind.asleep,
                "just_told": any(d.speaker == name and time.time() - d.at < 8
                                 for d in self.bus.log[-5:]),
                "events": [{"at": t, "text": x} for t, x in list(mind.events)[-8:]],
            }
        return {"ducks": ducks, "reachy": {"status": self.reachy_status,
                                           "watching": self.reachy_focus},
                "messages": self.bus.recent(), "pending_messages": len(self.bus.pending),
                "batteries": self.batteries.health(), "feed": self.feed[-30:],
                "landmarks": sorted(self.room.landmarks), "sim": self.sim,
                "things": [w for w in self.vocab.words
                           if w not in self.room.landmarks and w not in LANDMARK_WORDS],
                "uptime_s": round(time.time() - self.started)}
