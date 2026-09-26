# Pollen Robotics: Microduck Olares Integration

Two [Microducks](https://github.com/pollen-robotics/microduck), **Ah-Ah** and **Tee-Tee**, a
[Reachy Mini](https://github.com/pollen-robotics/reachy_mini) keeping watch from the TV stand, a
Jetson Orin Nano Super, and an [Olares](https://github.com/beclab/olares) box. Together they make
a pair of curious, tamagotchi-like creatures that explore your home, find things, and tell each
other about them.

> **The premise in one line:** the ducks keep their reflexes, Reachy Mini is the eyes and ears
> of the room, the Jetson is the always-on mind, and Olares does the heavy thinking by day.
> **Everything stays on the local network.**

Ground rules:

1. **The robot's own software has the final say on movement.** Anything off-board proposes
   intent. The duck's controller and its safety checks decide how to move, or whether to.
2. **LAN only.** No cloud rendezvous, no relay, no hosted models. If the internet goes down,
   nothing about Ah-Ah and Tee-Tee changes.
3. **No simulated age or handicaps.** They always use the best gait available at full speed,
   and grow in **knowledge and skills**. The long-term aim is for them to run.
4. **Tiredness is a battery gauge you can see.** A duck that's low on battery looks tired, so
   you know to charge it without opening an app.
5. **Quiet at night.** Olares can idle. The always-on work runs on the silent Jetson.
6. **Foundation first.** Human speech (talking to the ducks, the ducks talking to you) is
   **parked** until the foundation below works well (§12).

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

## 11. Build plan: foundation first

**Phase 0: plumbing (one evening)**
- Name the ducks: `sudo robotctl system set-name ah-ah` and `sudo robotctl system set-name tee-tee`.
  Every board flashed from one image is called `radxa-zero3`, so they'll collide otherwise.
- Put the ducks, the Jetson and Olares on the same LAN, with Reachy Lite plugged into the
  Jetson. From the Jetson, both `curl http://ah-ah.local:8080/frame -o f.png` and
  `curl http://localhost:8000/api/state/full` should work (or use duck IPs from `duckctl ip`).
- Enable the duck detector (`[duck_detector] enabled`) and chorale consent (`[chorale] accept`)
  in `robotctl configure` on both ducks.
- Noctua fans and a GPU power cap on Olares.

**Phase 1: the Jetson talks to everyone**
- One Python service in Docker on the Jetson. For the ducks, reuse
  `spaces/shared/control.py` (`Rpc`) over the WebRTC control lane via `ws://<duck>:8443`. For
  Reachy Lite, plug it into the Jetson by USB, run its daemon there, and use the `reachy_mini`
  SDK or `http://localhost:8000/api`.
- Milestone: a web page button makes Ah-Ah turn and `greet` while Reachy looks at it.

**Phase 2: seeing the room**
- The duck detector plus a furniture/object detector on Reachy's camera frames. Reachy's head
  follows the ducks. Build the living-room landmark map.
- Milestone: the Pond shows a live top-down sketch of the room with both ducks on it.

**Phase 3: the spine and the tiredness gauge**
- Needs/mood tick at 1 Hz per duck, state in SQLite or Redis, the safety supervisor, and the
  battery-to-tiredness mapping with its visual cues and Reachy's antenna droop.
- Milestone: left alone for an hour, both do believable things, and when the battery runs low
  they look tired and go to their bed spots.

**Phase 4: eyes and memory**
- Captions from the small VLM on the Jetson, the Duckdex, and object sightings pinned to
  landmarks.
- Milestone: show Ah-Ah a ball, move it behind the chair, and the Pond shows "yellow ball:
  behind the green chair".

**Phase 5: telling each other**
- The duck bus with the earshot rule and the chirp-phrase performance.
- Milestone: Ah-Ah finds the yellow ball, tells Tee-Tee, and Tee-Tee goes and finds it.

**Phase 6: the stretch goal and the dojo, in parallel**
- ggwave step 1 with Reachy, then ducks listening, then (with an upstream change) ducks speaking.
- Training, `duck-sim` validation, test-pen trials in Reachy's view, keep or roll back.
- Upstream proposals to Pollen: raw audio playback on the duck, a shared mic stream, and the
  spine itself as the missing autonomous brain.

**Phase 7: field trips**
- Tier 1 walkies with the on-duck recorder, homecoming, trip processing on Olares, the Wild
  Duckdex and slideshow night. Then outdoor-terrain training in the dojo, and later the Tier 2
  expedition kit.

---

## 12. Parked for later: human speech

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

## 13. Gotchas

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
