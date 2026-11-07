"""Movement understanding: rule-based descriptions + a small trainable classifier.

`describe_movement(track)` gives an immediate label (standing / walking / running / crouching / arms raised ...)
from the recent keypoint history. `MovementClassifier` learns your own labels ("waving", "jumping jacks",
"dog sitting" ...) from labelled clips via k-nearest-neighbours over pose-sequence features.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .schema import J

WINDOW = 15   # frames per feature window


def _angle(a, b, c) -> float:
    """Angle at b (degrees) formed by a-b-c."""
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / n, -1, 1)))) if n > 0 else 180.0


def normalize(kp: np.ndarray) -> np.ndarray:
    """Centre on the hips and scale by torso length so features are position/size invariant. -> (17, 2)."""
    xy = kp[:, :2].astype(np.float64)
    hip = (xy[J["left_hip"]] + xy[J["right_hip"]]) / 2
    sh = (xy[J["left_shoulder"]] + xy[J["right_shoulder"]]) / 2
    scale = np.linalg.norm(sh - hip) or 1.0
    return (xy - hip) / scale


def joint_angles(kp: np.ndarray) -> dict[str, float]:
    p = kp[:, :2]
    return {
        "left_elbow": _angle(p[J["left_shoulder"]], p[J["left_elbow"]], p[J["left_wrist"]]),
        "right_elbow": _angle(p[J["right_shoulder"]], p[J["right_elbow"]], p[J["right_wrist"]]),
        "left_knee": _angle(p[J["left_hip"]], p[J["left_knee"]], p[J["left_ankle"]]),
        "right_knee": _angle(p[J["right_hip"]], p[J["right_knee"]], p[J["right_ankle"]]),
        "left_hip": _angle(p[J["left_shoulder"]], p[J["left_hip"]], p[J["left_knee"]]),
        "right_hip": _angle(p[J["right_shoulder"]], p[J["right_hip"]], p[J["right_knee"]]),
    }


def pose_features(seq: list[np.ndarray]) -> np.ndarray:
    """Fixed-length feature vector for a sequence of (17,3) keypoints: mean pose, motion energy, angles."""
    seq = seq[-WINDOW:]
    norm = np.stack([normalize(k) for k in seq])                       # (T, 17, 2)
    mean_pose = norm.mean(0).ravel()                                     # 34
    vel = np.diff(norm, axis=0) if len(norm) > 1 else np.zeros((1, 17, 2))
    energy = np.linalg.norm(vel, axis=2).mean(0)                         # 17
    angles = np.array([list(joint_angles(k).values()) for k in seq]) / 180.0
    return np.concatenate([mean_pose, energy, angles.mean(0), angles.std(0)])  # 34+17+6+6 = 63


def describe_movement(history) -> str:
    """history: iterable of (t, Detection). Rule-based label from the last ~1s."""
    items = [(t, d) for t, d in list(history)[-WINDOW:]]
    if len(items) < 3:
        return ""
    (t0, d0), (t1, d1) = items[0], items[-1]
    h = float(np.median([d.box[3] - d.box[1] for _, d in items])) or 1.0
    dt = max(t1 - t0, 1e-3)
    speed = np.hypot(d1.anchor[0] - d0.anchor[0], d1.anchor[1] - d0.anchor[1]) / h / dt   # body-heights / s
    parts = []
    if speed > 1.5:
        parts.append("running")
    elif speed > 0.35:
        parts.append("walking")
    kp = d1.keypoints
    if kp is not None and kp[:, 2].mean() > 0.3:
        ang = joint_angles(kp)
        if not parts:
            knees = (ang["left_knee"] + ang["right_knee"]) / 2
            parts.append("crouching" if knees < 110 else "standing")
        nose_y = kp[J["nose"], 1]
        raised = [s for s in ("left", "right") if kp[J[f"{s}_wrist"], 1] < nose_y and kp[J[f"{s}_wrist"], 2] > 0.3]
        if len(raised) == 2:
            parts.append("both arms raised")
        elif raised:
            parts.append(f"{raised[0]} arm raised")
    elif not parts:
        parts.append("still")
    return ", ".join(parts)


class MovementClassifier:
    """k-NN over pose_features. Train on labelled windows; persist as JSON."""

    def __init__(self, k: int = 5):
        self.k = k
        self.X: np.ndarray = np.zeros((0, 63))
        self.y: list[str] = []
        self.mu = np.zeros(63)
        self.sd = np.ones(63)

    def fit(self, sequences: list[list[np.ndarray]], labels: list[str]) -> "MovementClassifier":
        X = []
        ys = []
        for seq, lab in zip(sequences, labels):   # slide windows across each labelled clip
            for end in range(min(WINDOW, len(seq)), len(seq) + 1, max(1, WINDOW // 3)):
                X.append(pose_features(seq[:end]))
                ys.append(lab)
        self.X = np.array(X)
        self.y = ys
        self.mu = self.X.mean(0)
        self.sd = self.X.std(0) + 1e-6
        return self

    def predict(self, seq: list[np.ndarray]) -> tuple[str, float]:
        if not self.y:
            return "", 0.0
        f = (pose_features(seq) - self.mu) / self.sd
        d = np.linalg.norm((self.X - self.mu) / self.sd - f, axis=1)
        idx = np.argsort(d)[: self.k]
        votes: dict[str, int] = {}
        for i in idx:
            votes[self.y[i]] = votes.get(self.y[i], 0) + 1
        label = max(votes, key=votes.get)
        return label, votes[label] / len(idx)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({"k": self.k, "X": self.X.tolist(), "y": self.y,
                                          "mu": self.mu.tolist(), "sd": self.sd.tolist()}))

    @classmethod
    def load(cls, path: str | Path) -> "MovementClassifier":
        d = json.loads(Path(path).read_text())
        c = cls(k=d["k"])
        c.X, c.y, c.mu, c.sd = np.array(d["X"]), d["y"], np.array(d["mu"]), np.array(d["sd"])
        return c
