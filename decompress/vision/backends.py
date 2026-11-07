"""Pluggable pose/detection backends. All return list[Detection] in the COCO-17 skeleton.

  mediapipe  — MediaPipe PoseLandmarker (2D + metric 3D world landmarks, up to N people). Best for Blender export.
  yolo       — Ultralytics YOLO-pose (fast multi-person 2D; `pip install ultralytics`).
  yolo-det   — Ultralytics YOLO detection: people *and animals* (boxes only) — creatures until animal pose lands.
  hog        — OpenCV HOG person detector (boxes only). Zero extra downloads; fallback.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .schema import Detection, box_from_keypoints

CACHE = Path(os.environ.get("DECOMPRESS_CACHE", Path.home() / ".cache" / "decompress"))
MP_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
                "pose_landmarker_{v}/float16/latest/pose_landmarker_{v}.task")
# MediaPipe's 33 landmarks -> COCO-17
MP_TO_COCO = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
ANIMALS = {"bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"}


class Backend:
    name = "base"

    def __call__(self, frame_bgr: np.ndarray, t_ms: int) -> list[Detection]:
        raise NotImplementedError


class HogBackend(Backend):
    name = "hog"

    def __init__(self):
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def __call__(self, frame, t_ms):
        h, w = frame.shape[:2]
        scale = 640 / max(h, w) if max(h, w) > 640 else 1.0
        small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
        rects, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        dets = []
        for (x, y, bw, bh), wgt in zip(rects, np.ravel(weights) if len(rects) else []):
            if wgt < 0.5:
                continue
            dets.append(Detection(box=(x / scale, y / scale, (x + bw) / scale, (y + bh) / scale), score=float(wgt)))
        return dets


class MediaPipeBackend(Backend):
    name = "mediapipe"

    def __init__(self, variant: str = "full", num_poses: int = 4, model_path: str | None = None):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        self.mp = mp
        path = model_path or os.environ.get("DECOMPRESS_POSE_MODEL") or self._download(variant)
        opts = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(path)),
            running_mode=vision.RunningMode.VIDEO, num_poses=num_poses,
            min_pose_detection_confidence=0.5, min_tracking_confidence=0.5)
        self.lm = vision.PoseLandmarker.create_from_options(opts)
        self._last_ts = -1

    @staticmethod
    def _download(variant: str) -> Path:
        dst = CACHE / f"pose_landmarker_{variant}.task"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            print(f"downloading MediaPipe pose model ({variant}) -> {dst}")
            urllib.request.urlretrieve(MP_MODEL_URL.format(v=variant), dst)
        return dst

    def close(self) -> None:
        if self.lm is not None:
            self.lm.close()
            self.lm = None

    def __call__(self, frame, t_ms):
        t_ms = max(int(t_ms), self._last_ts + 1)   # VIDEO mode requires strictly increasing timestamps
        self._last_ts = t_ms
        h, w = frame.shape[:2]
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        res = self.lm.detect_for_video(img, t_ms)
        dets = []
        for i, lms in enumerate(res.pose_landmarks):
            kp = np.array([[lms[j].x * w, lms[j].y * h, lms[j].visibility] for j in MP_TO_COCO], dtype=np.float32)
            world = None
            if i < len(res.pose_world_landmarks):
                wl = res.pose_world_landmarks[i]
                world = np.array([[wl[j].x, wl[j].y, wl[j].z] for j in MP_TO_COCO], dtype=np.float32)
            box = box_from_keypoints(kp)
            if box:
                dets.append(Detection(box=box, score=float(kp[:, 2].mean()), keypoints=kp, world=world))
        return dets


class YoloPoseBackend(Backend):
    name = "yolo"

    def __init__(self, weights: str = "yolo11n-pose.pt"):
        from ultralytics import YOLO
        self.model = YOLO(weights)

    def __call__(self, frame, t_ms):
        r = self.model(frame, verbose=False)[0]
        dets = []
        if r.keypoints is None:
            return dets
        kps = r.keypoints.data.cpu().numpy()
        for box, conf, kp in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(), kps):
            dets.append(Detection(box=tuple(map(float, box)), score=float(conf), keypoints=kp.astype(np.float32)))
        return dets


class YoloDetectBackend(Backend):
    """People and animals as boxes — lets the tracker follow creatures."""
    name = "yolo-det"

    def __init__(self, weights: str = "yolo11n.pt"):
        from ultralytics import YOLO
        self.model = YOLO(weights)

    def __call__(self, frame, t_ms):
        r = self.model(frame, verbose=False)[0]
        names = r.names
        dets = []
        for box, conf, cls in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy(), r.boxes.cls.cpu().numpy()):
            label = names[int(cls)]
            if label == "person" or label in ANIMALS:
                dets.append(Detection(box=tuple(map(float, box)), score=float(conf), label=label))
        return dets


_BACKENDS = {"mediapipe": MediaPipeBackend, "yolo": YoloPoseBackend, "yolo-det": YoloDetectBackend, "hog": HogBackend}


def available_backends() -> list[str]:
    out = []
    for name, mod in (("mediapipe", "mediapipe"), ("yolo", "ultralytics"), ("yolo-det", "ultralytics")):
        try:
            __import__(mod)
            out.append(name)
        except Exception:
            pass
    return out + ["hog"]


def get_backend(name: str | None = None, **kw) -> Backend:
    """Return the requested backend, or the best available one."""
    if name:
        return _BACKENDS[name](**kw)
    for cand in available_backends():
        try:
            return _BACKENDS[cand](**kw)
        except Exception as e:  # missing model, no network, etc.
            print(f"backend {cand} unavailable: {e}")
    return HogBackend()
