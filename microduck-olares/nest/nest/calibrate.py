"""Floor calibration for Reachy's view: pixels on the floor → metres in your room frame.

1. Snapshot what Reachy sees (run on the Jetson, with Reachy's daemon up):

       python -m nest.calibrate snapshot reachy_view.png

2. Put four or more markers on the floor (tape crosses, or the printed tags) spread across the
   area the ducks use, and measure each one's position in your room frame, in metres.
3. Open the PNG in any image viewer that shows pixel coordinates and write one line per marker
   into a CSV: `u,v,x,y` (pixel column, pixel row, room x, room y).
4. Fit, and paste the printed line into config.toml under [reachy]:

       python -m nest.calibrate fit markers.csv

A mean error of a few centimetres is good. Much more than 10 cm means a marker was misread.
"""

from __future__ import annotations

import argparse
import csv
import sys

from .world.room import FloorHomography


def snapshot(path: str) -> None:
    import cv2

    from .reachy import ReachyCamera

    camera = ReachyCamera()
    try:
        for _ in range(30):
            frame = camera.frame()
            if frame is not None:
                cv2.imwrite(path, frame)
                print(f"saved {path} ({frame.shape[1]}×{frame.shape[0]})")
                return
        sys.exit("no frame from Reachy's camera: is the daemon running and the USB cable in?")
    finally:
        camera.close()


def fit(csv_path: str) -> None:
    pixels, floor = [], []
    with open(csv_path, newline="") as handle:
        for row in csv.reader(handle):
            if not row or row[0].strip().startswith("#"):
                continue
            u, v, x, y = (float(value) for value in row[:4])
            pixels.append((u, v))
            floor.append((x, y))
    homography = FloorHomography()
    error = homography.fit(pixels, floor)
    rows = ", ".join("[" + ", ".join(f"{value:.9g}" for value in row) + "]"
                     for row in homography.as_list())
    print(f"# {len(pixels)} markers, mean error {error * 100:.1f} cm")
    print(f"homography = [{rows}]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m nest.calibrate", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot").add_argument("png")
    sub.add_parser("fit").add_argument("csv")
    args = parser.parse_args()
    if args.command == "snapshot":
        snapshot(args.png)
    else:
        fit(args.csv)


if __name__ == "__main__":
    main()
