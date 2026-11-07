import numpy as np

from decompress.vision.schema import J, Detection, box_from_keypoints


def skeleton(cx=320.0, cy=240.0, scale=100.0, arms_up=False, knee_bend=0.0) -> np.ndarray:
    """Synthetic upright COCO-17 skeleton in pixels (y down)."""
    p = {
        "nose": (0, -1.6), "left_eye": (-0.05, -1.65), "right_eye": (0.05, -1.65),
        "left_ear": (-0.1, -1.6), "right_ear": (0.1, -1.6),
        "left_shoulder": (-0.25, -1.3), "right_shoulder": (0.25, -1.3),
        "left_elbow": (-0.3, -0.95), "right_elbow": (0.3, -0.95),
        "left_wrist": (-0.3, -0.6), "right_wrist": (0.3, -0.6),
        "left_hip": (-0.15, -0.6), "right_hip": (0.15, -0.6),
        "left_knee": (-0.15 - knee_bend, -0.3), "right_knee": (0.15 + knee_bend, -0.3),
        "left_ankle": (-0.15, 0.0), "right_ankle": (0.15, 0.0),
    }
    if arms_up:
        p["left_elbow"], p["right_elbow"] = (-0.3, -1.6), (0.3, -1.6)
        p["left_wrist"], p["right_wrist"] = (-0.3, -1.9), (0.3, -1.9)
    kp = np.zeros((17, 3), dtype=np.float32)
    for name, (x, y) in p.items():
        kp[J[name]] = (cx + x * scale, cy + y * scale, 0.9)
    return kp


def det(kp) -> Detection:
    return Detection(box=box_from_keypoints(kp), keypoints=kp)
