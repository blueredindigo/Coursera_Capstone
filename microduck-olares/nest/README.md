# The Nest

The always-on mind for **Ah-Ah**, **Tee-Tee** and **Reachy Mini**, running on the Jetson Orin
Nano Super on the TV stand. It is the software half of
[the plan](../README.md), phases 0–3, with groundwork for 4–6.

Everything stays on the home network. The robots' own software always has the final say on
movement: the Nest proposes, and each duck's controller and safety decide.

## Try it without robots

```bash
pip install -e ".[dev]"
python -m nest --sim            # then open http://localhost:8090
pytest                          # 26 tests
```

`--sim` runs a simulated living room: two ducks whose batteries drain, who walk when asked and
see each other, and a Reachy that watches. In the Pond, record "Ah-Ah saw a yellow ball behind
the green chair near the sofa" and watch the message reach Tee-Tee when they are within earshot.

## What it does today

| Plan phase | What's built | How it's been tested |
|---|---|---|
| 0 Plumbing | `SETUP.md` checklist, `scripts/check_lan.sh`, config template, Docker + systemd | script syntax only |
| 1 Talk to everyone | **Duck:** JSON-RPC over the duck's own LAN WebRTC `control` channel (same handshake as its console page), plus `GET /frame`. **Reachy Lite:** its REST API on `localhost:8000` | unit tests, and a WebRTC handshake against a stand-in for the duck's `mediad` |
| 2 Seeing the room | Pollen's duck detector (ONNX) on Reachy's camera, floor calibration, a two-duck tracker with roll call, a navigator that learns each duck's odometry offset | the real detector model runs through the wrapper; tracker, homography and navigator unit-tested |
| 3 Spine and tiredness | needs, personality, the battery → tiredness gauge (with charging detection), behaviours, the safety gate, pit stops and the battery diary, bedtime and wake-up | unit tests and the simulated house |
| 4 The Pond | a status page with every duck's needs, battery, mood, messages and battery health | by eye, in simulation |
| 5 Memory and food | landmark memory, the Duckdex ("curiosity is food"), and a local vision captioner (Ollama) that turns snapshots into sightings | unit tests; the captioner has not met a real model yet |
| 6 Telling each other | the duck bus with the earshot rule, 9-byte messages, chirp-phrase performance, investigate-the-rumour | the full "yellow ball" story in the simulated house |

**Not yet tested against real ducks or a real Reachy.** Everything that touches hardware follows
the robots' published protocols and code (microduck API v37, reachy_mini's daemon), but the
first run in your living room is the real test. Expect to tune: antenna directions, head
angles, the ToF edge check, detector threshold, walking speed.

Later phases (games, the vet check, the dojo, ggwave, field trips, NFC once Pollen exposes it)
build on these pieces and aren't started.

## Layout

```
nest/
  duck/        rpc.py (JSON-RPC), transport.py (WebRTC, unix socket, loopback), client.py
  reachy.py    Reachy Lite REST client, gaze and antenna expressions, camera
  spine/       needs, personality, tiredness, safety, mind (behaviours)
  world/       room (homography, tracker, navigator), scene (landmark memory),
               duckdex, messages (the 9-byte duck language)
  vision/      duck_detector (ONNX), captioner (Ollama)
  bus.py       the duck bus and the earshot rule
  batteries.py pit stops and battery health
  app.py       wires it all together; web.py is the Pond
  sim.py       the simulated living room
  calibrate.py floor calibration for Reachy's view
```

## Things worth knowing

- **The duck's WebRTC channel refuses the `chorale.*` calls**, so Bluetooth presence between
  ducks is not visible to the Nest. Earshot uses Reachy's map and the ducks' own detectors
  instead.
- **`robot.move` has a 500 ms deadman.** Walks are streamed at 10 Hz and always end in a stop.
- **Battery and servo temperature come from `robot.health`**, polled every 5 s. Absent means
  "not known yet", never an empty battery.
- **Charging is inferred** from a rising percentage; the duck has no charging flag on the wire.
- **Tiredness never slows a duck down.** It changes what the duck chooses to do, how it holds its
  head and which sounds it makes, never its gait or speed.
