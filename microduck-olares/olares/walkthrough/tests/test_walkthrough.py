"""The walkthrough's geometry, on synthetic rooms with real tag images."""

import json
import math
import struct

import cv2
import numpy as np

from walkthrough import cameras as cams
from walkthrough import floorplan as fp
from walkthrough import tags as tg

W_IMG, H_IMG, F = 1920, 1440, 1500.0   # a phone's video frame, roughly
K = np.array([[F, 0, W_IMG / 2], [0, F, H_IMG / 2], [0, 0, 1]])


def tag_texture(tag_id, px=240):
    """The printed tag: 8×8 cells (with its black border) plus a white margin of one cell."""
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    inner = cv2.aruco.generateImageMarker(d, tag_id, px * 8 // 10, borderBits=1)
    out = np.full((px, px), 255, np.uint8)
    m = px // 10
    out[m:m + inner.shape[0], m:m + inner.shape[1]] = inner
    return out, m, m + inner.shape[0]


def wall_tag(centre, facing, size=0.1):
    """Room-frame corners (TL, TR, BR, BL as printed) of an upright tag on a wall."""
    n = np.array([math.cos(facing), math.sin(facing), 0.0])
    up = np.array([0, 0, 1.0])
    right = np.cross(up, n)  # looking at the tag's face: right is to the viewer's right
    c, h = np.array(centre, float), size / 2
    return np.array([c - right * h + up * h, c + right * h + up * h,
                     c + right * h - up * h, c - right * h - up * h])


def look_at(eye, target):
    """OpenGL camera-to-world: looking along -z, y up."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    back = eye - target
    back /= np.linalg.norm(back)
    right = np.cross([0, 0, 1.0], back)
    right /= np.linalg.norm(right)
    up = np.cross(back, right)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = right, up, back, eye
    return m


def similarity(scale, yaw, tilt, shift):
    """A random-ish W frame: what a reconstruction's arbitrary frame looks like."""
    cz, sz, cx, sx = math.cos(yaw), math.sin(yaw), math.cos(tilt), math.sin(tilt)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    m = np.eye(4)
    m[:3, :3] = scale * (rx @ rz)
    m[:3, 3] = shift
    return m


def render(camera, tags_room, w_from_room):
    """A grey image with every tag that's in view, drawn where the camera sees it."""
    img = np.full((H_IMG, W_IMG), 150, np.uint8)
    for tag_id, corners in tags_room.items():
        pix = camera.project(cams.apply(w_from_room, corners))
        w2c = np.linalg.inv(camera.c2w)
        z = (cams.GL_TO_CV @ (w2c[:3, :3] @ cams.apply(w_from_room, corners).T
                              + w2c[:3, 3:])).T[:, 2]
        if (z <= 0.05).any() or (pix < 0).any() or (pix[:, 0] > W_IMG).any() or \
                (pix[:, 1] > H_IMG).any():
            continue
        tex, a, b = tag_texture(tag_id)
        src = np.array([[a, a], [b, a], [b, b], [a, b]], np.float32)
        hmat = cv2.getPerspectiveTransform(src, pix.astype(np.float32))
        warped = cv2.warpPerspective(tex, hmat, (W_IMG, H_IMG), borderValue=0)
        mask = cv2.warpPerspective(np.full_like(tex, 255), hmat, (W_IMG, H_IMG))
        img[mask > 0] = warped[mask > 0]
    return img


def scene(tags_room, eyes, targets, w_from_room):
    cameras, detections = [], {}
    for k, (eye, target) in enumerate(zip(eyes, targets)):
        c2w = w_from_room.copy()
        c2w = c2w @ look_at(eye, target)
        # c2w must be a rigid transform in W: remove the scale from the rotation part
        s = np.cbrt(np.linalg.det(w_from_room[:3, :3]))
        c2w[:3, :3] /= s
        cam = cams.Camera(f"images/f{k:03d}.png", c2w, K, np.zeros(4))
        cameras.append(cam)
        found = tg.detect(render(cam, tags_room, w_from_room))
        if found:
            detections[cam.name] = found
    return cameras, detections


def test_tags_fix_scale_floor_and_place_two_rooms_in_the_room_frame():
    # The lounge: tags 0 and 1 measured; tag 3 is by the hallway door, 20 cm up like all tags.
    lounge_tags = {0: wall_tag((0.0, 2.0, 0.2), 0.0), 1: wall_tag((4.0, 3.0, 0.2), math.pi),
                   3: wall_tag((2.0, 5.0, 0.2), -math.pi / 2)}
    # The hallway's own video sees tag 3 (from the other side of the same spot, as filmed
    # through the doorway) and its own tag 4.
    hall_tags = {3: lounge_tags[3], 4: wall_tag((5.0, 6.0, 0.2), math.pi)}
    w_lounge = similarity(0.37, 0.7, 0.4, (1.0, -2.0, 0.5))
    w_hall = similarity(2.1, -1.2, -0.3, (-3.0, 0.2, 4.0))

    eyes, targets = [], []
    for i in range(24):  # walking round the lounge, phone at 1.2 m, looking at each wall
        a = 2 * math.pi * i / 24
        eyes.append((2.0 + 1.0 * math.cos(a), 2.5 + 1.0 * math.sin(a), 1.2))
        targets.append(list(lounge_tags.values())[i % 3].mean(axis=0))
    cams_l, det_l = scene(lounge_tags, eyes, targets, w_lounge)
    eyes_h = [(3.2 + 0.2 * k, 4.2 + 0.1 * k, 1.1) for k in range(8)] + \
             [(3.8 + 0.1 * k, 5.6, 1.1) for k in range(8)]
    targets_h = [hall_tags[3].mean(0)] * 8 + [hall_tags[4].mean(0)] * 8
    cams_h, det_h = scene(hall_tags, eyes_h, targets_h, w_hall)

    placed_l = tg.place_tags(cams_l, det_l)
    placed_h = tg.place_tags(cams_h, det_h)
    assert sorted(placed_l) == [0, 1, 3] and sorted(placed_h) == [3, 4]

    measured = {0: tg.KnownTag(0.0, 2.0), 1: tg.KnownTag(4.0, 3.0)}
    rooms, missing = tg.chain({"lounge": placed_l, "hallway": placed_h}, measured)
    assert not missing and set(rooms) == {"lounge", "hallway"}
    for room, truth in (("lounge", lounge_tags), ("hallway", hall_tags)):
        a = rooms[room]
        w_from_room = w_lounge if room == "lounge" else w_hall
        for tag_id, corners in truth.items():
            got = a.to_room(cams.apply(w_from_room, corners))
            # The lounge is pinned by two measured tags metres apart: within 5 mm. The
            # hallway only through one shared tag, so its scale and turn come from that one
            # 10 cm tag: within 4 cm at 3 m from it, still inside the map's 5 cm cells.
            assert np.abs(got - corners).max() < (0.005 if room == "lounge" else 0.04), \
                (room, tag_id, np.abs(got - corners).max())
    assert rooms["hallway"].anchored_by == [3]        # placed through the shared tag
    # A room nobody can reach stays unplaced, and says so.
    _, missing = tg.chain({"lounge": placed_l, "attic": {9: placed_h[4]}}, measured)
    assert missing == ["attic"]


def test_one_measured_tag_with_its_facing_is_enough():
    t = {0: wall_tag((1.0, 1.0, 0.2), math.pi / 2), 2: wall_tag((3.0, 1.5, 0.2), math.pi)}
    w = similarity(0.5, 2.0, 0.2, (0.0, 0.0, 0.0))
    obs = {i: tg.TagObs(i, cams.apply(w, c), 5) for i, c in t.items()}
    a = tg.anchor("room", obs, {0: tg.KnownTag(1.0, 1.0, math.pi / 2)}, 0.1, 0.2)
    got = a.to_room(obs[2].corners)
    assert np.abs(got - t[2]).max() < 1e-6


def test_floorplan_finds_floor_walls_and_furniture(tmp_path):
    rng = np.random.default_rng(1)
    # a 4 × 3 m room: floor points, four walls, a box (sofa) in one corner
    floor = np.c_[rng.uniform(0, 4, 4000), rng.uniform(0, 3, 4000), rng.normal(0, 0.01, 4000)]
    walls = []
    for x0, y0, x1, y1 in ((0, 0, 4, 0), (4, 0, 4, 3), (4, 3, 0, 3), (0, 3, 0, 0)):
        t = rng.uniform(0, 1, 1500)
        walls.append(np.c_[x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, rng.uniform(0, 2.4, 1500)])
    sofa = np.c_[rng.uniform(3.0, 3.9, 3000), rng.uniform(2.1, 2.9, 3000), rng.uniform(0.1, 0.8, 3000)]
    points = np.concatenate([floor, *walls, sofa])
    walked = np.c_[np.linspace(0.8, 3.0, 30), np.full(30, 1.5), np.full(30, 1.3)]
    plan = fp.build({"lounge": {"points": points, "walked": walked}}, filmed_at=123.0)
    at = lambda x, y: (int((y - plan["y0"]) / plan["cell"]), int((x - plan["x0"]) / plan["cell"]))
    assert plan["floor"][at(2.0, 1.5)] and not plan["blocked"][at(2.0, 1.5)]
    assert plan["blocked"][at(3.5, 2.5)]              # the sofa
    assert plan["blocked"][at(2.0, 0.01)]             # a wall
    assert not plan["floor"][at(-0.5, 1.5)]           # outside
    fp.save(plan, tmp_path / "floorplan.npz")
    back = np.load(tmp_path / "floorplan.npz")
    assert {"x0", "y0", "cell", "floor", "blocked", "filmed_at"} <= set(back.files)
    assert float(back["filmed_at"]) == 123.0


def test_readers(tmp_path):
    # COLMAP points3D.bin with two points, one with a track
    with open(tmp_path / "points3D.bin", "wb") as f:
        f.write(struct.pack("<Q", 2))
        f.write(struct.pack("<QdddBBBd", 1, 1.0, 2.0, 3.0, 9, 9, 9, 0.5) + struct.pack("<Q", 1)
                + struct.pack("<ii", 4, 5))
        f.write(struct.pack("<QdddBBBd", 2, -1.0, 0.5, 0.25, 9, 9, 9, 0.5) + struct.pack("<Q", 0))
    pts = cams.read_points3d_bin(tmp_path / "points3D.bin")
    assert pts.tolist() == [[1.0, 2.0, 3.0], [-1.0, 0.5, 0.25]]
    # a splat-like PLY
    header = ("ply\nformat binary_little_endian 1.0\nelement vertex 2\nproperty float x\n"
              "property float y\nproperty float z\nproperty float opacity\nend_header\n")
    with open(tmp_path / "s.ply", "wb") as f:
        f.write(header.encode())
        f.write(np.array([[1, 2, 3, 0.5], [4, 5, 6, -2]], "<f4").tobytes())
    v = cams.read_ply(tmp_path / "s.ply", ["x", "z", "opacity"])
    assert v.tolist() == [[1, 3, 0.5], [4, 6, -2]]
    # transforms.json: projection round trip, and the applied transform
    c2w = look_at((0, -2, 1), (0, 0, 1))
    (tmp_path / "transforms.json").write_text(json.dumps({
        "fl_x": F, "fl_y": F, "cx": W_IMG / 2, "cy": H_IMG / 2,
        "applied_transform": [[0, 1, 0, 0], [1, 0, 0, 0], [0, 0, -1, 0]],
        "frames": [{"file_path": "images/a.png", "transform_matrix": c2w.tolist()}]}))
    loaded, applied = cams.load_cameras(tmp_path / "transforms.json")
    uv = loaded[0].project(np.array([[0.0, 0.0, 1.0]]))[0]
    assert np.allclose(uv, [W_IMG / 2, H_IMG / 2])     # straight ahead: the image centre
    assert applied.shape == (4, 4) and applied[2, 2] == -1


def test_the_floorplan_step_runs_end_to_end_from_scene_files(tmp_path, monkeypatch):
    """The pipeline's glue, without nerfstudio: scene files as ns-process-data would leave
    them, the tag cache, then `--steps floorplan` writes what the Nest loads."""
    import sys

    from walkthrough import pipeline

    root = tmp_path / "walk"
    (root / "videos").mkdir(parents=True)
    (root / "videos" / "lounge.mp4").write_bytes(b"")
    (root / "tags.toml").write_text("size_mm = 100\ncentre_height_cm = 20\n"
                                    "[[tag]]\nid = 0\nx = 0.0\ny = 2.0\n"
                                    "[[tag]]\nid = 1\nx = 4.0\ny = 3.0\n")
    w = similarity(0.5, 0.3, 0.2, (1.0, 1.0, 0.0))  # the reconstruction's own frame
    scene_dir = root / "work" / "scenes" / "lounge"
    (scene_dir / "colmap" / "sparse" / "0").mkdir(parents=True)
    frames = [{"file_path": f"images/{k}.png",
               "transform_matrix": (w @ look_at((1 + 0.2 * k, 2.5, 1.2), (4, 2.5, 1))).tolist()}
              for k in range(10)]
    for f in frames:  # rigid in W
        m = np.array(f["transform_matrix"])
        m[:3, :3] /= 0.5
        f["transform_matrix"] = m.tolist()
    (scene_dir / "transforms.json").write_text(json.dumps(
        {"fl_x": F, "fl_y": F, "cx": 960, "cy": 720, "frames": frames}))
    rng = np.random.default_rng(3)
    room_pts = np.concatenate([
        np.c_[rng.uniform(0, 4, 6000), rng.uniform(0, 5, 6000), np.zeros(6000)],
        np.c_[np.zeros(3000), rng.uniform(0, 5, 3000), rng.uniform(0, 2, 3000)]])
    with open(scene_dir / "colmap" / "sparse" / "0" / "points3D.bin", "wb") as f:
        pts = cams.apply(w, room_pts)
        f.write(struct.pack("<Q", len(pts)))
        for k, (x, y, z) in enumerate(pts):
            f.write(struct.pack("<QdddBBBd", k, x, y, z, 0, 0, 0, 0.1) + struct.pack("<Q", 0))
    tags_w = {i: cams.apply(w, wall_tag(c, face)) for i, c, face in
              ((0, (0.0, 2.0, 0.2), 0.0), (1, (4.0, 3.0, 0.2), math.pi),
               (2, (2.0, 5.0, 0.2), -math.pi / 2))}
    (root / "work" / "tags.json").write_text(json.dumps(
        {"lounge": {str(i): c.tolist() for i, c in tags_w.items()}}))

    monkeypatch.setattr(sys, "argv", ["walkthrough", str(root), "--steps", "floorplan"])
    pipeline.main()
    plan = np.load(root / "out" / "floorplan.npz")
    at = lambda x, y: (int((y - float(plan["y0"])) / float(plan["cell"])),
                       int((x - float(plan["x0"])) / float(plan["cell"])))
    assert plan["floor"][at(2.0, 2.5)] and not plan["blocked"][at(2.0, 2.5)]
    assert plan["blocked"][at(0.02, 2.5)]            # the wall, in the right place
    assert (root / "out" / "floorplan.png").exists()
    assert "placed in the room frame by tags [0, 1]" in (root / "out" / "report.md").read_text()
