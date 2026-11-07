"""Analyzer: detection/pose -> tracking -> movement labels -> annotated frame (+ optional recording)."""
from __future__ import annotations

import colorsys

import cv2
import numpy as np

from .motion import MotionRecorder
from .vision import Tracker, describe_movement, get_backend
from .vision.movement import MovementClassifier
from .vision.schema import BONES, J


def _color(tid: int) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb((tid * 0.618) % 1, 0.75, 1.0)
    return int(b * 255), int(g * 255), int(r * 255)


class Analyzer:
    def __init__(self, backend=None, classifier: MovementClassifier | None = None):
        self.backend = backend or get_backend()
        self.tracker = Tracker()
        self.classifier = classifier
        self.recorder: MotionRecorder | None = None
        self.frame_idx = 0

    def start_recording(self, source: str, fps: float, size: tuple[int, int]) -> None:
        self.recorder = MotionRecorder(source, fps, size)

    def stop_recording(self) -> MotionRecorder | None:
        rec, self.recorder = self.recorder, None
        return rec

    def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if close:
            close()

    def process(self, frame: np.ndarray, t: float, annotate: bool = True):
        dets = self.backend(frame, int(t * 1000))
        h, w = frame.shape[:2]
        tracks = self.tracker.update(dets, t, frame_diag=float(np.hypot(w, h)))
        for tr in tracks:
            label = describe_movement(tr.history)
            if self.classifier and tr.det.keypoints is not None:
                seq = [d.keypoints for _, d in tr.history if d.keypoints is not None]
                if len(seq) >= 5:
                    learned, conf = self.classifier.predict(seq)
                    if conf >= 0.6:
                        label = f"{learned} ({conf:.0%})" + (f" · {label}" if label else "")
            tr.label = label
        if self.recorder:
            self.recorder.add(self.frame_idx, t, tracks)
        self.frame_idx += 1
        out = draw(frame.copy(), tracks) if annotate else frame
        return out, tracks


def draw(img: np.ndarray, tracks) -> np.ndarray:
    fs = max(0.5, img.shape[1] / 1280)          # scale text/lines with resolution
    th = max(1, round(fs * 2))
    for tr in tracks:
        c = _color(tr.id)
        x1, y1, x2, y2 = map(int, tr.det.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), c, th)
        # trail of the hip centre / box centre
        pts = [np.array(d.anchor, dtype=int) for _, d in list(tr.history)[-30:]]
        if len(pts) > 1:
            cv2.polylines(img, [np.array(pts)], False, c, 2)
        kp = tr.det.keypoints
        if kp is not None:
            for a, b in BONES:
                pa, pb = kp[J[a]], kp[J[b]]
                if pa[2] > 0.3 and pb[2] > 0.3:
                    cv2.line(img, tuple(map(int, pa[:2])), tuple(map(int, pb[:2])), c, 2)
            for x, y, conf in kp:
                if conf > 0.3:
                    cv2.circle(img, (int(x), int(y)), 3, (255, 255, 255), -1)
        text = f"#{tr.id} {tr.det.label}" + (f": {tr.label}" if tr.label else "")
        org = (x1, max(int(20 * fs), y1 - 6))
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6 * fs, (0, 0, 0), th + 2)
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6 * fs, c, th)
    return img
