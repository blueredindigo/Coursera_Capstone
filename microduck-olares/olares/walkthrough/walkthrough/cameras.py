"""Cameras from nerfstudio's `transforms.json`, and the points COLMAP found.

nerfstudio (`ns-process-data`) writes one `transforms.json` per scene: each frame's
camera-to-world matrix in the OpenGL convention (x right, y up, z backwards), intrinsics, and
the `applied_transform` it used to turn COLMAP's world into its own. Everything here works in
that frame, called W below.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

GL_TO_CV = np.diag([1.0, -1.0, -1.0])


@dataclass
class Camera:
    name: str                 # image file path, relative to the scene
    c2w: np.ndarray           # 4×4, OpenGL convention, world W
    k: np.ndarray             # 3×3 intrinsics
    dist: np.ndarray          # OpenCV distortion (k1, k2, p1, p2), zeros if none

    def projection(self) -> np.ndarray:
        """3×4 matrix taking a W point to (undistorted) pixels."""
        w2c = np.linalg.inv(self.c2w)[:3]
        return self.k @ GL_TO_CV @ w2c

    def project(self, points: np.ndarray) -> np.ndarray:
        p = self.projection() @ np.c_[points, np.ones(len(points))].T
        return (p[:2] / p[2]).T


def load_cameras(transforms_json: str | Path) -> tuple[list[Camera], np.ndarray]:
    """The cameras, and the 4×4 that takes COLMAP's world to W."""
    data = json.loads(Path(transforms_json).read_text())
    cams = []
    for f in data["frames"]:
        get = lambda key, default=None: f.get(key, data.get(key, default))
        k = np.array([[get("fl_x"), 0, get("cx")], [0, get("fl_y"), get("cy")], [0, 0, 1]], float)
        dist = np.array([get("k1", 0.0), get("k2", 0.0), get("p1", 0.0), get("p2", 0.0)], float)
        cams.append(Camera(f["file_path"], np.array(f["transform_matrix"], float), k, dist))
    applied = np.eye(4)
    if "applied_transform" in data:
        applied[:3] = np.array(data["applied_transform"], float)
    return cams, applied


def read_points3d_bin(path: str | Path) -> np.ndarray:
    """COLMAP's sparse points (`sparse/0/points3D.bin`): N×3, in COLMAP's world."""
    out = []
    with open(path, "rb") as handle:
        (count,) = struct.unpack("<Q", handle.read(8))
        for _ in range(count):
            _pid, x, y, z, _r, _g, _b, _err = struct.unpack("<QdddBBBd", handle.read(43))
            (track,) = struct.unpack("<Q", handle.read(8))
            handle.seek(8 * track, 1)  # (image id, point2D index) pairs: not needed
            out.append((x, y, z))
    return np.array(out, float).reshape(-1, 3)


def read_ply(path: str | Path, fields: list[str]) -> np.ndarray:
    """Chosen vertex properties from a binary little-endian PLY (a splat export, say)."""
    sizes = {"float": "f4", "float32": "f4", "double": "f8", "uchar": "u1", "uint8": "u1",
             "char": "i1", "int": "i4", "int32": "i4", "uint": "u4", "short": "i2",
             "ushort": "u2"}
    with open(path, "rb") as handle:
        header, props, count = [], [], 0
        while True:
            line = handle.readline().decode("ascii").strip()
            header.append(line)
            if line.startswith("format") and "binary_little_endian" not in line:
                raise ValueError("only binary little-endian PLY files are supported")
            if line.startswith("element vertex"):
                count = int(line.split()[-1])
            elif line.startswith("property") and "list" not in line:
                _, kind, name = line.split()
                props.append((name, "<" + sizes[kind]))
            if line == "end_header":
                break
        data = np.frombuffer(handle.read(count * np.dtype(props).itemsize), np.dtype(props),
                             count=count)
    return np.stack([data[name].astype(float) for name in fields], axis=1)


def apply(transform: np.ndarray, points: np.ndarray) -> np.ndarray:
    return (transform[:3, :3] @ points.T).T + transform[:3, 3]
