# Setting up the Nest

What you do by hand, in the order you'll do it. Each step says how to check it worked.

The ducks arrive at Christmas. Everything in **Part 1** works without them: the Nest runs with
Reachy alone, the ducks are eggs in their beds, and Reachy maps the lounge and keeps a diary of
what it sees. **Part 2** is the walkthrough. **Part 3** is hatch day.

---

# Part 1: before the ducks (Reachy, the Jetson, Olares)

## 1. A fixed address for the Jetson

In your router, reserve a DHCP address for the Jetson (for example `192.168.1.40`). Reserve two
more for the ducks now (`.41` Ah-Ah, `.42` Tee-Tee) so the config is ready for Christmas.

## 2. Reachy Mini Lite on the Jetson

1. Plug Reachy Lite's USB-C into the Jetson; power Reachy from its own supply (it does not
   charge over USB).
2. Install the SDK on the Jetson **host** following Pollen's Lite guide. Pollen's docs say the
   desktop app may not work on Jetson (ARM64), so install the Python SDK directly (GStreamer
   first, then `pip install reachy-mini`).
3. Run its daemon (`reachy-mini-daemon`) and make it start at boot.

**Check:** `curl http://localhost:8000/api/daemon/status` answers.

## 3. The Nest itself

On the Jetson:

```bash
git clone <this repo> && cd microduck-olares/nest
python3 -m venv /opt/nest/venv
/opt/nest/venv/bin/pip install ".[room-eye]"     # Reachy's camera, for the map and the diary
sudo mkdir -p /etc/nest /var/lib/nest
sudo cp config.example.toml /etc/nest/config.toml   # then edit it: see below
```

In `config.toml`, keep both `[[duck]]` entries with the addresses you reserved: a duck that
isn't there yet is simply "not switched on", and nothing waits for it. Set the landmarks you
know (the sofa, the TV stand, both beds) in metres from a corner you choose. That corner and
those axes are **your room frame**. Write them down; the floor calibration and the walkthrough
use the same frame.

Run it by hand:

```bash
/opt/nest/venv/bin/python -m nest --config /etc/nest/config.toml
```

or install `deploy/nest.service` so it starts at boot. The Pond is at `http://<jetson>:8090`.

**Check:** the Pond says "Reachy is awake", and both ducks show as eggs in their beds.
- **Goodnight at 19:45:** Reachy wiggles and goes to sleep.
- **Good morning at 9:00:** it wakes and looks out over the lounge again.
- **Off and on:** switch Reachy off and on, and within 20 seconds it's back and awake. (In quiet
  hours it stays resting.)
- **The eggs:** now and then Reachy leans over to peek at one. The Night tab lists it under
  Routines.

## 4. Reachy's view of the floor (the map)

This turns Reachy's camera into the first part of the fog-of-war map: the lounge floor it can
see.

```bash
python -m nest.calibrate snapshot reachy_view.png  # puts Reachy in its watch pose first
```

Put four or more markers on the floor (tape crosses), spread over the area Reachy sees, and
measure each one in your room frame. Open the PNG in any viewer that shows pixel coordinates, and
write one line per marker into `markers.csv` as `u,v,x,y` (pixel column, pixel row, room x,
room y). Then:

```bash
python -m nest.calibrate fit markers.csv           # paste the homography line into config.toml
```

A mean error of a few centimetres is good. Set `camera = true` under `[reachy]` and restart.

**Check:** the Pond's **Map** tab shows the lounge floor in view (bright) with a dashed outline
of what Reachy sees. The rest is black. Overnight it fades to grey, and it clears again in the
morning.

> The calibration only holds while Reachy looks straight out (its "watch pose"). The Nest
> returns it there after every glance, and only maps while it's there.

## 5. Reachy's diary (a local vision model)

1. Install Ollama on the Jetson (or on Olares for daytime use) and pull a small vision model:
   `ollama pull qwen2.5vl:3b`.
