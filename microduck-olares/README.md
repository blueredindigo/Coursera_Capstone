# Pollen Robotics: Microduck Olares Integration

Two [Microducks](https://github.com/pollen-robotics/microduck), **Ah-Ah** and **Tee-Tee**, one
[Olares](https://github.com/beclab/olares) box and one Jetson Nano. Together they make a pair of
curious, tamagotchi-like creatures that live in your home, remember you, find things, and tell
each other about them.

> **The premise in one line:** the duck keeps its reflexes, and your home network gives it a
> soul. The 50 Hz body stays on the robot. Memory, mood, speech, learning and gossip live on
> hardware you own. **Everything stays on the local network.**

Ground rules for this project:

1. **The robot's own software has the final say on movement.** Anything off-board proposes
   intent. The duck's controller and its safety checks decide how to move, or whether to.
2. **LAN only.** No cloud rendezvous, no relay, no hosted models. If the internet goes down,
   nothing about Ah-Ah and Tee-Tee changes.
3. **No simulated age or handicaps.** They always move with the best gait available and at full
   speed. They grow in **knowledge and skills**, never by walking worse. The aim is for them to
   get faster, eventually learning to run.
4. **Quiet at night.** The loud machine can sleep. The always-on work runs on something
   silent.

---

## 1. What you're working with

### The Microduck (what the repo actually exposes)

A 25 cm, 800 g biped with an RK3566 (Radxa Zero 3) and a 0.8 TOPS NPU. It runs Rust daemons
that talk one **JSON-RPC** contract, the same calls whether they come from `robotctl`, the phone
app, a gamepad or your own script.

| Sense / act | How you reach it | Pet use |
|---|---|---|
| Camera | WebRTC H.264 (`:8443` signalling), PNG snapshot `GET http://<duck>:8080/frame` | eyes for the vision model |
| 8×8 ToF depth + hand tracker | `tof.frame`, `tof.stream` | "a hand is near me", obstacles |
| Petting detector (mic, on-board CNN) | `pet-detect`, runs inside `robotd` and coos | affection meter |
| Duck detector (NPU, finds *other Microducks*) | `[duck_detector] enabled` | Ah-Ah and Tee-Tee can **see each other** |
| BLE chorale beacon: stable duck id, RSSI, ~245 spare bytes | `chorale.heard`, `chorale.beacon` | who's nearby, how close, a radio "voice" between ducks |
| Voice synth with a **per-robot personality seed** | `robot.sound` with tags `chirp greet coo inquire peck tock alarm wheee` | each duck sounds like a different creature |
| Gaze, head, mouth | `robot.look` (point in space), `robot.head`, `robot.mouth` | attention, curiosity, lip-sync |
| Skills (ONNX policies) | `robot.do`, `robotctl policy add/load` | walk, sit, kick, roulade, ground-pick, get up, plus anything you train |
| Locomotion | `robot.move`, `robot.mode` (walk vs roller) | wandering, following, fetching |
| Odometry (legs + IMU) | `odometry` crate, fed to `robotd` | short-range "where did I walk" |
| State and battery | `robot.state` (battery, thermals, loop health), `robot.subscribe` | tiredness, self-monitoring |

Things from the repo that shape the plan:

- **The on-robot "brain" isn't ported yet.** `docs/ideas/autonomous_behavior.md` lists a
  16-state machine (Chill, LookAround, Wander, Zoomies, Nap, Dance, BallPlay, Petted…) with an
  energy/mood model that isn't in the current daemons. We build the mind off-board first, and it
  can move on-board later.
- **Odometry drifts, and its origin is "wherever the duck faced at boot"** (no magnetometer).
  So a duck can't reliably say "the ball is at x=2.3, y=1.1". It *can* say "behind the green
  chair near the sofa". Landmark-relative memory isn't just charming, it's the representation
  that actually works here (§5).
- **The duck can only play its built-in voice tags today.** `robot.sound` takes a tag and
  nothing else, so arbitrary audio or speech isn't possible yet. The live synth that powers the
  theremin (`sounds::Stream`, with pitch/vowel/level at runtime) already exists, though, and
  exposing it over RPC is the natural upstream addition for speech (§6).
- **The mic is owned by `robotd`'s petting worker** (`arecord` on the codec), so a second
  listener needs the capture shared (ALSA `dsnoop`) or a separate mic (§6).

---

## 2. Hardware: who does what

### Olares, the daytime brain

The Olares box has the GPU, storage, databases and the phone app via LarePass. It handles the
**heavy, occasional** work: the bigger vision model, the language model for diaries and
conversation, image generation, policy training, and long-term memory.

**Keeping it quiet at night:**

- **Noctua fans help.** An NF-A12x25 / NF-A14 set on a sensible fan curve makes an idle box
  close to silent. The real noise source is usually the **GPU under load**, so also:
- **Cap the GPU power** (`nvidia-smi -pl <watts>`) to around 60–70% of stock. You lose a small
  share of speed for a large drop in heat and fan noise.
- **Schedule the heavy work for when nobody's home.** Home Assistant (in Olares Market) knows
  when your phones leave, so start training then, not at 3 a.m. At night Olares just idles.
- **The ducks don't need Olares at night.** They're asleep, and the Jetson keeps watch.

### The Jetson Nano, the always-on spine

It's a good fit: silent or near-silent, about 5–15 W, and it can sit on a shelf all the time.
Its job is **everything that must always run, but lightly**:

- the **spine**: the needs/mood simulation and behavior picker for both ducks, at ~1 Hz;
- the **message bus** between the ducks (§6) and the shared landmark map (§5);
- **speech in and out**: wake word, speech-to-text (whisper.cpp), text-to-speech (Piper);
- an optional **room camera** running a detector, so both ducks share one view of the room (§5);
- **safety supervision**: watching `robot.state` for falls, thermals and battery, and
  telling a duck to rest.

Which Jetson you have matters:

| | Original Jetson Nano (4 GB, 2019) | Jetson Orin Nano (8 GB) |
|---|---|---|
| Software | JetPack 4.x, Ubuntu 18.04, old CUDA. **Use Docker images** to get modern Python | JetPack 6, Ubuntu 22.04 |
| Spine, bus, map, supervision | Easily | Easily |
| Wake word + whisper.cpp `tiny`/`base` | Yes (base is a little slow) | Yes, `small` too |
| Piper TTS | Yes | Yes |
| Room-camera detector | Small YOLO at a few fps | Comfortably |
| Local LLM | Not really (tiny models, a few tokens/s) | 3B-class models and small VLMs work |

Run the Jetson as a **plain Docker host on the LAN**, not as an Olares node. The original Nano
doesn't meet Olares's minimums (8 GB RAM, 150 GB SSD, Ubuntu 22.04+), and keeping it separate
means it keeps running when Olares is off or rebooting. The Jetson calls Olares's model endpoints
over the LAN when Olares is awake, and **degrades gracefully** when it isn't: the ducks keep
their needs, moods, map and duck-to-duck messages, and only lose the big-model extras
(rich diaries, new captions) until morning.

---

## 3. Architecture

```
┌──────────── Olares (daytime, heavy, can sleep) ─────────────┐
│ VLM captions · LLM diaries & conversation · image gen        │
│ microduck_rl training on GPU · long-term memory (Postgres,   │
│ vectors, Files) · "Pond" dashboard via LarePass (LAN/VPN)     │
└──────────────────────────────▲───────────────────────────────┘
                               │ LAN HTTP, only when awake
┌─────────────── Jetson Nano (always on, silent) ──────────────┐
│ spine: needs/mood, behavior picker for Ah-Ah & Tee-Tee        │
│ duck bus: messages between ducks, "earshot" rules            │
│ landmark map · speech (wake word, whisper.cpp, Piper)         │
│ safety supervisor · optional room camera + mic                │
└────────▲──────────────────────────────────────────▲──────────┘
         │ JSON-RPC (WebRTC control lane) + /frame    │
┌────────┴────── Ah-Ah ──────┐   BLE beacon   ┌───────┴──── Tee-Tee ─────┐
│ robotd 50 Hz · safety ·    │◄──── RSSI ────►│ robotd 50 Hz · safety ·   │
│ pet-detect · tofd ·        │  spare bytes   │ pet-detect · tofd ·       │
│ duck-detect · voice        │                │ duck-detect · voice       │
└────────────────────────────┘                └───────────────────────────┘
```

**Three speeds, three places:**

1. **Reflexes (on the duck, milliseconds).** Balance, get-up, petting coo, obstacle stop. These
   never wait on the network, and **they always win**.
2. **Spine (Jetson, ~1 Hz).** Needs and mood pick the next behavior: `robot.do`, `robot.look`,
   `robot.sound`, `robot.move`. It's cheap and deterministic, and it works with Olares asleep.
3. **Soul (Olares, every 30 s to a few minutes, daytime).** Vision captions, memory,
   conversation and diaries. It sets *goals* ("go look behind the green chair"). It never
   drives motors directly.

---

## 4. The tamagotchi core (no aging, full speed)

Needs come from **real sensors**, so looking after them is a physical act.

| Need | Rises when | Satisfied by | What you see |
|---|---|---|---|
| **Energy** | long activity, low battery in `robot.state` | the charger, a nap | yawning `coo`, goes to its spot, sits. It never walks worse |
| **Curiosity / "hunger"** | time since it saw something *new* | showing it new things, exploring | `inquire` chirps, looks around, wanders |
| **Affection** | time since last petting | head scratches (`pet-detect`), a hand near its face (ToF) | follows you, `greet` when you walk in |
| **Social** | time away from the other duck | meeting up (BLE RSSI + duck detector) | calls out, rushes over on approach |
| **Play** | long calm periods | a ball, a game, the other duck | Zoomies, kicks, roulades, chases |

Tiredness changes **what** a duck wants to do, never **how well** it moves. When it's awake it's
at full ability.

### Curiosity is food

The ducks **eat novelty**. Put something in front of Ah-Ah and it looks (`robot.look`), the
Jetson grabs a `/frame`, and a vision model names it ("a yellow tennis ball"). Something new is
a full meal: a happy `wheee`, a little dance, and a new entry in Ah-Ah's **Duckdex**, a photo
album from its own eyes. Something it's seen before is a snack, and the same thing ten times is
boring. Each duck keeps its **own** Duckdex, which is what makes telling each other about things
worth doing (§5).

### Growing up means learning, not aging

There are no life stages and no speed caps. Growth is:

- **Knowledge.** A bigger Duckdex, a richer map of the home, more words understood (§6), more
  memories of you.
- **Skills.** New policies learned in the "dojo" (§7). Tee-Tee learns to trot, Ah-Ah learns to
  climb onto the rug edge, and later both learn to **run**.
- **Relationship.** A friendship score between them, and between each of them and you.

---

## 5. "I found a yellow ball, behind the green chair near the sofa"

This is the feature you liked most, so here's how it would work.

### Each duck builds a landmark map

A **scene graph** of landmarks and relations, not coordinates:

```
[sofa] ──near── [green chair] ──behind── (yellow ball)   seen by Ah-Ah, 14:32, photo #412
   │
  left-of
   │
[window] ──under── [radiator]
```

- **Landmarks** are big things that don't move: sofa, chairs, table, doorways, radiator.
  They get learned automatically as they keep showing up in captions from the same area, and
  you can name them in the Pond ("that's Grandma's chair").
- **Objects** are things that move: balls, socks, keys, the cat. Each sighting stores the
  nearest landmarks and the relation the vision model reports (`behind`, `under`, `on`,
  `next to`), plus the photo and time.
- **Odometry** fills in the short hops ("about 1 m past the chair, turned left") and resets its
  drift every time the duck recognises a landmark.
- **Optional room camera** on the Jetson, high on a shelf. It sees both ducks and the big
  furniture, which gives a shared, drift-free map. The duck detector model can run on it too.
  This is the easiest way to make "go to the green chair" reliable.

### Telling the other duck

1. Ah-Ah finds the ball. Its caption plus landmark lookup produces a small structured message:
   `{found: "yellow ball", rel: "behind", landmark: "green chair", near: "sofa", conf: 0.8}`.
2. If Tee-Tee is **in earshot** (§6), Ah-Ah "says" it: it turns toward Tee-Tee (the duck
   detector gives the bearing), does an excited chirp phrase, and the message is delivered.
   The Pond shows the subtitle: *"I found a yellow ball! It's behind the green chair near the
   sofa."*
3. Tee-Tee's curiosity now **targets the ball**. It resolves "green chair" in *its own* map,
   walks there, looks behind it, and either confirms ("found it!", with a Duckdex entry
   credited "told by Ah-Ah") or reports back ("it's not there anymore", and the memory is
   marked stale).
4. If Tee-Tee has never seen the green chair, it asks: an `inquire` chirp, and Ah-Ah leads the
   way (follow-the-leader).

It also works for you: "Ah-Ah, where's my keys?" gets "Under the coat by the front door, I saw
them at 9:10", with the photo.

### More games on the same machinery

- **Fetch-by-description.** You say "Tee-Tee, find something red". It searches its map, then
  goes.
- **Treasure hunt.** You hide a toy. The first duck to find it tells the other, and they race
  to it.
- **Tidy-up scouting.** In the evening, both ducks tour the rooms and report "things out of
  place" compared with the morning.
- **Rumours.** Old or second-hand information carries lower confidence, so the ducks can be
  wrong, go check, and "argue" about who was right.

---

## 6. Speech: how Ah-Ah and Tee-Tee talk

There are three different problems. Solving them separately keeps each one simple.

### A. Duck to duck: meaning travels by radio, the voice is the performance

The ducks don't need to hear English. They need to **exchange meaning** and **look like
they're talking**.

- **Meaning** goes over the Jetson's duck bus on the LAN, or directly in the ~245 spare bytes
  of the BLE beacon. A message like the ball one compresses to a few dozen bytes using IDs for
  object, relation and landmark. The BLE path works even if the Jetson is down.
- **Earshot rule:** a message is delivered **only if the ducks could plausibly hear each
  other**, meaning BLE RSSI above a threshold and ideally the duck detector seeing the other
  duck. If Tee-Tee is in another room, it doesn't find out until they meet, so gossip spreads
  physically, like it would with real animals.
- **Performance:** the speaker turns toward the listener, plays a chirp phrase whose length and
  rhythm follow the message, and the listener answers (`inquire` for a question, `greet` or
  `wheee` for "great!", `peck` for "not interested"). The Pond and your phone show the
  subtitle.

**Stretch goal: truly acoustic messages.** With raw audio playback on the duck (below), the
message itself can be sent as sound using a data-over-sound library like
[ggwave](https://github.com/ggerganov/ggwave), in R2-D2-style chirps that the other duck's mic
decodes. Then "in earshot" is literally true, and you'd hear them talking. Motor noise and
room echo make this unreliable, so keep the radio path as the fallback.

### B. Duck to you: "duck-speak" with subtitles

- The language model on Olares (or a small one on an Orin Nano) writes what the duck wants to
  say, in character.
- **Duck-speak, not a human voice.** The best way to keep them feeling like ducks is
  Animal-Crossing-style babble: syllables in the duck's own voice following the rhythm and
  intonation of the real sentence, with the English as subtitles in the Pond. The duck already
  has the right engine for this: `sounds::Stream`, the live synth behind the theremin, driven by
  pitch, vowel and level. It needs one **upstream addition**: an RPC such as `robot.voice` that
  takes a short pitch/vowel/level contour. Piper TTS on the Jetson gives the phoneme timing to
  build the contour, and `robot.mouth` moves the beak in sync.
- **Actual English, if you want it sometimes.** The duck's speaker can't play arbitrary audio
  today, so for real words either add raw playback upstream (a `robot.play` RPC, or an ALSA
  path next to the voice), or play Piper's audio from a small speaker near the Jetson or a
  Home Assistant speaker. A good split: ducks babble, and one "translator" speaker says the
  English quietly when you ask.

### C. You to the ducks: listening

- **Start with a room mic on the Jetson.** A USB mic array (ReSpeaker-class) with wake words
  "Ah-Ah" and "Tee-Tee" (openWakeWord), then whisper.cpp for the command. It's far from the
  motors so it's clean, and it doesn't touch the robot at all. The wake word picks which duck
  you're talking to, and the landmark map turns "the green chair" into a destination.
- **Later, the duck's own ears.** The petting worker holds the duck's mic. Sharing it via ALSA
  `dsnoop` and streaming 16 kHz audio to the Jetson over the LAN lets each duck hear you
  itself, and makes acoustic duck-to-duck messages possible. Walking is loud, so only listen
  when the duck is still (turning toward you when it hears its name is a nice cue).
- **Understanding stays small at first:** names, "come here", "find X", "where's X", "go to
  sleep", "good duck". The ducks learn more phrases over time as part of growing up in
  knowledge.

---

## 7. The dojo: learning to move better, and to run

You want them to move freely and as well as possible, and eventually to run. Here's a safe
path to that.

1. **Always run the best official gait.** `robotctl policy check` / `update` keeps both ducks on
   Pollen's latest policies. Never cap speed for "personality".
2. **Train on Olares** with [microduck_rl](https://github.com/pollen-robotics/microduck_rl)
   (MuJoCo + PPO) on the GPU, while you're out, at a capped power limit. Candidate skills:
   faster walk, trot, **run**, turning in place, stepping over a threshold, better recovery.
3. **Test in simulation first.** `scripts/duck-sim` runs the *real daemons* against the
   simulated body. A new policy has to pass a test battery (flat, carpet friction, pushes,
   slopes, start/stop) before it ever touches a real duck.
4. **Self-trials in a test pen.** Once a policy passes in sim, the duck tries it for real in a
   marked safe area (soft floor, clear ToF, no stairs or table edges), in short bursts. The
   Jetson records falls, `robot.state` thermals and loop health, and speed achieved from
   odometry (plus the room camera if you have one). The duck's own get-up and safety handling
   stays in charge throughout.
5. **Keep or roll back automatically.** Better and no worse on falls or heat: keep it. Otherwise
   `robotctl policy reset`, and a diary entry: "Tried running today. Fell twice. Not yet."
6. **Teach the other one.** When Ah-Ah graduates a skill, Tee-Tee gets it after it has watched
   Ah-Ah do it (the duck detector confirms). Tee-Tee then goes through its own test-pen trial,
   since every body is a little different.

Worth being honest about: running is hard for a 25 cm biped. Sim-to-real gaps are real, falls
wear servos, and "run" may first look like a fast shuffle or a tiny hop. The pen, short bursts,
and automatic rollback are what make it safe to keep trying.

---

## 8. More ideas for the pair

- **Recognition and friendship.** They face each other and greet in a way that depends on
  their friendship score, which grows with good interactions.
- **Personalities from seeds.** Each duck's voice seed also rolls temperament traits (bold↔shy,
  chatty↔quiet). Maybe Ah-Ah is the explorer and Tee-Tee the one who asks questions.
- **Spontaneous duets** on the shared BLE beat, rarely and only when both are happy and
  together.
- **Follow the leader / conga**, **hide and seek** with BLE hot/cold, **separation anxiety**
  and big reunions.
- **A shared dialect.** Their chirp phrases for landmarks and objects drift as they gossip, so
  in a month they have "words" you didn't design.
- **Diaries and letters.** Nightly diary summaries on Olares (in the morning, if Olares slept),
  plus a weekly letter to you with a photo each duck picked.
- **Door greeting.** Home Assistant sees your phone on the home Wi-Fi and the nearer duck goes
  to meet you. LAN presence only, no cloud.
- **The Pond dashboard** on Olares, reachable from your phone through LarePass: needs bars,
  live camera, both Duckdexes, the landmark map with "last seen" pins, diaries, and message
  subtitles between the ducks.

---

## 9. Build plan

**Phase 0: plumbing (one evening)**
- Name them: `sudo robotctl system set-name ah-ah` and `sudo robotctl system set-name tee-tee`.
  Every board flashed from one image is called `radxa-zero3`, so they'll collide otherwise.
- Put the ducks, Jetson and Olares on the same LAN. From the Jetson,
  `curl http://ah-ah.local:8080/frame -o f.png` should work (or use the IP from `duckctl ip`).
- Enable the duck detector (`[duck_detector] enabled`) and chorale consent (`[chorale] accept`)
  in `robotctl configure` on both.
- Noctua fans and a GPU power cap on Olares. Home Assistant from Olares Market for presence.

**Phase 1: Jetson talks to a duck**
- One Python service in Docker on the Jetson. Reuse `spaces/shared/control.py` (`Rpc`) over the
  WebRTC control lane via `ws://<duck>:8443`: `robot.subscribe`, `robot.sound`, `robot.look`,
  `robot.do`.
- Milestone: press "call Ah-Ah" in a web page and Ah-Ah turns and `greet`s.

**Phase 2: the spine**
- Needs/mood tick at 1 Hz per duck on the Jetson, state in SQLite or Redis, and the safety
  supervisor.
- Milestone: left alone for an hour, both do believable things, and rest when the battery is low.

**Phase 3: eyes and memory**
- VLM captions (Olares by day, a small model on the Jetson as a fallback), the Duckdex, and
  the landmark scene graph. Optionally the room camera.
- Milestone: show Ah-Ah a ball, move it behind the chair, and ask "where's the ball?"

**Phase 4: talking**
- The duck bus with the earshot rule, chirp-phrase performance and subtitles, then the room mic
  with wake words and whisper.cpp.
- Milestone: Ah-Ah finds the yellow ball, tells Tee-Tee, and Tee-Tee goes and finds it.

**Phase 5: the dojo**
- Train, validate in `duck-sim`, run test-pen trials, keep or roll back, then teach the other duck.
- Upstream proposals to Pollen: `robot.voice` (drive the live synth for duck-speak), raw audio
  playback, a shared mic stream, and the spine itself as the missing autonomous brain.

---

## 10. Gotchas

- **Keep the language model off the motors.** It proposes and the duck's own controller
  decides. Refuse `robot.move` goals toward edges or drops flagged by ToF.
- **LAN only, and stay off the relay.** The repo's remote path uses a hosted rendezvous with
  metered relay bandwidth. Talk to the ducks directly on the LAN, and reach the Pond from
  outside only through LarePass's private VPN.
- **Snapshots, not streams.** A frame every few seconds is plenty for captions. Use WebRTC video
  only when the dashboard is open.
- **Two consumers, one duck.** If the Pond and the phone app both hold a session, check how the
  duck handles it (`robot.remoteSessionActive`) before assuming both can drive.
- **Upstream changes are real changes.** Duck-speak, raw playback and a shared mic each need a
  small addition to the Microduck software. Propose them to Pollen rather than patching the
  robot image, because the updater replaces anything local on the next release.
