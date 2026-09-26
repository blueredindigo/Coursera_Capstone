"""The anchor tags: find them in the frames, place them in 3D, and let them fix the scale,
the floor and where each room sits in your room frame.

The tags are printed 100 mm across, stuck upright on walls with their centres all at the same
height (20 cm). That's enough to recover what a video alone can't know:

* **scale**: a tag's edge is 100 mm;
* **which way is up**: every tag's own "up" points at the ceiling;
* **the floor**: 20 cm below the tags' centres;
* **where the room is**: tags whose position you measured (two in the lounge, or one plus the
  direction it faces) pin the lounge to the same room frame as Reachy's floor calibration.
  Other rooms are pinned by tags they share with a room already placed (the hallway tag is in
  the lounge's video too, because you film through the doorways).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .cameras import Camera


@dataclass
class TagObs:
    """One tag in one scene's world W: its corners (TL, TR, BR, BL, as printed)."""
    tag_id: int
    corners: np.ndarray  # 4×3
    views: int

    @property
    def centre(self) -> np.ndarray:
        return self.corners.mean(axis=0)

    @property
    def edge(self) -> float:
        c = self.corners
        return float(np.mean([np.linalg.norm(c[(i + 1) % 4] - c[i]) for i in range(4)]))

    @property
    def up(self) -> np.ndarray:
        c = self.corners
        v = (c[0] + c[1]) - (c[3] + c[2])
        return v / np.linalg.norm(v)

    @property
    def right(self) -> np.ndarray:
        c = self.corners
        v = (c[1] + c[2]) - (c[0] + c[3])
        return v / np.linalg.norm(v)

    @property
    def normal(self) -> np.ndarray:
        """Out of the tag's face, into the room."""
        n = np.cross(self.right, self.up)
        return n / np.linalg.norm(n)


@dataclass
class KnownTag:
    """A tag's place in your room frame: measured with a tape, or learned from a placed room."""
    x: float
    y: float
    facing: float | None = None  # radians: the direction its face points, in the room frame
    measured: bool = True


def detect(image, dictionary=None) -> dict[int, np.ndarray]:
    """Tag corners in one image: id → 4×2 pixels (TL, TR, BR, BL)."""
    import cv2

    dictionary = dictionary or cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    params = cv2.aruco.DetectorParameters()
    # Sub-pixel corners: whole-pixel ones shrink a small tag by a pixel, and that biases the
    # scale of the whole room by a few percent.
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(dictionary, params)
    grey = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(grey)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).astype(float) for i, c in zip(ids.flatten(), corners)}


def undistort(camera: Camera, pixels: np.ndarray) -> np.ndarray:
    if not np.any(camera.dist):
        return pixels
    import cv2

    pts = cv2.undistortPoints(pixels.reshape(-1, 1, 2), camera.k, camera.dist, P=camera.k)
    return pts.reshape(-1, 2)


def triangulate(projections: list[np.ndarray], pixels: list[np.ndarray]) -> np.ndarray:
    """One point seen in several views: linear least squares (DLT)."""
    rows = []
    for p, (u, v) in zip(projections, pixels):
        rows.append(u * p[2] - p[0])
        rows.append(v * p[2] - p[1])
    _, _, vt = np.linalg.svd(np.array(rows))
    x = vt[-1]
    return x[:3] / x[3]


def place_tags(cameras: list[Camera], detections: dict[str, dict[int, np.ndarray]],
               max_error_px: float = 4.0) -> dict[int, TagObs]:
    """Each tag seen in at least two frames, placed in W.

    `detections`: image name → {tag id → 4×2 pixels}. Views that disagree with the rest by more
    than `max_error_px` are dropped one at a time, worst first."""
    by_name = {c.name: c for c in cameras}
    seen: dict[int, list[tuple[Camera, np.ndarray]]] = {}
    for name, tags in detections.items():
        cam = by_name.get(name)
        if cam is None:
            continue
        for tag_id, px in tags.items():
            seen.setdefault(tag_id, []).append((cam, undistort(cam, px)))
    placed = {}
    for tag_id, views in seen.items():
        while len(views) >= 2:
            projections = [c.projection() for c, _ in views]
            corners = np.array([triangulate(projections, [px[k] for _, px in views])
                                for k in range(4)])
            errors = [float(np.mean(np.linalg.norm(c.project(corners) - px, axis=1)))
                      for c, px in views]
            worst = int(np.argmax(errors))
            if errors[worst] <= max_error_px or len(views) == 2:
                if errors[worst] <= max_error_px * 2:
                    placed[tag_id] = TagObs(tag_id, corners, len(views))
                break
            views = views[:worst] + views[worst + 1:]
    return placed


@dataclass
class Alignment:
    """W → room frame (metres, z up, floor at z = 0)."""
    room: str
    transform: np.ndarray            # 4×4 similarity
    scale: float
    anchored_by: list[int] = field(default_factory=list)
    tag_spread_mm: float = 0.0       # how much the tags disagree about the scale

    def to_room(self, points: np.ndarray) -> np.ndarray:
        return (self.transform[:3, :3] @ points.T).T + self.transform[:3, 3]


