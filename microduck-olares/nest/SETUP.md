# Setting up the Nest (Phase 0)

What you do by hand, once, before the Nest can run. Each step says how to check it worked.

## 1. Name the ducks

Every Microduck flashed from one image is called `radxa-zero3`, so two on one network collide.
On each duck (ssh, or `duckctl` over Bluetooth):

```bash
sudo robotctl system set-name ah-ah      # on the first duck
sudo robotctl system set-name tee-tee    # on the second
```

**Check:** `robotctl system info` shows the new name.

## 2. Fixed addresses

In your router, reserve a DHCP address for each duck and for the Jetson (for example
`192.168.1.41` Ah-Ah, `.42` Tee-Tee, `.40` Jetson). The Nest's config uses these. mDNS names work
from the Jetson's host, but not always from inside containers.

## 3. Turn on what the Nest listens to

On both ducks, in `sudo robotctl configure`:

- `[duck_detector] enabled`: each duck can see the other (earshot, greetings, following);
- `[chorale] accept`: the consent switch for anything social between ducks.

**Check:** open `http://<duck>:8080/` in a browser, press connect; the console shows video, and
detection boxes appear when the other duck is in view.

## 4. Reachy Mini Lite on the Jetson

1. Plug Reachy Lite's USB-C into the Jetson; power Reachy from its own supply (it does not
   charge over USB).
2. Install the SDK on the Jetson **host** following Pollen's Lite guide. Pollen's docs say the
   desktop app may not work on Jetson (ARM64), so install the Python SDK directly (GStreamer
   first, then `pip install reachy-mini`).
3. Run its daemon (`reachy-mini-daemon`) and make it start at boot.

**Check:** `curl http://localhost:8000/api/daemon/status` answers.

## 5. The Nest itself

On the Jetson:

```bash
git clone <this repo> && cd microduck-olares/nest
python3 -m venv /opt/nest/venv
/opt/nest/venv/bin/pip install .            # add [room-eye] for Reachy's camera (Phase 2)
sudo mkdir -p /etc/nest /var/lib/nest
sudo cp config.example.toml /etc/nest/config.toml   # then edit the IPs, landmarks, beds
scripts/check_lan.sh 192.168.1.41 192.168.1.42
```

`check_lan.sh` checks each duck's console page, a camera frame and the signalling port, plus
Reachy's daemon. Fix anything marked FAIL before going on.

Then either run it by hand:

```bash
/opt/nest/venv/bin/python -m nest --config /etc/nest/config.toml
```

or install `deploy/nest.service` so it starts at boot. The Pond is at `http://<jetson>:8090`.

**Check (Phase 1 milestone):** press **Call** under Ah-Ah in the Pond. Ah-Ah looks up and says
`greet`, and Reachy turns toward it once the room eye is on.

> Only one program should drive a duck at a time. If the robot's own console page is open in a
> browser while the Nest runs, close one of them.

## 6. The Nest, physically

On the TV stand: Reachy, the Jetson, the dual charger, two bed spots (add them as landmarks in
the config: `"ah-ah's bed" = [x, y]`), and a box for spares and tools. Put a cheap NFC sticker on
each battery pack (A–D). Log swaps with the Pond's pit-stop form, or with a Home Assistant NFC
automation that POSTs to `/api/pitstop`.

## 7. Quiet Olares

Noctua fans, and cap the GPU's power (`nvidia-smi -pl <watts>`, around 60–70% of stock). Nothing
in the Nest depends on Olares at night.

## 8. Before surgery day

Check the three spare XL330s' firmware is v46 or later (ROBOTIS Dynamixel Wizard and a USB
adapter). Replace only **one** servo per session: `robotd` adopts a new servo automatically only
when exactly one is missing.

## Later: the room eye (Phase 2)

```bash
scripts/get_detector.sh /var/lib/nest/models      # Pollen's duck detector, once
python -m nest.calibrate snapshot reachy_view.png  # then mark 4+ floor points
python -m nest.calibrate fit markers.csv           # paste the homography into config.toml
```

Set `camera = true` under `[reachy]`, restart the Nest, then press **Roll call** under one duck
so Reachy can tell the two apart.
