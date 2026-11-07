from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Common skeleton used everywhere in Decompress (COCO-17 order).
JOINTS = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist",
    "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
]
J = {n: i for i, n in enumerate(JOINTS)}
BONES = [
    ("left_shoulder", "right_shoulder"), ("left_hip", "right_hip"),
    ("left_shoulder", "left_hip"), ("right_shoulder", "right_hip"),
    ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
    ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
    ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
    ("nose", "left_eye"), ("nose", "right_eye"), ("left_eye", "left_ear"), ("right_eye", "right_ear"),
]


@dataclass
class Detection:
    box: tuple[float, float, float, float]          # x1, y1, x2, y2 in pixels
    score: float = 1.0
    label: str = "person"
    keypoints: np.ndarray | None = None              # (17, 3): x, y, confidence (pixels)
    world: np.ndarray | None = None                  # (17, 3): metric 3D (metres, hip-centred) if backend provides it
    extra: dict = field(default_factory=dict)

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.box
        return (x1 + x2) / 2, (y1 + y2) / 2

    @property
    def anchor(self) -> tuple[float, float]:
        """Stable body point for trails/speed: mid-hips when visible, else the box's bottom-centre."""
        kp = self.keypoints
        if kp is not None and kp[11, 2] > 0.3 and kp[12, 2] > 0.3:
            return float((kp[11, 0] + kp[12, 0]) / 2), float((kp[11, 1] + kp[12, 1]) / 2)
        x1, _, x2, y2 = self.box
        return (x1 + x2) / 2, y2 - 0.45 * (y2 - self.box[1])


def iou(a, b) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def box_from_keypoints(kp: np.ndarray, min_conf: float = 0.3, pad: float = 0.1):
    pts = kp[kp[:, 2] >= min_conf, :2]
    if len(pts) == 0:
        return None
    x1, y1 = pts.min(0)
    x2, y2 = pts.max(0)
    w, h = x2 - x1, y2 - y1
    return (float(x1 - pad * w), float(y1 - pad * h), float(x2 + pad * w), float(y2 + pad * h))
