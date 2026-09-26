# The walkthrough: a Gaussian map of every room

Turns your phone videos of the flat into:

- **a Gaussian map (3D Gaussian splat) of each room**, to orbit around;
- **a floor plan for the Nest**: the flat's outline, walls and furniture. The Pond's Map
  starts from it, and everything the videos saw counts as seen on the day you filmed.

It runs on a machine with an NVIDIA GPU: Olares. It needs no internet while it runs, and
nothing leaves your network.

## Before you film

1. Print `../../tags/anchor-tags.pdf` **at 100 %** and check one tag with a ruler (100 mm).
2. Stick the nine tags up, upright, **centres 20 cm above the floor**, one per spot on the sheet.
3. Measure **two lounge tags** (0 and 1) in the room frame: the same origin and axes you'll use
   for Reachy's floor calibration (SETUP.md, step 4). Write them into `tags.toml`
   (copy `tags.example.toml`).

## Filming

- Daytime, all lights on, **every door open**, and **nobody else in shot**. Frames with a person
  in them are thrown away anyway, but that leaves gaps.
- Lock exposure and focus (tap and hold), no zoom. 4K at 30 fps, or 1080p at 60.
- **One video per room**, named after the room: `lounge.mp4`, `kitchen.mp4`, `hallway.mp4`,
  `bedroom-1.mp4` … Walk slowly along the walls, phone pointing into the room: one loop at chest
  height, one loop **about 20 cm off the floor** (the ducks' view).
- **Film through each doorway**, so a tag from the next room is in the video. That's how
  rooms get placed: a room that shares no tag with an already-placed room can't be put on the map.
- Copy the videos to Olares **over your network**. Check your phone's photo backup isn't
  uploading them first.

## Running it

```bash
docker build -t walkthrough .
# the folder holds videos/ and tags.toml
docker run --rm --gpus all -v /path/to/walkthrough:/data walkthrough /data
```

On Olares, run it from a terminal on the box (or as a custom app with the GPU assigned); it's a
plain Docker image. Start it in the daytime: it keeps the GPU busy for a while, about 10–30
minutes per room depending on the GPU.

Steps, each skipped if already done, so a failed run picks up where it stopped:

| Step | What it does |
|---|---|
| `frames` | 2 frames a second from each video; the blurriest 15 % and **any frame with a person in it** are dropped (a COCO detector; the run refuses to go on without it unless you pass `--allow-people`) |
| `poses` | camera positions with COLMAP, through nerfstudio's `ns-process-data` |
| `tags` | finds the anchor tags in the frames, places them in 3D, and from them works out the scale, the floor and where each room sits in your room frame |
| `train` | trains each room's Gaussian map (`ns-train splatfacto`) and exports `splat.ply` |
| `floorplan` | the floor plan for the Nest, from the Gaussian maps (or COLMAP's points) |

Output in `out/`: `splats/<room>/splat.ply` with `alignment.json` (splat → room frame, metres,
z up, floor at 0), `floorplan.npz` and `floorplan.png` to check by eye, and `report.md`, which
says room by room what was kept, dropped and placed.

**Then copy `out/floorplan.npz` into the Nest's data directory** (`/var/lib/nest` on the
Jetson) and restart the Nest. The Map tab then shows the flat.

## How accurate

In the tests (synthetic rooms, real tag images at phone resolution, detected with OpenCV):

- a room placed by **two measured tags**: within 5 mm;
- a room placed **through one shared tag**: within about 4 cm at 3 m from that tag.

Both are inside the map's 5 cm cells. To place a room more exactly, film through its doorway
so it shares **two** tags with a room that's already placed.

## What's tested and what isn't

Tested here: the geometry (tag triangulation, scale, levelling, placing and chaining rooms), the
floor plan, the file readers, and the `floorplan` step end to end from scene files. **Not run
yet:** the nerfstudio steps (`poses`, `train`) and the person filter. They need the GPU box.
The `ns-*` command lines follow nerfstudio's documented interface; if your nerfstudio version
differs, the report says which step failed and it's a one-line fix in `walkthrough/pipeline.py`.

```bash
pip install -e ".[dev]" && pytest     # the geometry tests; no GPU needed
```