2. Set `enabled = true` under `[captioner]`.

Every 10 minutes by day (`reachy_every_s`), the model looks at the lounge through Reachy's camera.
The feed says what's new ("spotted a yellow ball under the table"), what moved, and what's gone,
and the Map tab lists what Reachy has seen. On hatch day each duck inherits all of it.

Small models are wrong sometimes. Something only counts as gone after three looks without it.

## 6. The Nest, physically

On the TV stand: Reachy, the Jetson, and the dual charger, **where Reachy can see it**. Add two
bed spots as landmarks in the config (`"ah-ah's bed" = [x, y]`), plus a box for spares and
tools.

Put a cheap NFC sticker on each battery pack (A–D), and charge the new packs a few times
now. Log swaps with the Pond's pit-stop form, or with a Home Assistant NFC automation that POSTs
to `/api/pitstop`.

## 7. Quiet Olares

Noctua fans, and cap the GPU's power (`nvidia-smi -pl <watts>`, around 60–70% of stock). Nothing
in the Nest depends on Olares at night.

---

# Part 2: the walkthrough

1. Print `../tags/anchor-tags.pdf` **at 100 %**, and check one tag with a ruler (100 mm).
2. Stick the nine tags up, **centres 20 cm above the floor**, one per spot on the sheet.
3. Measure tags 0 and 1 (lounge) in your room frame, and film one video per room.
4. Run the pipeline on Olares.

Everything is in [`../olares/walkthrough/README.md`](../olares/walkthrough/README.md).

When it's done, copy `out/floorplan.npz` to `/var/lib/nest/` and restart the Nest.

**Check:** the Map tab shows the whole flat's outline, walls and furniture, all in fog "from
your walkthrough", with the lounge bright where Reachy is looking.

---

# Part 3: hatch day (Christmas)

## 8. Name the ducks

Every Microduck flashed from one image is called `radxa-zero3`, so two on one network collide.
On each duck (ssh, or `duckctl` over Bluetooth):

```bash
sudo robotctl system set-name ah-ah      # on the first duck
sudo robotctl system set-name tee-tee    # on the second
```

**Check:** `robotctl system info` shows the new name. Check that the router gives each duck the
address you reserved in step 1.

## 9. Turn on what the Nest listens to

On both ducks, in `sudo robotctl configure`:

- `[duck_detector] enabled`: each duck can see the other (earshot, greetings, following);
- `[chorale] accept`: the consent switch for anything social between ducks.

**Check:** open `http://<duck>:8080/` in a browser and press connect. The console shows video,
and detection boxes appear when the other duck is in view. Close the page afterwards: only one
program should drive a duck at a time.

```bash
scripts/check_lan.sh 192.168.1.41 192.168.1.42
```

It checks each duck's console page, a camera frame and the signalling port, plus Reachy's
daemon. Fix anything marked FAIL.

## 10. Hatch

Switch Ah-Ah on and put it on its bed. Within half a minute the Nest connects, and the egg
hatches in the Pond:
- Reachy turns to it and wiggles hello;
- Ah-Ah gets its first good morning;
- it learns from Reachy where everything in the lounge is.

Then Tee-Tee.

**Check:** the feed says "hatched! Welcome home, Ah-Ah" and the API version (the Nest was
written against v37; say if yours differs). Press **Look at me** in the Care tab: Ah-Ah looks up
and says hello.

## 11. The room eye for the ducks

```bash
scripts/get_detector.sh /var/lib/nest/models      # Pollen's duck detector, once
```

Set `detector_model` under `[reachy]`, restart, then press **Roll call** under one duck so Reachy
can tell the two apart.

**Check:** the Map tab shows each duck where it really is, and the floor clears around it.

## 12. Before surgery day

Check the three spare XL330s' firmware is v46 or later (ROBOTIS Dynamixel Wizard and a USB
adapter). Replace only **one** servo per session: `robotd` adopts a new servo automatically only
when exactly one is missing.
