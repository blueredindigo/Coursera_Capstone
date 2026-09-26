"""A floor plan for the Nest, from what the walkthrough reconstructed.

Input, per room: points in the room frame (z up, floor at 0), preferably the centres of the
Gaussian splat (dense), else COLMAP's sparse points, and where the camera walked. Output: one
`floorplan.npz` with a 5 cm grid over the whole flat:

* `floor`: cells that are part of the flat: where points lie on the floor, and where you walked
  (a phone that went there proves there's floor);
* `blocked`: walls and furniture: cells with enough points between 6 cm and 1.6 m up;
* `x0`, `y0`, `cell`, `filmed_at`: where the grid sits and when you filmed.

The Nest loads it from its data directory: its Map tab then shows the flat's real outline, and
everything the walkthrough saw counts as seen at the time you filmed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

FLOOR_BAND = 0.04       # m: a point this close to z = 0 is floor
OBSTACLE = (0.06, 1.6)  # m: points in this band are walls or furniture (ducks can't pass)
WALKED_RADIUS = 0.45    # m: floor around where the camera went


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy > r * r:
                continue
            out |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return out


def _erode(mask: np.ndarray, r: int) -> np.ndarray:
    return ~_dilate(~mask, r)


def build(rooms: dict[str, dict[str, np.ndarray]], cell: float = 0.05, filmed_at: float = 0.0,
          min_points: int = 3) -> dict[str, np.ndarray | float]:
    """`rooms`: name → {"points": N×3, "walked": M×3 camera positions}, all in the room frame."""
    everything = np.concatenate([np.concatenate([r["points"], r["walked"]]) for r in
                                 rooms.values()])
    # A wide margin: the dilations below wrap around the grid's edges.
    lo = everything[:, :2].min(axis=0) - 0.8
    hi = everything[:, :2].max(axis=0) + 0.8
    w, h = (np.ceil((hi - lo) / cell)).astype(int)
    floor_hits = np.zeros((h, w), int)
    obstacle_hits = np.zeros((h, w), int)
    walked = np.zeros((h, w), bool)

    def cells(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        j = ((p[:, 0] - lo[0]) / cell).astype(int).clip(0, w - 1)
        i = ((p[:, 1] - lo[1]) / cell).astype(int).clip(0, h - 1)
        return i, j

    for name, r in rooms.items():
        pts = r["points"]
        on_floor = pts[np.abs(pts[:, 2]) < FLOOR_BAND]
        in_band = pts[(pts[:, 2] > OBSTACLE[0]) & (pts[:, 2] < OBSTACLE[1])]
        np.add.at(floor_hits, cells(on_floor), 1)
        np.add.at(obstacle_hits, cells(in_band), 1)
        cam = np.zeros((h, w), bool)
        cam[cells(r["walked"])] = True
        cam = _dilate(cam, max(1, int(round(WALKED_RADIUS / cell))))
        walked |= cam
    blocked = obstacle_hits >= min_points
    floor = (floor_hits >= 1) | walked
    floor = _erode(_dilate(floor, 2), 2)          # close small gaps between points
    blocked &= _dilate(floor, 2)                  # furniture and walls that border the flat
    return {"x0": float(lo[0]), "y0": float(lo[1]), "cell": float(cell),
            "floor": floor | blocked, "blocked": blocked, "filmed_at": float(filmed_at),
            "rooms": np.array(sorted(rooms))}


def save(plan: dict, path: str | Path) -> None:
    np.savez_compressed(path, **plan)


def preview(plan: dict, path: str | Path) -> None:
    """A PNG to check by eye: walls dark, floor light, outside grey; north (+y) up."""
    import cv2

    floor, blocked = plan["floor"], plan["blocked"]
    img = np.full(floor.shape + (3,), 225, np.uint8)
    img[floor] = (190, 214, 232)
    img[blocked] = (70, 51, 59)
    img = cv2.resize(img[::-1], None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(path), img)
