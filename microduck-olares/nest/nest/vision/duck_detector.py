"""Pollen's duck detector, run on the Jetson against Reachy Mini's camera.

The same model the ducks run on their NPUs, in its ONNX form: `duck_detect.onnx` from the Hub
repo `pollen-robotics/microduck-duck-detector` (trained in `pollen-robotics/duck_detector`).
Download it once during setup; after that it is a local file and nothing leaves the house.

The pre- and post-processing must agree with training, and nothing enforces that except the
`duck-detect` crate's own notes, which this follows:

* letterbox (not stretch) into 320×320, padded with grey 114;
* RGB, not BGR (OpenCV and the reachy_mini SDK hand you BGR);
* one class; the head emits 2100 candidates of (cx, cy, w, h, score).

Scores from this float model are probabilities (unlike the INT8 `.rknn` on the ducks, where
every real detection reads about 1.3). Random noise can reach ~0.4, so the default threshold is
0.6. Reachy's camera sees a different picture from the one the model was trained on (a wide lens
at TV-stand height rather than a duck's eye view): tune `detector_threshold` on your own room,
and let the tracker smooth over misses.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SIZE = 320
PAD = 114


@dataclass
class Letterbox:
    scale: float
    pad_x: float
    pad_y: float

    def to_frame(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.pad_x) / self.scale, (y - self.pad_y) / self.scale


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    score: float

    @property
    def foot(self) -> tuple[float, float]:
        """Bottom-centre: where the duck touches the floor, the point to map to the room."""
        return (self.x0 + self.x1) / 2.0, self.y1


def letterbox(rgb: np.ndarray, size: int = SIZE) -> tuple[np.ndarray, Letterbox]:
    h, w = rgb.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    # Nearest-neighbour resize in numpy keeps OpenCV optional; cv2 is used when present.
    try:
        import cv2

        resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    except ImportError:
        ys = (np.arange(nh) / scale).astype(int).clip(0, h - 1)
        xs = (np.arange(nw) / scale).astype(int).clip(0, w - 1)
        resized = rgb[ys][:, xs]
    canvas = np.full((size, size, 3), PAD, dtype=np.uint8)
    pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
    canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
    return canvas, Letterbox(scale, pad_x, pad_y)


def decode(output: np.ndarray, box: Letterbox, threshold: float = 0.4,
           iou: float = 0.5, size: int = SIZE) -> list[Box]:
    """Candidates → boxes in the original frame's pixels, after NMS.

    Accepts the head as (1, 5, N), (5, N), (1, N, 5) or (N, 5). Coordinates may be in input
    pixels or normalised to 0..1; both are handled."""
    a = np.squeeze(np.asarray(output, dtype=np.float32))
    if a.ndim != 2:
        raise ValueError(f"unexpected detector output shape {output.shape}")
    if a.shape[0] == 5 and a.shape[1] != 5:
        a = a.T
    keep = a[:, 4] >= threshold
    a = a[keep]
    if a.size == 0:
        return []
    if float(np.max(a[:, :4])) <= 1.5:
        a[:, :4] *= size
    cx, cy, w, h, score = a.T
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    order = np.argsort(-score)
    kept: list[int] = []
    while order.size:
        i = int(order[0])
        kept.append(i)
        rest = order[1:]
        ix0, iy0 = np.maximum(x0[i], x0[rest]), np.maximum(y0[i], y0[rest])
        ix1, iy1 = np.minimum(x1[i], x1[rest]), np.minimum(y1[i], y1[rest])
        inter = np.clip(ix1 - ix0, 0, None) * np.clip(iy1 - iy0, 0, None)
        union = (x1[i] - x0[i]) * (y1[i] - y0[i]) + (x1[rest] - x0[rest]) * (y1[rest] - y0[rest])
        order = rest[inter / np.maximum(union - inter, 1e-6) < iou]
    boxes = []
    for i in kept:
        fx0, fy0 = box.to_frame(float(x0[i]), float(y0[i]))
        fx1, fy1 = box.to_frame(float(x1[i]), float(y1[i]))
        boxes.append(Box(fx0, fy0, fx1, fy1, float(score[i])))
    return boxes


class DuckDetector:
    def __init__(self, model_path: str, threshold: float = 0.6):
        import onnxruntime as ort  # optional dependency: `pip install onnxruntime-gpu` on Jetson

        providers = [p for p in ("TensorrtExecutionProvider", "CUDAExecutionProvider",
                                 "CPUExecutionProvider") if p in ort.get_available_providers()]
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.threshold = threshold

    def detect(self, frame_bgr: np.ndarray) -> list[Box]:
        rgb = frame_bgr[:, :, ::-1]
        canvas, box = letterbox(np.ascontiguousarray(rgb))
        tensor = canvas.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        (output, *_) = self.session.run(None, {self.input_name: tensor})
        return decode(output, box, self.threshold)
