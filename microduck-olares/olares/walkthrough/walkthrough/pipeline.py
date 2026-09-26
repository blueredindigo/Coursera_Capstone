"""The whole run, room by room. `python -m walkthrough /data/walkthrough`.

    /data/walkthrough/
      videos/lounge.mp4, kitchen.mp4, …    one per room; the file name is the room's name
      tags.toml                            tag size and height, and the tags you measured
      work/                                frames, COLMAP scenes, training runs (big; deletable)
      out/
        splats/<room>/splat.ply            the Gaussian map of each room
        splats/<room>/alignment.json       splat → room frame (metres, z up, floor at 0)
        floorplan.npz, floorplan.png       for the Nest's Map
        report.md                          what happened, room by room

Steps (all by default, or pick with --steps): frames → poses → tags → train → floorplan.
Every step skips work it has already done, so a failed run picks up where it stopped.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import cameras as cams
from . import floorplan as fp
from . import tags as tg

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

STEPS = ["frames", "poses", "tags", "train", "floorplan"]


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


class Walkthrough:
    def __init__(self, root: Path, fps: float = 2.0, iterations: int = 15000,
                 allow_people: bool = False):
        self.root = root
        self.videos = sorted((root / "videos").glob("*.*"))
        self.work, self.out = root / "work", root / "out"
        self.fps, self.iterations, self.allow_people = fps, iterations, allow_people
        settings = tomllib.loads((root / "tags.toml").read_text())
        self.tag_size = float(settings.get("size_mm", 100)) / 1000
        self.tag_height = float(settings.get("centre_height_cm", 20)) / 100
        self.measured = {int(t["id"]): tg.KnownTag(float(t["x"]), float(t["y"]),
                                                   math.radians(t["facing_deg"])
                                                   if "facing_deg" in t else None)
                         for t in settings.get("tag", [])}
        self.report: dict[str, list[str]] = {}

    def rooms(self) -> list[str]:
        return [v.stem for v in self.videos]

    def note(self, room: str, text: str) -> None:
        print(f"[{room}] {text}", flush=True)
        self.report.setdefault(room, []).append(text)

    # ── frames ─────────────────────────────────────────────────────────────────

    def frames(self, room: str, video: Path) -> None:
        raw, kept = self.work / "raw" / room, self.work / "frames" / room
        if kept.exists() and any(kept.iterdir()):
            return
        raw.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", f"fps={self.fps}",
             "-q:v", "2", str(raw / "f_%05d.jpg")])
        paths = sorted(raw.glob("*.jpg"))
        sharp = sharpness(paths)
        cutoff = float(np.percentile(list(sharp.values()), 15)) if sharp else 0
        blurry = {p for p, s in sharp.items() if s < cutoff}
        people = set() if self.allow_people else people_in([p for p in paths if p not in blurry])
        kept.mkdir(parents=True, exist_ok=True)
        for p in paths:
            if p not in blurry and p not in people:
                shutil.copy2(p, kept / p.name)
        shutil.rmtree(raw)
        self.note(room, f"{len(paths)} frames: {len(blurry)} blurry and {len(people)} with a "
                        f"person dropped, {len(paths) - len(blurry) - len(people)} kept")

    # ── poses ──────────────────────────────────────────────────────────────────

    def scene(self, room: str) -> Path:
        return self.work / "scenes" / room

    def poses(self, room: str) -> None:
        if (self.scene(room) / "transforms.json").exists():
            return
        run(["ns-process-data", "images", "--data", str(self.work / "frames" / room),
             "--output-dir", str(self.scene(room)), "--matching-method", "sequential"])

    # ── tags ───────────────────────────────────────────────────────────────────

    def find_tags(self, room: str) -> dict[int, tg.TagObs]:
        import cv2

        cameras, _ = cams.load_cameras(self.scene(room) / "transforms.json")
        detections = {}
        for cam in cameras:
            image = cv2.imread(str(self.scene(room) / cam.name))
            if image is not None:
                found = tg.detect(image)
                if found:
                    detections[cam.name] = found
        placed = tg.place_tags(cameras, detections)
        self.note(room, f"tags placed: {sorted(placed) or 'none'}")
        return placed

    def align(self) -> dict[str, tg.Alignment]:
        cache = self.work / "tags.json"
        if cache.exists():
            data = json.loads(cache.read_text())
            scenes = {room: {int(i): tg.TagObs(int(i), np.array(c), 0) for i, c in t.items()}
                      for room, t in data.items()}
        else:
            scenes = {room: self.find_tags(room) for room in self.rooms()}
            cache.write_text(json.dumps({room: {i: t.corners.tolist() for i, t in obs.items()}
                                         for room, obs in scenes.items()}))
        placed, missing = tg.chain(scenes, self.measured, self.tag_size, self.tag_height)
        for room, a in placed.items():
            self.note(room, f"placed in the room frame by tags {a.anchored_by}; the tags "
                            f"agree on the scale within {a.tag_spread_mm:.0f} mm")
        for room in missing:
            self.note(room, "NOT placed: it shares no tag with a placed room. Film through "
                            "the doorway so a tag from the next room is in the video.")
        return placed

    # ── training ───────────────────────────────────────────────────────────────

    def run_dir(self, room: str) -> Path:
        return self.work / "runs" / room / "splatfacto" / "walkthrough"

    def train(self, room: str) -> None:
        splat = self.out / "splats" / room / "splat.ply"
        if splat.exists():
            return
        if not (self.run_dir(room) / "config.yml").exists():
            run(["ns-train", "splatfacto", "--data", str(self.scene(room)),
                 "--output-dir", str(self.work / "runs"), "--experiment-name", room,
                 "--timestamp", "walkthrough", "--max-num-iterations", str(self.iterations),
                 "--viewer.quit-on-train-completion", "True"])
        run(["ns-export", "gaussian-splat", "--load-config",
             str(self.run_dir(room) / "config.yml"), "--output-dir", str(splat.parent)])
        self.note(room, f"Gaussian map trained ({self.iterations} iterations)")

    def w_from_splat(self, room: str) -> np.ndarray:
        """nerfstudio trains in its own normalised frame: X_n = s·(T·X_w)."""
        data = json.loads((self.run_dir(room) / "dataparser_transforms.json").read_text())
        t = np.eye(4)
        t[:3] = np.array(data["transform"])
        n_from_w = np.diag([data["scale"]] * 3 + [1.0]) @ t
        return np.linalg.inv(n_from_w)

    # ── floor plan ─────────────────────────────────────────────────────────────

    def room_points(self, room: str, a: tg.Alignment) -> np.ndarray:
        splat = self.out / "splats" / room / "splat.ply"
        if splat.exists() and (self.run_dir(room) / "dataparser_transforms.json").exists():
            v = cams.read_ply(splat, ["x", "y", "z", "opacity"])
            v = v[1 / (1 + np.exp(-v[:, 3])) > 0.3]
            room_from_splat = a.transform @ self.w_from_splat(room)
            (splat.parent / "alignment.json").write_text(json.dumps(
                {"room_from_splat": room_from_splat.tolist(), "units": "metres",
                 "up": "+z", "floor": "z = 0"}, indent=1))
            return cams.apply(room_from_splat, v[:, :3])
        cameras, applied = cams.load_cameras(self.scene(room) / "transforms.json")
        sparse = next(self.scene(room).glob("colmap/sparse/0/points3D.bin"), None)
        if sparse is None:
            return np.zeros((0, 3))
        return a.to_room(cams.apply(applied, cams.read_points3d_bin(sparse)))

    def floorplan(self, placed: dict[str, tg.Alignment]) -> None:
        rooms = {}
        for room, a in placed.items():
            cameras, _ = cams.load_cameras(self.scene(room) / "transforms.json")
            walked = a.to_room(np.array([c.c2w[:3, 3] for c in cameras]))
            rooms[room] = {"points": self.room_points(room, a), "walked": walked}
        if not rooms:
            print("no room could be placed, so there's no floor plan", file=sys.stderr)
            return
        filmed_at = min(v.stat().st_mtime for v in self.videos)
        plan = fp.build(rooms, filmed_at=filmed_at)
        fp.save(plan, self.out / "floorplan.npz")
        fp.preview(plan, self.out / "floorplan.png")
        print(f"floor plan: {plan['floor'].shape[1]}×{plan['floor'].shape[0]} cells → "
              f"{self.out / 'floorplan.npz'}")

    # ── all of it ──────────────────────────────────────────────────────────────

    def go(self, steps: list[str]) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        for video in self.videos:
            room = video.stem
            if "frames" in steps:
                self.frames(room, video)
            if "poses" in steps:
                self.poses(room)
        placed = self.align() if {"tags", "floorplan"} & set(steps) else {}
        if "train" in steps:
            for room in self.rooms():
                self.train(room)
        if "floorplan" in steps:
            self.floorplan(placed)
        lines = ["# Walkthrough report", ""]
        for room in self.rooms():
            lines += [f"## {room}", ""] + [f"- {t}" for t in self.report.get(room, [])] + [""]
        (self.out / "report.md").write_text("\n".join(lines))


def sharpness(paths: list[Path]) -> dict[Path, float]:
    """Variance of the Laplacian: low means motion blur."""
    import cv2

    out = {}
    for p in paths:
        grey = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if grey is not None:
            out[p] = float(cv2.Laplacian(cv2.resize(grey, (640, 360)), cv2.CV_64F).var())
    return out


def people_in(paths: list[Path], threshold: float = 0.5) -> set[Path]:
    """Frames with a person in them (a COCO detector on the GPU): they never leave this step."""
    try:
        import torch
        import torchvision
        from torchvision.io import read_image
    except ImportError as exc:
        sys.exit(f"The person filter needs torch and torchvision ({exc}). Refusing to go on "
                 "without it, so nobody ends up in a Gaussian map. Use --allow-people only if "
                 "nobody was in shot.")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    weights = torchvision.models.detection.FasterRCNN_MobileNet_V3_Large_FPN_Weights.DEFAULT
    model = torchvision.models.detection.fasterrcnn_mobilenet_v3_large_fpn(weights=weights)
    model.eval().to(device)
    person = weights.meta["categories"].index("person")
    found = set()
    with torch.no_grad():
        for p in paths:
            image = read_image(str(p)).float().div(255).to(device)
            result = model([image])[0]
            hits = (result["labels"] == person) & (result["scores"] >= threshold)
            if bool(hits.any()):
                found.add(p)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(prog="walkthrough", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--steps", default=",".join(STEPS))
    parser.add_argument("--fps", type=float, default=2.0, help="frames kept per second of video")
    parser.add_argument("--iterations", type=int, default=15000)
    parser.add_argument("--allow-people", action="store_true",
                        help="skip the person filter (only if nobody was in shot)")
    args = parser.parse_args()
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    unknown = set(steps) - set(STEPS)
    if unknown:
        parser.error(f"unknown steps: {sorted(unknown)}; choose from {STEPS}")
    Walkthrough(args.root, args.fps, args.iterations, args.allow_people).go(steps)
