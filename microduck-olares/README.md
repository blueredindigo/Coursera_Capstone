# Pollen Robotics: Microduck Olares Integration

Two [Microducks](https://github.com/pollen-robotics/microduck) and one
[Olares](https://github.com/beclab/olares) box, turned into a pair of curious, tamagotchi-like
creatures that live in your home, remember you, and grow over time.

> **The premise in one line:** the duck keeps its reflexes, and Olares gives it a soul. The
> 50 Hz body stays on the robot. Memory, mood, dreams, learning and gossip live on hardware you
> own, and nothing leaves the house.

---

## 1. What you're working with

### The Microduck (what the repo actually exposes)

A 25 cm, 800 g biped with an RK3566 (Radxa Zero 3) and a 0.8 TOPS NPU. It runs Rust daemons
that talk one **JSON-RPC** contract, the same calls whether they come from `robotctl`, the phone
app, a gamepad or your own script. These are the parts that matter for a pet:

| Sense / act | How you reach it | Pet use |
|---|---|---|
| Camera | WebRTC H.264 (`:8443` signalling), PNG snapshot `GET http://<duck>:8080/frame` | eyes for the VLM on Olares |
| 8×8 ToF depth + hand tracker | `tof.frame`, `tof.stream` | "a hand is near me", obstacles |
| Petting detector (mic, on-board CNN) | `pet-detect`, runs inside `robotd` and coos | affection meter |
| Duck detector (NPU, finds *other Microducks*) | `[duck_detector] enabled` | the two ducks can **see each other** |
| BLE chorale beacon: stable duck id, RSSI, ~245 spare bytes | `chorale.heard`, `chorale.beacon` | who's nearby, how close, duck-to-duck notes |
| Voice synth with a **per-robot personality seed** | `robot.sound` with tags `chirp greet coo inquire peck tock alarm wheee` | each duck sounds like a different creature |
| Gaze, head, mouth | `robot.look` (point in space), `robot.head`, `robot.mouth` | attention, curiosity, expressions |
| Skills (ONNX policies) | `robot.do`, `robotctl policy add <hf-repo>` | walk, sit, kick, roulade, ground-pick, get up, plus anything you train |
| Locomotion | `robot.move`, `robot.mode` (walk vs roller) | wandering, following |
| State and battery | `robot.state` (battery, thermals, loop health), `robot.subscribe` | hunger and tiredness |

Two things from the repo's own notes shape the plan:

- **The on-robot "brain" isn't ported yet.** `docs/ideas/autonomous_behavior.md` lists a
  16-state machine (Chill, LookAround, Wander, Zoomies, Startle, Nap, Preen, Sneeze, Dance,
  BallPlay, Petted…) with an energy/mood model that exists in the old runtime but not in the
  current daemons. That gap is exactly where Olares fits: build the mind off-board first, and
  move the fast parts on-board later.
- The same file already has social ideas like recognition, greetings, loneliness, Marco Polo,
  follow-the-leader and telephone. With two ducks you can build all of them.

### Olares (what it brings)

A self-hosted personal cloud on Kubernetes, with GPU pooling, one-click apps from Olares Market
(local LLMs, image generation, home automation, workflow tools), Files and backups, private
networking via Headscale/Tailscale and the LarePass app, and HTTPS entrances for apps you host
yourself. In short, it gives you a GPU, a database, a place to keep memories, and a phone app,
all local.

---

## 2. Architecture: body, spine, soul

```
                ┌──────────────────────── Olares (home server, GPU) ─────────────────────────┐
                │                                                                             │
                │   ┌──────────── "Pond" service (your app, one Deployment) ─────────────┐   │
                │   │  duck-agent: Pip   duck-agent: Wren   ← one mind per duck           │   │
                │   │     │ needs/mood tick (1 Hz)  │ memory  │ relationship graph          │   │
                │   └─────┼─────────────────────────┼─────────┼──────────────────────────────┘   │
                │         ▼                         ▼         ▼                                 │
                │   local VLM/LLM (Ollama)    Postgres/Redis   Files: diary, photo album        │
                │   image gen (ComfyUI)       vector memory    Home Assistant (presence, time)  │
                │   microduck_rl on GPU (MuJoCo + PPO) → "dreams" that become new skills       │
                └────────────────────────────▲──────────────────────────────▲──────────────────┘
                           JSON-RPC (WebRTC control lane) + /frame snapshots, LAN only
                                             │                              │
                ┌─────────── Duck "Pip" ─────┴───┐            ┌────────── Duck "Wren" ───┴────┐
                │ robotd 50 Hz · pet-detect ·    │◄── BLE ───►│ robotd 50 Hz · pet-detect ·    │
                │ tofd · duck-detect · voice     │ beacon+RSSI│ tofd · duck-detect · voice     │
                └────────────────────────────────┘            └────────────────────────────────┘
```

**Three speeds, three places:**

1. **Reflexes (on the duck, milliseconds).** Balance, get-up, petting coo, obstacle stop.
   These never wait on the network.
2. **Spine (on Olares, ~1 Hz).** A needs/mood simulation. It reads `robot.state`,
   `chorale.heard` and `tof` and picks the next behavior with `robot.do`, `robot.look`,
   `robot.sound` and `robot.move`. It's cheap, deterministic and works even when the LLM is off.
3. **Soul (on Olares, every 30 s to a few minutes).** The VLM looks at a snapshot, the LLM
   writes an inner monologue, updates memory, and nudges the spine's goals ("go investigate the
   new thing on the rug"). It never drives motors directly. It suggests, and the spine decides.

Why that split matters: an LLM that says "walk forward" at the wrong moment walks the duck off a
table. Keep the language model in charge of *intent* and let the robot keep charge of its body.

---

## 3. The tamagotchi core

A classic tamagotchi has hunger, happiness and discipline. These ducks get needs that come from
**real sensors**, so looking after them is a physical act, not a button in an app.

| Need | Rises when | Satisfied by (real, physical) | What you see |
|---|---|---|---|
| **Energy** | walking, playing, low battery in `robot.state` | putting it on the charger, a nap | slower gait, drooping head, yawning `coo`, sits down |
| **Curiosity / "hunger"** | time since it saw something *new* | **showing it novel things**, letting it explore | `inquire` chirps, looks around, wanders off |
| **Affection** | time since last petting | scratching its head (`pet-detect`), a hand near its face (ToF) | follows you, `greet` when you walk in |
| **Social** | time since it saw the other duck | the two ducks meeting (BLE RSSI + duck detector) | calls out when alone, gets giddy on approach |
| **Play** | long calm periods | rolling it a ball, gamepad play, a game | Zoomies, kicks, roulades |

### Curiosity is food

This is the key idea: **the ducks eat novelty.**

- You "feed" a duck by putting an object in front of it. The duck looks at it (`robot.look`),
  Olares grabs a `/frame`, and the local VLM names it ("a green ceramic mug with a chipped handle").
- If it has **never seen it before**, that's a full meal: a happy `wheee`, a little dance, and a
  new entry in its **Duckdex**, a collection album with the photo taken from the duck's own eyes.
- A thing it has seen before is a snack. The same thing ten times in a row makes it bored, and it
  pecks at it and looks away.
- Rare things are treats: a cat, a plant that has flowered, a new person.
- Each duck keeps its **own** Duckdex, so they'll know different things (see Gossip in §4).

### It grows up

Life stages are unlocked by *experience*, not by a timer:

1. **Egg (day 0).** Sits, looks around, chirps. Only reacts to petting and light.
2. **Hatchling.** Wobbly short walks, stays close, a high-pitched voice.
3. **Duckling.** Explores rooms, starts the Duckdex, learns your name (from what you tell it in
   the app).
4. **Adult.** Full wandering, games with the other duck, opinions about objects.
5. **Elder.** Knows the house. Tells "stories" (diary summaries) and teaches the younger duck.

On the robot, "growing" means **loading different skills and speed limits**: early stages cap
`robot.move` speed and load fewer skills, and later stages `robotctl policy add` new ones.
You'll actually see the gait get more confident.

### No death, just mood

Old tamagotchis ran on guilt. Here, neglect makes a duck **mopey** (head down, quieter, keeps
to its corner), and it's thrilled when you come back. Nothing dies and nothing is lost for good.

---

## 4. Ideas, roughly by delight per effort

### Quick wins (a weekend each)

- **Greeting at the door.** Home Assistant sees your phone arrive. The Pond wakes the nearer
  duck, which turns toward the door (`robot.look`), waddles over and does `greet`. Guests get
  `inquire` instead.
- **Morning report.** At breakfast, a duck does a little sit/stand bow and the phone gets a
  message in duck-voice: "Slept 9 hours. Dreamt about the red sock. Wren was being weird."
- **The window watcher.** One duck parks by a window. Every few minutes the VLM describes the
  view, and the duck reacts only when something *changes*: a bird, rain starting, a delivery.
- **Photo album from duck height.** Every Duckdex entry and every "surprise" frame goes to
  Olares Files, organised by duck and day. It's 25 cm tall, so your home looks like a cathedral.
- **Personalities from seeds.** Each duck already has a per-robot voice seed. Use the *same*
  seed to roll temperament traits (bold↔shy, chatty↔quiet, tidy↔chaotic) that bias the spine's
  choices and flavour the LLM's monologue. One duck will be the explorer and the other the homebody.

### The two-duck magic

- **They recognise each other.** BLE gives identity and distance, and the duck detector gives
  direction. When they meet, they turn to face each other and do a greeting that depends on
  their **friendship score**, which grows with every good interaction.
- **Gossip.** When they meet, the Pond lets them "exchange memories": Pip tells Wren about the
  mug it found. Wren now *wants* to see the mug (its curiosity need targets it) and goes looking.
  For the romantic version, send it over the ~245 spare BLE bytes instead, so it works even when
  Olares is down.
- **Teaching.** When one duck learns a new skill (see Dreams below), the other **can't use it until
  it has watched** the first one do it. The duck detector confirms it saw Pip do the roulade, and
  only then does the Pond install the policy on Wren. Wren's first attempt is clumsy on purpose,
  with a lower speed cap, and gets better.
- **Rivalry and making up.** Both ducks want the same ball, and one "wins". The loser sulks for
  ten minutes, and making up is a synchronized head-bob on the shared BLE beat.
- **Duets.** The repo already has a four-part duck chorale synced over BLE with no shared
  clock. Make it *spontaneous*: a low chance when both are happy and together, as the repo's own
  notes suggest ("a surprise duet is a delight, a jukebox is not").
- **Hide and seek / Marco Polo.** One duck hides (you carry it). The other hunts using BLE RSSI
  hot/cold, quacking faster as it gets closer, then switches to the camera for the final approach.
- **Follow the leader, conga line.** RSSI holds the spacing, ToF keeps them from bumping, and the
  duck detector steers. Put on music and it becomes a parade.
- **Separation anxiety.** Keep one duck in another room for a day. The other calls out more
  often, and when they meet again the reunion is big.

### Olares-powered superpowers

- **Dreams that teach skills.** At night, while the ducks nap on their chargers, Olares's GPU
  runs [microduck_rl](https://github.com/pollen-robotics/microduck_rl) (MuJoCo + PPO) on a
  "dream" chosen from the day: "Wren kept trying to reach the couch cushion, so train a
  step-up." The Pond validates the new ONNX policy in `scripts/duck-sim` first. If it passes,
  the duck wakes up with a new trick and a matching diary entry: "I dreamt I could climb." This
  is the one feature a cloud tamagotchi could never have.
- **Dream images.** Alongside that, ComfyUI or a Flux model renders a picture of the dream from
  the day's Duckdex, for the morning report.
- **Diaries and memory.** Each duck writes a short daily diary in its own voice, from its events
  plus VLM captions, stored as markdown in Files and embedded into a vector store. Ask a duck
  "when did you last see my keys?" and it answers from memory, with the photo. It's a genuinely
  useful pet.
- **A map of home.** Odometry plus the novelty grid from the old runtime gives each duck a
  "territory". The Pond draws it: "Pip has explored 63% of the living room. The hallway is
  unknown and frightening."
- **Curiosity as surprise.** Keep a caption per spot on the map. When the VLM's description of a
  familiar place *differs* from memory ("the chair has moved", "there's a box by the door"),
  that's a surprise, and the duck walks over to investigate. Curiosity comes from prediction
  error, which is how real curiosity models work.
- **The Pond dashboard.** A small web app hosted on Olares and reachable from your phone through
  LarePass. It shows both ducks' needs as little bars, a live camera per duck, the Duckdex, the
  diaries, the friendship meter, and buttons to "call" a duck or start a game.
- **Home Assistant hooks.** Ducks huddle together when it rains, get sleepy when the lights dim,
  and get excited on a calendar birthday. Keep these optional so they stay pets, not
  notification speakers.

### Wild ones

- **Culture.** Give each duck a small vocabulary of made-up "words", which are short sequences
  of its voice tags. When the ducks gossip, words spread and mutate, and in a month they share a
  dialect you didn't design.
- **Tiny economy.** The Duckdex is worth "shells". Ducks trade knowledge for shells and hoard
  favourite objects by always going back to look at them.
- **Seasons.** The personality drifts slowly with experience. A shy duck that gets petted a lot
  becomes bolder, and one that gets knocked over a lot becomes careful.
- **Letters.** Once a week each duck "writes" you a letter, generated from its memory and printed
  or emailed, with a photo it chose itself.

---

## 5. Build plan

**Phase 0: plumbing (one evening)**
- Name both ducks: `sudo robotctl system set-name pip` and `… wren`. Every board flashed from one
  image is called `radxa-zero3`, so two ducks on one network will collide otherwise.
- Put both ducks and Olares on the same LAN. Confirm `http://<duck>:8080/` (console) and
  `curl http://<duck>:8080/frame -o f.png` from the Olares host.
- Enable the duck detector (`robotctl configure`, `[duck_detector] enabled`) and chorale
  consent (`[chorale] accept`) on both.

**Phase 1: a Pond that can poke a duck**
- One Python service, packaged as an Olares app (Docker image plus an Olares Application Chart).
- Reuse `spaces/shared/control.py` (`Rpc`, which doesn't care about transport) and open the
  WebRTC control lane over the LAN signalling at `ws://<duck>:8443` to send JSON-RPC: `robot.subscribe`, `robot.sound`, `robot.look`,
  `robot.do`.
- Milestone: from your phone, press "call Pip" and Pip turns toward the camera and says `greet`.

**Phase 2: the spine**
- A needs/mood tick at 1 Hz per duck, with state in Postgres or Redis. It picks behaviors from a
  table: needs × personality → weighted choice among {look around, wander, nap, seek other duck,
  seek human, play}.
- Milestone: leave them alone for an hour and they do believable things by themselves.

**Phase 3: the soul**
- A local VLM for captions (any small vision model Ollama can serve), a local LLM for the
  monologue and diary, and a vector memory. Add the Duckdex and "curiosity is food".
- Milestone: show Pip a new object and it gets excited. Show it again tomorrow and it's
  a snack.

**Phase 4: two ducks**
- Friendship graph, greetings, gossip, teaching, spontaneous duets.

**Phase 5: dreams**
- A nightly job: pick a dream, train with microduck_rl on the GPU, validate in `duck-sim`,
  install with `robotctl policy add`, and write the diary entry.

---

## 6. Gotchas worth knowing up front

- **Keep the LLM off the motors.** It proposes and the spine disposes. Clamp speeds and
  refuse `robot.move` near edges using ToF.
- **Stay on the LAN.** Olares and the ducks on the same network means no relay, no metering and
  no rendezvous. The repo's remote path goes through a Hugging Face–hosted rendezvous with a
  10 GB/month relay allowance, and a pet that watches all day would burn through it. For remote
  viewing, go through the Pond via LarePass instead of connecting to the ducks directly.
- **Snapshots, not streams.** A VLM needs a frame every few seconds, not 30 fps, so
  `GET /frame` is plenty. Use the WebRTC video only when the dashboard is open.
- **Microphone audio doesn't leave the robot today.** Petting and sound events are classified
  on-board. Speech understanding (Whisper on Olares) would need a small audio forwarder on the
  duck. It's doable, but it's a real change, and worth proposing upstream rather than hacking in.
- **A new policy can fall over.** Always run a dreamt skill in `scripts/duck-sim` first, and
  keep `sudo robotctl policy reset` handy.
- **Social behavior is opt-in.** The repo's rule is "anything social is opt-in, and off means
  invisible". Keep that for the Pond too.
- **Upstream opportunity.** The spine (needs, mood, state machine) is exactly the missing
  "autonomous brain" in the Microduck roadmap. Prototype it in Python on Olares, then offer the
  design back to Pollen as the port.