def _rotation_to_z(up: np.ndarray) -> np.ndarray:
    """A rotation taking `up` to +z."""
    up = up / np.linalg.norm(up)
    z = np.array([0.0, 0.0, 1.0])
    v, c = np.cross(up, z), float(np.dot(up, z))
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


def level(tags: dict[int, TagObs], tag_size: float, tag_height: float,
          scale: float | None = None):
    """Scale, up and floor from the tags alone. Returns (4×4 levelled transform, scale, spread):
    the result is metric, z up with the floor at 0, but still turned and shifted in x, y."""
    if not tags:
        raise ValueError("no tags placed in this scene: it can't be scaled or levelled")
    edges = np.array([t.edge for t in tags.values()])
    scale = scale or tag_size / float(np.median(edges))
    spread = float((edges.max() - edges.min()) * scale * 1000)
    up = np.mean([t.up for t in tags.values()], axis=0)
    up /= np.linalg.norm(up)
    # Every tag's centre is at the same height, so the plane through them is level. Metres
    # across, it gives "up" far more exactly than any one tag's 10 cm does.
    centres = np.array([t.centre for t in tags.values()])
    if len(centres) >= 3:
        _, sv, vt = np.linalg.svd(centres - centres.mean(axis=0))
        if sv[1] > 0.1 * sv[0]:  # not all in a line
            up = vt[2] * np.sign(np.dot(vt[2], up))
    elif len(centres) == 2:
        d = (centres[1] - centres[0]) / np.linalg.norm(centres[1] - centres[0])
        up = up - np.dot(up, d) * d
        up /= np.linalg.norm(up)
    r = _rotation_to_z(up)
    heights = [float((r @ t.centre)[2]) * scale for t in tags.values()]
    floor = float(np.mean(heights)) - tag_height
    m = np.eye(4)
    m[:3, :3] = r * scale
    m[2, 3] = -floor
    return m, scale, spread


def _yaw(v: np.ndarray) -> float:
    return math.atan2(v[1], v[0])


def anchor(room: str, tags: dict[int, TagObs], known: dict[int, KnownTag], tag_size: float,
           tag_height: float) -> Alignment | None:
    """Place one scene in the room frame, if it sees enough known tags."""
    m, scale, spread = level(tags, tag_size, tag_height)
    ids = [i for i in tags if i in known]
    if len(ids) >= 2:
        # Two known tags metres apart pin the scale far better than one tag's 10 cm edge.
        lv = lambda p: (m[:3, :3] @ p) + m[:3, 3]
        pairs = [(i, j) for k, i in enumerate(ids) for j in ids[k + 1:]]
        got = sum(np.linalg.norm(lv(tags[i].centre)[:2] - lv(tags[j].centre)[:2])
                  for i, j in pairs)
        want = sum(math.dist((known[i].x, known[i].y), (known[j].x, known[j].y))
                   for i, j in pairs)
        if got > 0.3 and 0.8 < want / got < 1.25:
            m, scale, spread = level(tags, tag_size, tag_height, scale * want / got)
    lv = lambda p: (m[:3, :3] @ p) + m[:3, 3]
    pts = {i: lv(tags[i].centre)[:2] for i in ids}
    facing = {i: _yaw(m[:3, :3] @ tags[i].normal) for i in ids}
    with_xy = [i for i in ids]
    if len(with_xy) >= 2:
        a = np.array([pts[i] for i in with_xy])
        b = np.array([[known[i].x, known[i].y] for i in with_xy])
        ac, bc = a - a.mean(0), b - b.mean(0)
        h = ac.T @ bc
        phi = math.atan2(h[0, 1] - h[1, 0], h[0, 0] + h[1, 1])
    else:
        with_facing = [i for i in ids if known[i].facing is not None]
        if not with_facing:
            return None
        i = with_facing[0]
        phi = known[i].facing - facing[i]
        a = np.array([pts[i]])
        b = np.array([[known[i].x, known[i].y]])
    rot = np.array([[math.cos(phi), -math.sin(phi)], [math.sin(phi), math.cos(phi)]])
    shift = b.mean(0) - rot @ a.mean(0)
    turn = np.eye(4)
    turn[:2, :2] = rot
    turn[:2, 3] = shift
    return Alignment(room, turn @ m, scale, sorted(ids), spread)


def chain(scenes: dict[str, dict[int, TagObs]], measured: dict[int, KnownTag],
          tag_size: float = 0.1, tag_height: float = 0.2) -> tuple[dict[str, Alignment],
                                                                    list[str]]:
    """Place every room it can: first those with measured tags, then rooms that share a tag
    with a room already placed. Returns (placed rooms, rooms it couldn't place)."""
    known = dict(measured)
    placed: dict[str, Alignment] = {}
    progress = True
    while progress:
        progress = False
        for room, tags in scenes.items():
            if room in placed or not tags:
                continue
            aligned = anchor(room, tags, known, tag_size, tag_height)
            if aligned is None:
                continue
            placed[room] = aligned
            progress = True
            r = aligned.transform[:3, :3]
            for i, t in tags.items():
                if i not in known:
                    c = aligned.to_room(t.centre[None])[0]
                    known[i] = KnownTag(float(c[0]), float(c[1]), _yaw(r @ t.normal), False)
    return placed, [room for room in scenes if room not in placed]
