# Pollen Robotics: Microduck Olares Integration

Two [Microducks](https://github.com/pollen-robotics/microduck), **Ah-Ah** and **Tee-Tee**, a
[Reachy Mini](https://github.com/pollen-robotics/reachy_mini) keeping watch from the TV stand, a
Jetson Orin Nano Super, and an [Olares](https://github.com/beclab/olares) box. Together they make
a pair of curious, tamagotchi-like creatures that explore your home, find things, and tell each
other about them.

> **The premise in one line:** the ducks keep their reflexes, Reachy Mini is the eyes and ears
> of the room, the Jetson is the always-on mind, and Olares does the heavy thinking by day.
> **Everything stays on the local network.**

> **Status (26 Sep 2026):** the software for phases 0–3 is built, with groundwork for phases
> 4–6. It lives in [`nest/`](nest/): the Nest runs on the Jetson, talks to both ducks over
> their own LAN WebRTC control channel and to Reachy Lite over its local API, and has the needs,
> tiredness gauge, safety gate, landmark memory, Duckdex and duck bus. It is tested in a
> simulated living room and against a stand-in for the duck's WebRTC server, **not yet on the
> real robots**. Start with [`nest/SETUP.md`](nest/SETUP.md).

Ground rules:

1. **The robot's own software has the final say on movement.** Anything off-board proposes
   intent. The duck's controller and its safety checks decide how to move, or whether to.
2. **LAN only.** No cloud rendezvous, no relay, no hosted models. If the internet goes down,
   nothing about Ah-Ah and Tee-Tee changes. The one optional exception is the kit's Hugging
   Face credit for training on *simulated* data (§11). Nothing from inside the home goes up.
3. **No simulated age or handicaps.** They always use the best gait available at full speed,
   and grow in **knowledge and skills**. The long-term aim is for them to run.
4. **Tiredness is a battery gauge you can see.** A duck that's low on battery looks tired, so
   you know to charge it without opening an app.
5. **Quiet at night.** Olares can idle. The always-on work runs on the silent Jetson.
6. **Foundation first.** Human speech (talking to the ducks, the ducks talking to you) is
   **parked** until the foundation below works well (§13).

---

## 1. What you're working with

### The Microducks (what the repo actually exposes)

A 25 cm, 800 g biped with an RK3566 (Radxa Zero 3) and a 0.8 TOPS NPU. It runs Rust daemons
that talk one **JSON-RPC** contract, the same calls whether they come from `robotctl`, the phone
app, a gamepad or your own script.

| Sense / act | How you reach it | Pet use |
|---|---|---|
| Camera | WebRTC H.264 (`:8443` signalling), PNG snapshot `GET http://<duck>:8080/frame` | the duck's own view, for captions |
| 8×8 ToF depth + hand tracker | `tof.frame`, `tof.stream` | "a hand is near me", obstacles |
| Petting detector (mic, on-board CNN) | `pet-detect`, runs inside `robotd` and coos | affection meter |
| Duck detector (NPU, finds *other Microducks*) | `[duck_detector] enabled` | Ah-Ah and Tee-Tee can **see each other** |
| BLE chorale beacon: stable duck id, RSSI, ~245 spare bytes | `chorale.heard`, `chorale.beacon` | who's nearby, how close, a radio link between ducks |
| Voice synth with a **per-robot personality seed** | `robot.sound` with tags `chirp greet coo inquire peck tock alarm wheee` | each duck sounds like a different creature |
| Gaze, head, mouth | `robot.look` (point in space), `robot.head`, `robot.mouth` | attention, curiosity, expressions |
| Skills (ONNX policies) | `robot.do`, `robotctl policy add/load` | walk, sit, kick, roulade, ground-pick, get up, plus anything you train |
| Locomotion | `robot.move`, `robot.mode` (walk vs roller) | wandering, following, fetching |
| **NFC, two antennas: head and beak** (per Pollen's press kit; no software interface in the public repo yet) | awaiting a Pollen interface | the beak "sniffs" tagged objects, the head reads cards you hold to it (§11) |
| Odometry (legs + IMU) | `odometry` crate, fed to `robotd` | short-range "where did I walk" |
| State and battery | `robot.state` (battery, thermals, loop health), `robot.subscribe` | the tiredness gauge, self-monitoring |

Things from the repo that shape the plan:

- **The on-robot "brain" isn't ported yet.** `docs/ideas/autonomous_behavior.md` lists a
  16-state machine (Chill, LookAround, Wander, Zoomies, Nap, Dance, BallPlay, Petted…) with an
  energy/mood model that isn't in the current daemons. We build the mind off-board first.
- **Odometry drifts, and its origin is "wherever the duck faced at boot"** (no magnetometer).
  A duck can't reliably say "the ball is at x=2.3, y=1.1", but it can say "behind the green
  chair near the sofa". Reachy Mini's fixed view of the room helps fix that (§5).
- **A duck can only play its built-in voice tags today** (`robot.sound` takes a tag and nothing
  else), and **its mic is held by `robotd`'s petting worker**. Both matter for the acoustic
  stretch goal (§6).

### Reachy Mini, the room's lighthouse

It doesn't walk, but from the TV stand it sees and hears the whole living room, and it can
**play and record any audio**. Its SDK and daemon work over the LAN:

| Capability | How | Role in this project |
|---|---|---|
| Camera | `mini.media.get_frame()` | a fixed, **drift-free view of the room**: where each duck is, where the furniture is |
| Head (6 DOF) + body rotation | `goto_target`, `look_at_image`, `look_at_world` | turns to follow the ducks, so you can *see* what it's watching |
| Antennas | motors, also usable as buttons | expressions, and "Ah-Ah is tired" signals |
| Mic array with **direction of arrival** | `GET http://<reachy>:8000/api/state/doa` | *which way* a duck chirp came from |
| Speaker, raw audio in and out | `play_sound`, `push_audio_sample`, `get_audio_sample` | the first node that can do data-over-sound today (§6) |
| REST + WebSocket API | `http://<reachy>:8000/api`, Swagger at `/docs` | the Jetson drives it without running code on the robot |

**Yours is the Lite**, so it has no computer of its own: it's a USB-C device (120° 12 MP
camera, a 4-mic XVF3800 array with direction of arrival, a 5 W speaker) powered from its own
supply. **Plug it into the Jetson, which becomes its brain**, and put the Jetson on the TV stand
beside it. Its docs name Jetson among the ARM64 systems where the desktop app may not work, so
install the **Python SDK** directly on the Jetson (`pip install reachy-mini` plus GStreamer, per
its installation guide) and run the daemon there. Everything then stays on one machine, with no
Wi-Fi hop for Reachy's camera or audio, and `localhost:8000` is its API.

The TV stand becomes **the Nest**: Reachy, the Jetson, and both ducks' chargers and "beds".

### The Jetson Orin Nano Super, the always-on mind

8 GB, ~67 TOPS, JetPack 6 (Ubuntu 22.04), 7–25 W, near-silent. That's enough to run most of this
project **without Olares**:

- the **spine**: needs, mood and the behavior picker for both ducks, at ~1 Hz;
- **the duck detector on Reachy Mini's camera** (the duck-detect model also ships as ONNX),
  plus a small YOLO for furniture and objects, both in real time;
- a **small vision-language model** (3B-class, e.g. Qwen2.5-VL-3B or moondream) for captions
  like "a yellow ball behind a green chair";
- the **duck bus** (messages between ducks), the **landmark map**, and the **safety
  supervisor**;
- **ggwave** encode/decode for the acoustic stretch goal.

Run it as a plain Docker host on the LAN, not as an Olares node, so it keeps going when Olares
is asleep or rebooting.

### Olares, the daytime heavy lifter

Now that the Jetson covers the always-on work, Olares does only the **heavy, occasional** jobs:
bigger vision models for hard captions, diaries and summaries, image generation, **policy
training** (§7), long-term storage and backups, and the Pond dashboard on your phone through
LarePass.

**Quiet at night:**

- **Noctua fans** (e.g. NF-A12x25 / NF-A14) on a sensible curve make an idle box near silent.
- **Cap the GPU power** (`nvidia-smi -pl <watts>`, around 60–70% of stock). The GPU under load
  is usually the loud part.
- **Train while you're out.** Home Assistant (Olares Market) sees your phones leave the home
  Wi-Fi, which is LAN-only presence. At night Olares just idles, and nothing depends on it.

---

## 2. Architecture

```
┌──────────── Olares (daytime, heavy, can idle) ──────────────┐
│ big VLM · diaries · image gen · microduck_rl training on GPU │
│ long-term memory & backups · Pond dashboard (LarePass)        │
└───────────────────────────────▲──────────────────────────────┘
                                │ LAN HTTP, only when awake
┌──────── Jetson Orin Nano Super (always on, silent) ──────────┐
│ spine: needs/mood/behavior for Ah-Ah & Tee-Tee                │
│ duck detector + object detector + small VLM                   │
│ landmark map · duck bus (earshot rule) · safety supervisor   │
│ ggwave encode/decode                                          │
└───▲──────────────────────────▲───────────────────────────▲───┘
    │ USB-C (Lite, localhost)   │ JSON-RPC + /frame          │ JSON-RPC + /frame
┌───┴──────── Reachy Mini ──┐ ┌─┴────────── Ah-Ah ──────┐ ┌──┴───────── Tee-Tee ─────┐
│ room camera · mic array   │ │ robotd 50 Hz · safety · │ │ robotd 50 Hz · safety ·  │
│ + direction of arrival ·  │ │ pet-detect · tofd ·     │ │ pet-detect · tofd ·      │
│ speaker · head & body     │ │ duck-detect · voice     │ │ duck-detect · voice      │
└───────────────────────────┘ └────────────▲────────────┘ └────────────▲─────────────┘
                                           └──── BLE beacon + RSSI ────┘
```

**Three speeds, three places:**

1. **Reflexes (on the ducks, milliseconds).** Balance, get-up, petting coo, obstacle stop. These
   never wait on the network, and **they always win**.
2. **Spine (Jetson, ~1 Hz).** Needs and mood pick the next behavior: `robot.do`, `robot.look`,
   `robot.sound`, `robot.move`. Reachy Mini's view says where everyone is. This works with
   Olares off.
3. **Soul (Jetson for small models, Olares for big ones, every 30 s to minutes).** Captions,
   memory, goals ("go look behind the green chair"). It never drives motors directly.

---

## 3. Reachy Mini's jobs

Reachy Mini is a real part of the household, not only a camera.

- **The overseer's gaze.** It turns its head to follow whichever duck is doing something
  interesting, so you can tell at a glance what's happening. When the ducks meet, it looks from
  one to the other.
- **The map-keeper.** Its fixed view gives the Jetson a drift-free map of the living room:
  furniture, doorways, and each duck's position. Each duck's odometry gets corrected whenever
  Reachy can see it.
- **The referee.** In hide-and-seek, fetch and treasure hunts it watches, confirms who found
  it first, and celebrates with its antennas.
- **The battery watcher.** When a duck is tired, Reachy looks at it and droops its antennas,
  and when the duck is on the charger it perks up. You get two visual cues from across the room.
- **The listener.** Its mic array can tell *which direction* a duck chirp came from, which
  helps find a duck that's out of sight behind the sofa.
- **The first ggwave speaker.** It can already play and record raw audio, so the acoustic
  channel can be prototyped between Reachy and the Jetson before the ducks can do it (§6).
- **A different species.** It isn't a duck and shouldn't act like one. It's the calm older
  presence: curious about the ducks, occasionally amused, and it never takes part in their
  gossip.

---

## 4. The tamagotchi core

Needs come from **real sensors**, so looking after them is a physical act.

| Need | Rises when | Satisfied by | What you see |
|---|---|---|---|
| **Energy (tiredness)** | battery drains (from `robot.state`), long activity | **the charger**, a nap | see the tiredness gauge below |
| **Curiosity / "hunger"** | time since it saw something *new* | new things to look at, exploring | `inquire` chirps, looks around, wanders |
| **Affection** | time since last petting | head scratches (`pet-detect`), a hand near its face (ToF) | follows you, `greet` when you walk in |
| **Social** | time away from the other duck | meeting up (BLE RSSI + duck detector + Reachy's view) | calls out, rushes over on approach |
| **Play** | long calm periods | a ball, a game, the other duck | Zoomies, kicks, roulades, chases |

### Tiredness is the battery gauge

Energy is mostly the battery level, so tiredness is **a reading you can see**. It changes
*what* the duck does and how it looks, never *how well* it walks.

| Battery | How the duck looks | What it does |
|---|---|---|
| High | bright, head up, quick `chirp`s | explores, plays, starts games |
| Medium | normal | normal needs-driven life |
| Low | slower head movements, yawning `coo`, head dips between activities | stops starting long games, prefers short trips, sits more often |
| Very low | sits down, head low, occasional sleepy `coo` | stops exploring, **goes to its "bed" spot** near the charger, and Reachy droops its antennas looking at it |
| Charging | naps, eyes-closed head pose, tiny content sounds | wakes refreshed once charged |

Thermal warnings in `robot.state` can use the same cue ("tired, needs a rest") so hot servos
get a break too. The battery thresholds are yours to tune, and the Pond shows the real
percentage for when you want the number.

### Curiosity is food

The ducks **eat novelty**. Put something in front of Ah-Ah and it looks (`robot.look`), the
Jetson grabs a `/frame`, and the vision model names it ("a yellow tennis ball"). Something new
is a full meal: a happy `wheee`, a little dance, and a new entry in Ah-Ah's **Duckdex**, a photo
album from its own eyes. Something it's seen before is a snack, and the same thing ten times is
boring. Each duck keeps its **own** Duckdex, which is what makes telling each other worthwhile.

### Growing up means learning, not aging

There are no life stages and no speed caps. They grow in:

- **Knowledge:** a bigger Duckdex, a richer map of the home, memories of you.
- **Skills:** new policies from the dojo (§7), aiming at running.
- **Relationships:** a friendship score between them, and with you.

---

## 5. "I found a yellow ball, behind the green chair near the sofa"

### Each duck builds a landmark map

A **scene graph** of landmarks and relations, not coordinates:

```
[sofa] ──near── [green chair] ──behind── (yellow ball)   seen by Ah-Ah, 14:32, photo #412
   │
  left-of
   │
[window] ──under── [radiator]
```

- **Landmarks** are big things that don't move: sofa, chairs, table, doorways. Reachy Mini's
  view of the living room gives them fixed positions, and you can name them in the Pond
  ("that's Grandma's chair").
- **Objects** are things that move: balls, socks, keys, the cat. Each sighting stores the
  nearest landmarks, the relation the vision model reports (`behind`, `under`, `on`,
  `next to`), the photo and the time.
- **Odometry** fills in short hops, and resets its drift whenever Reachy sees the duck or the
  duck recognises a landmark.
- **Rooms Reachy can't see** rely on landmarks the ducks recognise themselves. It's less exact,
  and that's fine: that's where the ducks get to be genuinely exploratory.

### Telling the other duck

1. Ah-Ah finds the ball. Its caption plus landmark lookup produces a small message:
   `{found: "yellow ball", rel: "behind", landmark: "green chair", near: "sofa", conf: 0.8}`.
2. If Tee-Tee is **in earshot** (§6), Ah-Ah turns toward it (the duck detector gives the
   bearing), does an excited chirp phrase, and the message is delivered. Reachy looks between
   them. The Pond shows what was said.
3. Tee-Tee's curiosity now **targets the ball**. It resolves "green chair" in *its own* map,
   walks there, looks behind it, and either confirms (a Duckdex entry credited "told by Ah-Ah")
   or reports that it's gone (the memory is marked stale).
4. If Tee-Tee doesn't know the green chair, it does an `inquire` chirp and Ah-Ah leads the way.

### Games on the same machinery

- **Treasure hunt.** You hide a toy. The first duck to find it tells the other, they race to
  it, and Reachy referees.
- **Tidy-up scouting.** In the evening, both ducks tour the rooms and report "things out of
  place" compared with the morning, shown in the Pond.
- **Rumours.** Second-hand information carries lower confidence, so the ducks can be wrong, go
  check, and "argue" about who was right.
- **Where's the duck?** Ah-Ah hides out of Reachy's view. Tee-Tee searches with BLE hot/cold,
  and Reachy turns toward any chirp it hears.

---

## 6. How Ah-Ah and Tee-Tee talk to each other

The ducks don't need English to talk. They need to **exchange meaning** and **look like
they're talking**.

### Foundation: meaning by radio, the voice as performance

- **Meaning** goes over the Jetson's duck bus on the LAN, or directly in the ~245 spare bytes of
  the BLE beacon. The ball message compresses to a few dozen bytes using IDs for object,
  relation and landmark. The BLE path works even if the Jetson is down.
- **Earshot rule:** a message is delivered **only if the ducks could plausibly hear each
  other**: BLE RSSI above a threshold, ideally the duck detector or Reachy's view confirming
  they're in the same space. If Tee-Tee is in another room, it finds out when they meet, so
  gossip spreads physically.
- **Performance:** the speaker turns toward the listener, plays a chirp phrase whose length and
  rhythm follow the message, and the listener answers (`inquire` for a question, `greet` or
  `wheee` for "great!", `peck` for "not interested").

### Stretch goal: messages as real sound (ggwave)

Here the message itself is sent as sound. [ggwave](https://github.com/ggerganov/ggwave)
encodes a short payload (up to ~140 bytes) as R2-D2-style tones that another device's mic
decodes. "In earshot" becomes literally true, and you'd hear them talking.

**Step 1: prove it with Reachy Mini (works today).** Reachy can play and record raw audio, so:
- the Jetson encodes a message and Reachy plays it with `push_audio_sample`;
- a USB mic on the Jetson (or Reachy's own mic array via `get_audio_sample`) decodes it;
- measure reliability across the living room, with the TV on, and with a duck walking nearby.
  This tells you which ggwave protocol, volume and payload size actually work in *your* room
  before touching the ducks.

**Step 2: ducks listen.** Share the duck's mic between the petting worker and a small forwarder
using ALSA `dsnoop`, then stream 16 kHz audio to the Jetson, which decodes. Now Reachy can
"announce" to the ducks ("Tee-Tee, Ah-Ah is behind the sofa"), and Reachy's direction-of-arrival
tells the Jetson where a sound came from.

**Step 3: ducks speak.** The duck's speaker needs raw playback. That's an upstream addition to
the Microduck software: a `robot.play` RPC, or an ALSA path alongside the voice. Then Ah-Ah
chirps the ball message and Tee-Tee decodes it through its own ears.

**Make it feel like a duck:**
- Wrap the data in a duck-flavoured frame: a `chirp` before and a `tock` after, so it sounds
  like a sentence.
- Only send data chirps while **both ducks are still**. Walking is loud, and the motors will
  swamp the mic.
- Keep the radio path as the fallback and the source of truth. The acoustic version is the one
  you hear, and if it fails, the radio copy still delivers, so the game never breaks.
- A fun side effect: Reachy overhears everything, so it can "react" to gossip it wasn't meant
  to hear.

---

## 7. The dojo: learning to move better, and to run

1. **Always run the best official gait.** `robotctl policy check` / `update` keeps both ducks on
   Pollen's latest policies. Never cap speed for "personality".
2. **Train on Olares** with [microduck_rl](https://github.com/pollen-robotics/microduck_rl)
   (MuJoCo + PPO) on the GPU, while you're out, at a capped power limit. Candidate skills:
   faster walk, trot, **run**, turning in place, stepping over a threshold, better recovery.
3. **Test in simulation first.** `scripts/duck-sim` runs the *real daemons* against the
   simulated body. A new policy has to pass a test battery (flat, carpet friction, pushes,
   slopes, start/stop) before it ever touches a real duck.
4. **Self-trials in a test pen** in Reachy Mini's view: a marked safe area with a soft floor and
   no edges or stairs, in short bursts. The Jetson records falls, thermals, loop health, and
   **true speed from Reachy's camera** (better than odometry). The duck's own get-up and safety
   handling stays in charge throughout.
5. **Keep or roll back automatically.** Better, and no worse on falls or heat: keep it.
   Otherwise `robotctl policy reset`, and a diary line: "Tried running today. Fell twice. Not
   yet."
6. **Teach the other one.** When Ah-Ah graduates a skill, Tee-Tee gets it after watching Ah-Ah
   do it (the duck detector confirms). Tee-Tee then does its own test-pen trial, since every
   body is a little different.

Running is genuinely hard for a 25 cm biped. Sim-to-real gaps are real, falls wear servos, and
"run" may first look like a fast shuffle or a tiny hop. The pen, short bursts and automatic
rollback are what make it safe to keep trying.

---

## 8. More ideas for the pair

- **Recognition and friendship.** They face each other and greet in a way that depends on
  their friendship score, which grows with good interactions.
- **Personalities from seeds.** Each duck's voice seed also rolls temperament traits (bold↔shy,
  chatty↔quiet). Maybe Ah-Ah is the explorer and Tee-Tee the one who asks questions.
- **Spontaneous duets** on the shared BLE beat, rarely and only when both are happy and
  together. Reachy sways along.
- **Follow the leader / conga**, **separation anxiety** and big reunions.
- **A shared dialect.** Their chirp phrases for landmarks and objects drift as they gossip, so
  in a month they have "words" you didn't design.
- **Door greeting.** Home Assistant sees your phone on the home Wi-Fi. Reachy turns to the door,
  and the less tired duck goes to meet you.
- **The Pond dashboard** on Olares through LarePass: needs bars (with the real battery %),
  Reachy's room view, both Duckdexes, the landmark map with "last seen" pins, and a log of what
  the ducks told each other.

---

## 9. Field trips: exploring outside and coming home to tell

**Yes, it's possible**, as *supervised outings* with you, not as a duck roaming the
neighbourhood alone. It stays LAN-only: outside there's no internet involved at all. The duck
records its trip, and when it's back on the home Wi-Fi it hands everything over. Olares
processes it, and the whole household hears about it.

### Why it has to be supervised

- **Terrain.** Its policies are trained for indoor floors. Grass, gravel, kerbs and slopes are
  hard for a 25 cm biped. Roller mode (`robot.mode`, wheels on) is the better choice on smooth
  pavement.
- **Its brain stays home.** The spine runs on the Jetson, so outside the duck only has its
  on-board reflexes. **The gamepad still works**: it pairs straight to the duck over Bluetooth
  with no network, so you can always take over.
- **The world.** No waterproofing, dust and grit in the servos, heat and cold on the battery,
  dogs, bikes, traffic, and people who'd happily pocket a cute robot.
- **Privacy.** It will photograph strangers and their houses. Keep the footage local, blur
  faces when it's processed, and don't publish it.

### Two ways to do it

**Tier 1, "Walkies" (start here).** You walk it or drive it with the gamepad, and carry it
across anything risky. A small recorder on the duck saves a frame every few seconds plus
odometry, IMU, ToF and fall events to its SD card. `mediad` already serves frames on a local
socket (`media.frame` on `/run/mediad/media.sock`) for exactly this kind of local recorder.
Install the recorder as its own service, and check it survives a Microduck update, since the
updater only owns its own daemons.

**Tier 2, "Expedition kit" (later).** Take the duck's home network with you in a small bag:
- a travel router broadcasting a Wi-Fi network the duck knows
  (`sudo robotctl net connect <ssid>` once at home);
- the Jetson Orin Nano Super on a power bank with the right DC or USB-C PD output;
- optionally a USB GPS dongle on the Jetson.

Now the duck has its whole mind with it outside: detection, captions and the spine, still
without the internet. At home, the Olares box runs a **backup copy of the spine** (it's just a
container) so the stay-home duck keeps its needs and moods. Reachy Lite has no computer while
the Jetson is out, so it "waits by the door" in its sleep pose. That's thematically perfect.

### Coming home

1. **Homecoming.** The traveller rejoins the home Wi-Fi. Reachy wakes, turns to the door and
   raises its antennas. The stay-home duck, whose social need has been climbing, rushes over
   to greet it.
2. **Unpacking.** The trip log goes to the Jetson, and then to Olares when it's awake. The big
   VLM captions the frames, drops duplicates, blurs faces and picks out **wonders**: things no
   duck has seen before, like a snail, a pinecone, a puddle or a big dog.
3. **The Wild Duckdex.** A new section of the collection, only reachable by going outside. If
   there's GPS (from the kit, or a GPX export from your phone, merged by timestamp locally), the
   wonders are pinned on a map of your walks.
4. **Telling.** The traveller tells the other duck about its best finds, over the duck bus or
   ggwave: "I saw a big dog by the red gate." The stay-home duck can't go and check, so its
   curiosity turns into **wanderlust**, a new need that makes it the natural pick for the next
   outing. Taking turns comes out of their needs, not a rota.
5. **Slideshow night.** The Jetson sits on the TV stand, so it can drive the TV (DisplayPort to
   HDMI). It shows the trip's best "postcards" while Reachy turns between the screen and the
   ducks, and the traveller gets excited when it recognises its own photos.
6. **Learning from it.** The fall log feeds the dojo: "slipped on gravel three times" becomes a
   gravel-terrain training run in MuJoCo, with the usual simulation, test pen and rollback.
   Adventurous ducks slowly get better outdoors, and a well-travelled duck's personality drifts
   bolder.
7. **Souvenirs (for fun, maybe).** The duck has a ground-pick skill with its beak. It might
   manage to bring home a leaf. It might not.

### Outing rules

Dry weather, moderate temperatures, soft or smooth ground, short trips, carried across roads
and kerbs, never left alone, and back on the charger at home afterwards. Tiredness still works
as the battery gauge outside, so when it starts yawning, head home.

---

## 10. More tamagotchi ideas for this setup

- **The TV is its tamagotchi screen.** When nobody's watching, the Jetson shows an ambient
  pixel-art pond on the TV with little Ah-Ah and Tee-Tee sprites that mirror the real ducks'
  state: sleepy sprites when their batteries are low, a heart when one is being petted, a
  speech bubble when they gossip. It's a literal tamagotchi display of real pets.
- **Red light, green light with Reachy as game master.** Reachy turns away and the ducks walk
  toward it. It spins back round, and any duck its camera catches moving is "out" and has to go
  back. Pure camera and turn-taking, very little to build, very funny to watch.
- **Simon says.** Reachy strikes a head pose and the ducks copy it with `robot.head`. You can
  join in too.
- **Gifts, like a cat.** A contented duck sometimes picks up a small toy with its beak and
  brings it to where you're sitting (Reachy's view knows where the sofa is), then waits for a
  head scratch.
- **Daily wishes.** Each morning each duck picks a wish from its needs and personality: "play
  ball", "see the kitchen", "meet Tee-Tee under the table", "go on walkies". The Pond (and the TV
  pond) shows it, and granting it gives a big mood boost. That's a gentle, non-guilty
  tamagotchi care loop.
- **Weekly vet check.** Reachy plays doctor. Each duck walks a short route in the test pen while
  the Jetson compares gait, speed, servo temperatures and fall count against its own baseline.
  The result reads like a pet check-up ("Tee-Tee's left knee is running warm"), and it's genuinely
  useful maintenance: you spot a wearing servo before it fails.
- **Bedtime and wake-up rituals.** At night Reachy looks at each duck on its charger in turn,
  wiggles its antennas, and dims into its own sleep pose. In the morning, whoever has the most
  energy wakes the other one.
- **Printed tags as furniture for robots.** Small AprilTag-style printed markers on the
  chargers, beds and toys give the cameras exact positions. It makes "go to bed" docking,
  gift-bringing and games much more reliable, and the tags can double as "toys" in games.

---

## 11. The dev kit: parts become part of the story

The kit has 3 spare motors, 5 motor cables, 2 batteries, a dual charger, 10 NFC tags, Hugging
Face credit, a screwdriver and a screw pack. Every item can be useful *and* part of pet life.

### Spare motors and cables: the duck hospital

The Microduck's servos are **Dynamixel XL330s**, 15 per duck. The robot's software makes a swap
easy. From `docs/design/robotd-design.md`: a new XL330 comes up as ID 1, and at start-up
`robotd` pings the fifteen expected IDs. If **exactly one** is missing, it finds the new servo,
gives it the missing ID and the bus speed, checks its registers and reboots it. You swap the
motor, power on, and the duck adopts it.

- **Surgery day.** When the weekly vet check (§10) flags a servo (running hot, a joint that
  lags, a knee that no longer matches the other), that's surgery. Reachy plays the nurse and
  watches, the duck is "put to sleep" (`robot.shutdown`), you swap the motor, and it wakes up
  and does a gentle recovery walk in the test pen before it's allowed to play. The diary says
  "Tee-Tee got a new left knee", and the Pond keeps a medical history per joint.
- **One at a time, always.** Auto-adoption only works when exactly one servo is missing. With
  two missing, `robotd` leaves them alone because it can't tell which is which. Never replace
  two motors in one session.
- **Firmware check.** `robotd` reads the bus with fast sync read, which needs **XL330 firmware
  v46 or later**. Check a spare's firmware (e.g. with ROBOTIS's Dynamixel Wizard and a USB
  adapter) before surgery day, not during it.
- **Keep two spares for repairs.** With two ducks and 30 servos between them, and the knees
  and ankles working hardest, two spares is a sensible hospital stock.
- **Spare cables** are for repairs first. A cable that's worn at a joint is a classic cause of
  "random" servo dropouts, so the vet check should suggest reseating or replacing the cable
  before blaming the motor.
- **The third spare builds a Nest gadget** (below). Reachy Mini's head also uses XL330-family
  motors, so check the exact variant (M288 vs M077) before assuming a spare fits Reachy, and
  don't open Reachy's bus for gadgets.

### A Nest gadget from the spare motor: the novelty feeder

A **rotating feeder**: a small turntable with 4–6 compartments, each holding one small,
interesting object (a pinecone, a toy car, a shell, a bottle cap). One XL330 turns it, driven
from the Jetson through a small USB-to-Dynamixel adapter (a ROBOTIS U2D2 or similar; not in the
kit). Use the spare cables to wire it, and the screw pack to mount it.

- It's the tamagotchi **food bowl** made literal. Curiosity is hunger, and the feeder serves
  a new object when a duck is hungry for novelty, but only a few times a day.
- The duck walks up, looks, the VLM names the object, and it goes into the Duckdex.
- You restock it weekly with new things. Objects from **field trips** (a leaf, a stone from the
  walk) are the best food: the duck that stayed home gets to "taste" the outing.
- The same motor can also **roll a ball out** for fetch, or raise a little **flag** when a
  game ends. One motor, one gadget at a time.

### Batteries and the dual charger: pit stops and battery health

With the two kit batteries, you have **four packs for two ducks**, and the dual charger keeps
two topping up at the Nest.

- **Pit stops.** When a duck is very tired, it walks to the Nest. You swap its pack in under a
  minute (after `robot.shutdown`, since the duck sits and powers off first) and it wakes
  "refreshed" with a satisfied `coo`. That's a real tamagotchi **meal time**, and it means the
  ducks can play all afternoon instead of sitting on the charger.
- **Longer walkies.** A charged spare in your bag roughly doubles the time a field trip can
  last.
- **Battery health diary.** Put a cheap NFC sticker on each pack and log swaps with your phone
  (below). The Jetson logs which pack is in
  which duck and learns each pack's discharge curve from `robot.state`. Eventually you'll get
  "Pack C is getting tired, fewer minutes per charge", and battery wear stops being a surprise.
- **Safety, since these are 2S LiPo packs.** Charge on a non-flammable surface or in a LiPo bag,
  never unattended overnight, and store spares at storage charge if they won't be used for a
  while. Don't let charging become a night-time job: the Nest's night is for sleeping.

### NFC: the duck's sense of touch-and-smell

**Correction to earlier versions of this plan:** the Microduck *does* have NFC. Pollen's press
kit lists **two NFC antennas, one in the head and one in the beak**, and says tagged objects
brought close can trigger specific moves. The dev pack includes the 10 tags, and the separate
accessory pack adds an "NFC polaroid" prop and ten more tags.

**What's not public yet:** the open-source `microduck` repo (checked at commit `a9ec4b2`,
23 Sep 2026) has no NFC daemon, no `nfc.*` RPC and no NFC config key. The press kit doesn't say
how NFC is exposed to software, either. So the plan assumes Pollen will expose tag reads (for
example a notification carrying the tag ID and which antenna saw it). Until then, the ideas below
are designed but not buildable. When the interface lands, **Phase 5 is where it plugs in**. If
it's slow to arrive, it's a good thing to ask Pollen about or contribute.

NFC range is a few centimetres, so the two antennas mean two different senses:

- **The beak is a nose.** With the ground-pick skill the beak touches the floor, so the duck can
  **sniff** a tagged object before picking it up, and confirm it's holding the right one.
- **The head is for touch.** You hold a tagged card to the duck's head and it reacts, the way
  you'd show a pet a treat. That makes cards tapped on the head the most natural "buttons" in
  the house: no phone and no extra reader needed.

**What NFC adds to the pet:**

- **Picking up the correct item.** "Tee-Tee, bring the yellow ball." The camera finds
  ball-shaped candidates, the beak sniffs each one, and only the right ID gets picked up. It's
  real fetch-by-name.
- **Treasure hunts by smell.** Hide tagged tokens under cushions. The ducks search by sight,
  then confirm by sniffing, and whoever sniffs it first tells the other where it was found.
- **Objects the ducks truly know.** Vision can confuse two yellow balls, but a tag can't. Tagged
  toys become named companions in the Duckdex ("Ball #3, Ah-Ah's favourite"), with a history of
  where each was found and who played with it.
- **Self-labelled training data.** Each time the beak reads a tag, the Jetson saves the camera
  frame paired with that ID, on the LAN. Over weeks that becomes a dataset for training a small
  **personal object detector** for *your* toys on Olares, better than any generic model.
- **Sim-to-real in the dojo.** In MuJoCo, give simulated objects IDs and train "pick up the
  object with ID X among several". On the real duck, the beak reading is a **ground-truth
  check** of success: right tag in the beak means success, wrong tag means a miss. The test
  pen can then score pick-up skills automatically, the same keep-or-roll-back loop as gaits.
- **Feeding you can verify.** With a tag under each feeder compartment, the beak confirms which
  "dish" the duck ate from, so the Duckdex never mixes up what it tasted.
- **Social sniffing (to test, not promised).** Whether one duck's beak can read anything from
  the other's head depends on how Pollen drives the antennas. If it only reads passive tags, a
  small tag sticker on each duck's head gives the same effect: a "beak boop" greeting where
  each duck knows who it touched.

**A suggested use for the ten dev-kit tags:**

| Tags | Where | Read by | Does |
|---|---|---|---|
| 3 | favourite toys (the yellow ball, a plush, a block) | beak | named toys: fetch-by-name, pick the correct item, toy history |
| 2 | treasure tokens | beak | treasure hunts by smell |
| 1 | under the feeder | beak | confirms feeding. Add more stickers later for one per compartment |
| 1 | **walkies card** | head | tap on a duck's head at the door: *that* duck goes on the outing (recorder mode, Reachy "waits by the door"). Tap again on return for **homecoming** |
| 1 | **bedtime card** | head | starts the bedtime ritual |
| 1 | **game card** | head | starts a game. Each tap picks the next: red light green light, Simon says, treasure hunt |
| 1 | **postcard** of a favourite trip photo | head | replays that trip's slideshow on the TV, and the duck that took it gets excited |

**Home bookkeeping moves to cheap extra stickers and your phone.** Battery packs A–D and the
spare-parts box are logged by tapping them with your phone (Home Assistant's companion app
reads NFC and reports to your local Home Assistant), so the duck's ten tags stay for play.
NTAG213 stickers are cheap if you want more toys and postcards.

**If you get the accessory pack,** the NFC polaroid is a perfect "photo card": hold it to a
duck's head to show it a memory.

### Hugging Face credit: cloud muscle without home data

This is the one item that touches the cloud, so the rule is: **nothing from inside the home
ever goes up.** Within that, it's genuinely useful:

- **Quiet training.** Reinforcement-learning runs in `microduck_rl` use only *simulated* data.
  Running the big dojo sweeps (running gait, gravel, grass) on Hugging Face GPUs keeps Olares
  cool and quiet, and only the finished ONNX policy comes home. It then still goes through
  `duck-sim`, the test pen and rollback, exactly as before.
- **Try before you host.** Compare vision models on public images to choose which small VLM
  to run on the Jetson, before downloading anything.
- **Share skills, if you want.** `robotctl policy search` finds community policies on the Hub.
  A running gait Ah-Ah learned could become one other Microduck owners can install. Only the
  policy file is shared, never footage.

If you'd rather keep even simulation training at home, the credit is optional: the plan works
without it.

### Screwdriver and screw pack: the maintenance ritual

- **Monthly grooming.** Servo horns and frame screws loosen with walking. A monthly
  "preening" session where you check and tighten screws is the duck equivalent of grooming.
  The vet check's asymmetric-gait warning should suggest "check the screws" before "replace the
  motor".
- **Mounting the Nest:** the feeder, charger cradles, and a small shelf for the
  spares box.

---

## 12. Build plan: foundation first, then everything

Each phase has a milestone you can see. Ideas are listed where they fit, so nothing from this
plan is lost.

**Phase 0: plumbing and the Nest (a weekend)**
- Name the ducks: `sudo robotctl system set-name ah-ah` and `sudo robotctl system set-name tee-tee`.
  Every board flashed from one image is called `radxa-zero3`, so they'll collide otherwise.
- Put the ducks, the Jetson and Olares on the same LAN, and plug Reachy Lite into the Jetson
  by USB. From the Jetson, both `curl http://ah-ah.local:8080/frame -o f.png` and
  `curl http://localhost:8000/api/state/full` should work (or use duck IPs from `duckctl ip`).
- Enable the duck detector (`[duck_detector] enabled`) and chorale consent (`[chorale] accept`)
  in `robotctl configure` on both ducks.
- Noctua fans and a GPU power cap on Olares. Home Assistant from Olares Market.
- Set up **the Nest** on the TV stand: Reachy, the Jetson, the dual charger, two bed spots, and
  a box for spares and tools. Label the four battery packs A–D with cheap NFC stickers (read by
  your phone). Tag the three favourite toys and make the walkies, bedtime, game and postcard
  cards.
- Check the three spare motors' firmware (v46+).
- Milestone: every device answers from the Jetson, and the Nest exists physically.

**Phase 1: the Jetson talks to everyone**
- One Python service in Docker on the Jetson. For the ducks, reuse
  `spaces/shared/control.py` (`Rpc`) over the WebRTC control lane via `ws://<duck>:8443`. For
  Reachy Lite, run its daemon on the Jetson and use the `reachy_mini` SDK or
  `http://localhost:8000/api`.
- Milestone: a web page button makes Ah-Ah turn and `greet` while Reachy looks at it.

**Phase 2: seeing the room**
- The duck detector plus a furniture/object detector on Reachy's camera frames, and printed
  tags on the chargers, beds and toys.
- Reachy's **overseer gaze**: its head follows whichever duck is doing something interesting.
- Build the living-room landmark map.
- Milestone: the Pond shows a live top-down sketch of the room with both ducks on it.

**Phase 3: the spine, tiredness and the Nest routines**
- Needs/mood tick at 1 Hz per duck, state in SQLite or Redis, and the safety supervisor.
- The **battery-to-tiredness gauge** with its visual cues, Reachy's antenna droop, and "go to
  bed" docking using the tags.
- **Pit stops**, logged by a phone tap on the pack, and the **battery health diary**.
- The **bedtime and wake-up rituals** (the bedtime card on a duck's head, once NFC is exposed;
  a button in the Pond until then).
- **Personalities from voice seeds.**
- Milestone: left alone for an hour, both do believable things. When the battery runs low they
  look tired and go to their bed spots, and a pit stop brings them back.

**Phase 4: the Pond and the TV pond**
- The **Pond dashboard** on Olares (via LarePass): needs bars with real battery %, Reachy's view,
  the map, diaries, and battery and medical history.
- The **TV tamagotchi screen**: pixel-art Ah-Ah and Tee-Tee mirroring the real ducks.
- **Daily wishes**, shown on both.
- Milestone: a glance at the TV tells you how both ducks are doing.

**Phase 5: eyes, memory and food**
- Captions from the small VLM on the Jetson, the **Duckdex**, **curiosity is food**, and object
  sightings pinned to landmarks.
- Build and wire the **novelty feeder** from the spare motor and cables, with a tag under it.
- **NFC, once Pollen exposes it:** named toys, beak sniffing, feeding confirmation, and
  camera-frame + tag-ID pairs saved to start the personal object dataset.
- Milestone: show Ah-Ah a ball, move it behind the chair, and the Pond shows "yellow ball:
  behind the green chair". The feeder serves Tee-Tee a pinecone, and it's a new Duckdex entry.

**Phase 6: telling each other, and the social life**
- The duck bus with the earshot rule and the chirp-phrase performance.
- **Recognition and friendship scores**, **follow the leader / conga**, **separation anxiety**
  and reunions, and **spontaneous duets** with Reachy swaying along.
- Milestone: Ah-Ah finds the yellow ball, tells Tee-Tee, and Tee-Tee goes and finds it.

**Phase 7: games**
- The **game card** (tapped on a duck's head), and Reachy as referee.
- **Red light, green light**, **Simon says**, **treasure hunt** (by sight, then by beak sniffing), **fetch-by-name** with the correct tagged toy,
  **where's the duck?**,
  **fetch** (the feeder rolls the ball), **tidy-up scouting**, **rumours**, and **gifts**
  brought to the sofa.
- **Door greeting** via Home Assistant presence.
- Milestone: an evening of red light, green light that makes you laugh.

**Phase 8: health, the dojo and the hospital**
- The **weekly vet check** in the test pen: gait, speed, servo temperatures and falls against
  each duck's baseline.
- **Surgery day** with the spare motors (one at a time), logged with a phone tap on the spares box, with a post-op recovery
  walk. Monthly **grooming** with the screwdriver.
- **The dojo:** train (on Olares while you're out, or on Hugging Face credit with simulated data
  only), validate in `duck-sim`, run test-pen trials in Reachy's view, keep or roll back, and
  **teach the other duck**. Aim for running.
- **NFC-scored pick-up skills:** train "pick the object with ID X" in MuJoCo, and let the beak's
  tag read score the real trials automatically.
- Train the **personal object detector** on Olares from the tag-labelled frames.
- Milestone: the vet check catches something real before it breaks, and one duck learns a new
  move.

**Phase 9: sound (the stretch goal)**
- ggwave step 1 with Reachy (works today), then the ducks listening (shared mic), then the ducks
  speaking (an upstream raw-playback change). Reachy overhears the gossip.
- A **shared dialect** of chirp phrases emerges from their gossip.
- Upstream proposals to Pollen: raw audio playback, a shared mic stream, and the spine as the
  missing autonomous brain.
- Milestone: you hear Ah-Ah tell Tee-Tee where the ball is, and Tee-Tee goes.

**Phase 10: field trips**
- **Tier 1 walkies** with the on-duck recorder, with a
  spare battery in the bag, started and ended by the walkies card tapped on the traveller's head.
- **Homecoming**, trip processing on Olares, the **Wild Duckdex**, **wanderlust** and taking
  turns, **slideshow night** on the TV, NFC **postcards** held to a duck's head, and souvenirs for the feeder.
- Outdoor-terrain training in the dojo, then the **Tier 2 expedition kit**.
- Milestone: after a walk, the stay-home duck "tastes" a leaf from the feeder and watches the
  slideshow.

**Phase 11: mapping the flat (§15)**
- **M1:** fog of war over the lounge floor in Reachy's view, cleared where the ducks walk and
  fading again with time. The Pond's room becomes the real lounge.
- **M2:** the 9 AprilTags and your walkthrough. First Gaussian map per room on Olares, and the
  floor grid seeded from it.
- **M3:** the ducks' ToF building the floor grid, and scout-pose snapshots with positions.
  Doors, thresholds and the balcony's glass line.
- **M4:** exploration mode: frontiers, sorties with battery and drift budgets, and the two ducks
  splitting the flat.
- **M5:** render-and-compare change detection, fog that fades differently for each kind of
  thing, room retraining, and the timeline.
- Milestone: after a day of sorties, the Map tab shows the whole flat. You move the green chair,
  and by the evening Ah-Ah has noticed, told Tee-Tee, and the lounge's Gaussian map shows it in
  its new place.

**Later: human speech** (§13), and **social beak boops** if the antennas allow it.

---

## 13. Parked for later: human speech

On hold until the foundation works well. Kept here so nothing's lost:

- **Duck-speak to you:** Animal-Crossing-style babble in each duck's own voice, driven through
  the live synth behind the theremin (`sounds::Stream`), with English subtitles in the Pond.
  Needs an upstream `robot.voice` RPC. Piper TTS on the Jetson would provide the timing.
- **Listening to you:** wake words "Ah-Ah" / "Tee-Tee" and whisper.cpp on the Jetson. Reachy
  Mini's mic array, with its direction of arrival, is the natural mic for this, and it can turn
  toward whoever is speaking.
- **Questions like "where are my keys?"** answered from the landmark memory, with the photo.
- **Reachy as translator,** saying in English what the ducks just told each other, on request.

---

## 14. Gotchas

- **Keep the language model off the motors.** It proposes and the duck's own controller
  decides. Refuse `robot.move` goals toward edges or drops flagged by ToF.
- **LAN only, and stay off the relays.** The Microduck's remote path and Reachy Mini's JS apps
  both use a hosted signalling service by default. Talk to both robots **directly on the LAN**
  (duck `:8080/:8443`, Reachy `:8000`), and reach the Pond from outside only through LarePass's
  private VPN.
- **Snapshots, not streams, from the ducks.** A frame every few seconds is plenty for captions.
  Reachy's camera is the one that runs continuously, and it's on mains power.
- **One consumer at a time.** If the Pond and the phone app both hold a session with a duck,
  check how it handles that (`robot.remoteSessionActive`) before assuming both can drive. The
  same goes for Reachy Mini apps running at the same time as the Jetson's control.
- **Upstream changes are real changes.** Raw playback and a shared mic on the duck each need a
  small addition to the Microduck software. Propose them to Pollen rather than patching the
  robot image, because the updater replaces anything local on the next release.
- **LiPo packs deserve respect.** Charge them on a non-flammable surface, never unattended
  overnight, and store spares at storage charge.
- **Surgery is one servo at a time.** `robotd` only auto-adopts a replacement when exactly one
  ID is missing.
- **NFC is hardware today, software later.** Pollen lists two NFC antennas (head and beak), but
  the public repo has no NFC interface yet. Build the NFC ideas against whatever Pollen exposes,
  and don't hack a reader service onto the robot image, because the updater replaces it.

---

## 15. Mapping the flat: fog of war and the Gaussian map

The ducks map the whole flat and keep the map up to date, like the fog of war in Age of Empires
or StarCraft: somewhere never seen is black, somewhere seen before shows what it looked like last
time, faded, and it sharpens again when a duck goes back. On top of that sits a photoreal
**Gaussian map** of every room that you can orbit around in the Pond.

### The flat

About 70 m²: the **lounge with the open kitchen**, **two bedrooms**, **two bathrooms**, and a
**5 m² balcony**. Reachy sits on the TV stand in the lounge, and the chargers are there too, in
Reachy's view. **No room is off limits**, but the bathroom doors are often closed.

Because the chargers are in Reachy's view, **every trip starts and ends where the Nest knows
exactly where the duck is**. Drift in a duck's own position estimate is reset at the start and
end of every trip.

### Four layers, each on the right box

| Layer | What it holds | Built from | Runs on |
|---|---|---|---|
| **1. Floor grid** | 5 cm cells, about 32,000 for the flat: free, blocked, drop, door, threshold, fence, or unknown. Each cell has a last-seen time and who saw it | The walkthrough, the ducks' ToF, where they have walked, Reachy's view of the lounge floor | Jetson, live |
| **2. Things** | Landmarks and objects, each with a last-seen time and confidence (the scene memory the Nest already has) | Detectors, the captioner, what the ducks tell each other | Jetson |
| **3. Snapshots** | Photos with the position and direction they were taken from | Duck cameras in scout pose, Reachy | Olares disk |
| **4. Gaussian map** | One 3D Gaussian splat per room | Your walkthrough, then fresh snapshots | Olares GPU, batch, daytime |

The ducks steer by layers 1 and 2. The Gaussian map is for looking, and for spotting changes
(below). It never drives a motor.

### Fog of war

- **Black: never seen.** Mostly under beds, behind the sofa, and inside cupboards left open,
  because the walkthrough seeds everything else.
- **Fog: seen before.** It shows the last known state, desaturated, with its age ("kitchen: last
  seen 2 days ago"). What you saw in the walkthrough starts here: surveyed, but not yet visited
  by a duck. Turning on **"start blind"** hides the walkthrough from the game view, so the
  ducks discover the flat from black.
- **Clear: seen right now.** Reachy's camera view of the lounge floor (from the floor
  calibration), and each duck's camera and ToF cone.

**Fog thickens at different speeds for different things.** Walls, doorways and the sofa stay
trusted for weeks. Chairs are trusted for days. Toys and shoes are trusted for hours. Thick fog
over a spot where something used to be is a reason to go and look, so stale information becomes
food for curiosity.

### Doors, thresholds and the balcony

- **Doors are their own kind of cell, with a state:** open, closed, or unknown, plus when it was
  last checked. A closed bathroom door is not a wall. The room behind it keeps its last fogged
  state, and exploring it is **suspended, not abandoned**: a duck passing by checks the door, and
  when it's open again the room is back on the list. In the daytime, a curious duck gives a
  closed door one soft `inquire` chirp, a knock, and then moves on.
- **Thresholds.** Door sills and the balcony sill might be too high for a duck to step over. The
  ToF sees the step. The duck tries once, and if it can't cross, the map marks the sill "can't
  cross yet". That's a dojo goal (§7): learning to step over it is growing up.
- **Bathrooms.** Wet tiles make feet slip, which makes position drift. Each bathroom gets its
  own anchor tag inside (below), and the ToF drop check stays on around the shower tray.
- **The balcony is fully on the map.** It has a uniform glass barrier with no gaps, so there's
  nothing to fall through, and no virtual fence is needed. The glass is a wall to the ducks'
  feet, but a window to their eyes: the ToF can miss clear glass, so the walkthrough marks the
  barrier line and the grid treats it as solid. The ducks go out in the daytime whenever the
  balcony door is open, and the door sill is the only thing to learn (above).

### Knowing where a duck is: anchors

Outside Reachy's view, a duck only has its walking odometry, and legs slip. So the flat gets
**printed AprilTags** (the 36h11 family, about 10 cm square, at the duck's eye height), which
snap a duck's position back to exact whenever it sees one:

| Where | Tags |
|---|---|
| Lounge: one at the Nest, one across the room | 2 |
| Kitchen | 1 |
| Hallway | 1 |
| Bedrooms, one each | 2 |
| Bathrooms, one each, inside | 2 |
| Balcony door | 1 |

That's **9 tags**. The dev kit has 10 NFC tags, so once Pollen exposes NFC, one sticker under
each AprilTag gives a second, touch-based anchor, and a duck can "plant a flag" by pecking it.

Every trip also has a **drift budget**: how far a duck may walk since its last anchor before it
turns back toward one. That budget matters more than battery in a flat this size; the furthest
room is only a few metres from the chargers.

### Exploration mode

- **Frontiers.** A duck goes to the edge between the free floor it knows and black or thick
  fog. That's the standard robot exploration method, and easy to follow on the map.
- **Sorties from the Nest.** A duck goes out, surveys, and comes home before its battery or drift
  budget runs out. It tells the other duck what it found (the duck bus, §6) and takes a pit stop.
- **Scout pose.** A duck doesn't take snapshots while walking: the camera is low and shakes. At
  each survey spot it stops, pans its head, snaps 3–5 frames and moves on. It looks like a little
  surveyor at work.
- **Two ducks split the flat.** They claim rooms over the duck bus ("I'll take the bedrooms,
  you take the kitchen and the balcony") so they never scout the same corner.
- **When it runs:** from a button in the Pond, or on its own when curiosity is high and the fog
  is thick. Never in quiet hours: at night the fog visibly creeps back over the map, and in the
  morning the ducks have somewhere to go.
- **Rewards:** new cells feed curiosity. Duckdex entries like "first into the second bedroom",
  and an "explored today" score in the Pond.

### Your walkthrough: the first Gaussian map

Do this once, after the AprilTags are up, so they appear in the video. The tags give the splat
real-world scale and pin it to the same floor as the grid.

1. **Daytime, all lights on, every door open** (bathrooms too), nobody else in shot.
2. Lock exposure and focus on your phone (tap and hold), no zoom, 4K at 30 fps or 1080p at 60.
3. **Room by room, 1–2 minutes each.** Walk slowly along the walls with the phone pointing
   into the room, once at chest height and **once low, about 20 cm off the floor**. The low loop
   gives the splat duck's-eye views, so it matches what the ducks see later.
4. **Film through each doorway** as you go from room to room, so the rooms join up.
5. **Keep it off the cloud:** copy the videos to Olares over the LAN, and make sure your phone's
   photo backup doesn't upload them first.

On Olares, in the daytime:
- Extract frames and drop any with a person in them.
- Find camera poses with COLMAP or GLOMAP, and use the AprilTags in the frames to fix scale and
  the floor plane.
- Train one splat per room with gsplat.
- Project the result onto the floor to seed layer 1: walls, doorways, furniture footprints, the
  balcony's glass barrier.
- Label the rooms by which tags are in them.

The walkthrough seeds the map with where things are. After that, the ducks keep it true.

### Keeping it fresh, and spotting changes

- **Render and compare.** For each snapshot a duck takes, Olares renders the view the splat
  expects from the same position and compares it with the real photo. Where they differ,
  something has changed. That becomes a map update and a rumour on the duck bus ("the green
  chair moved about 40 cm", "something new by the sofa"), just like the yellow ball.
- **Room retraining.** When a room has enough new snapshots, or a real change, Olares retrains
  that room's splat, in the daytime. Old versions are kept for the timeline.

### In the Pond

A **Map** tab:
- the isometric pixel flat, one tile per 50 cm, drawn from the floor grid;
- fog of war, both ducks, and door states;
- tap a room to open its Gaussian map in a 3D viewer that is bundled with the Nest, not loaded
  from the internet;
- a **timeline slider**: "the lounge last Tuesday".

### Privacy

- Frames with a person in them are thrown away on the Jetson before they're stored or used to
  train, so the toddler never ends up in a splat.
- Snapshots and splats live on Olares, and nothing leaves the LAN.
