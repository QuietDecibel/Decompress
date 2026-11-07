"""Live video sources (IP cameras, webcams, files) with background capture + analysis threads."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .pipeline import Analyzer


def open_capture(url: str) -> cv2.VideoCapture:
    if url.isdigit():                      # local webcam index
        return cv2.VideoCapture(int(url))
    if url.startswith("rtsp://"):
        return cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    return cv2.VideoCapture(url)


def redact(url: str) -> str:
    """Hide credentials when showing a URL."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


@dataclass
class Source:
    url: str
    name: str = ""
    kind: str = "camera"                   # camera | file | webcam
    analyze: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "starting"
    fps: float = 0.0
    size: tuple[int, int] = (0, 0)
    last_jpeg: bytes = b""
    tracks_summary: list = field(default_factory=list)
    recording: bool = False

    def __post_init__(self):
        if not self.name:
            self.name = Path(self.url).name if self.kind == "file" else redact(self.url)
        self._frame: np.ndarray | None = None
        self._frame_t = 0.0
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._analyzer: Analyzer | None = None
        self._analyzer_factory = None
        self.last_motion_file: str = ""

    # ---- lifecycle -------------------------------------------------------
    def start(self, analyzer_factory=None) -> "Source":
        self._analyzer_factory = analyzer_factory or Analyzer
        threading.Thread(target=self._capture_loop, daemon=True, name=f"cap-{self.id}").start()
        threading.Thread(target=self._analyze_loop, daemon=True, name=f"ana-{self.id}").start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def _capture_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            cap = open_capture(self.url)
            if not cap.isOpened():
                self.status = f"unreachable, retrying in {backoff:.0f}s"
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            backoff = 1.0
            self.fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            self.size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            self.status = "live"
            t0 = time.time()
            n = 0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    if self.kind == "file":           # loop imported files in the grid
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        t0, n = time.time(), 0
                        continue
                    self.status = "stream lost, reconnecting"
                    break
                n += 1
                if self.kind == "file":               # pace file playback at native fps
                    delay = t0 + n / self.fps - time.time()
                    if delay > 0:
                        time.sleep(delay)
                with self._cond:
                    self._frame, self._frame_t = frame, time.time()
                    self._cond.notify_all()
            cap.release()

    def _analyze_loop(self) -> None:
        last_t = 0.0
        while not self._stop.is_set():
            with self._cond:
                self._cond.wait_for(lambda: self._stop.is_set() or self._frame_t != last_t, timeout=1.0)
                frame, last_t = self._frame, self._frame_t
            if frame is None or self._stop.is_set():
                continue
            if self.analyze:
                if self._analyzer is None:
                    self._analyzer = self._analyzer_factory()
                out, tracks = self._analyzer.process(frame, last_t)
                self.tracks_summary = [{"id": t.id, "label": t.det.label, "movement": t.label} for t in tracks]
            else:
                out = frame
                self.tracks_summary = []
            if self.recording and self._analyzer:
                cv2.circle(out, (18, 18), 8, (0, 0, 255), -1)
            ok, jpg = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                self.last_jpeg = jpg.tobytes()
        if self._analyzer:
            self._analyzer.close()

    # ---- recording -------------------------------------------------------
    def start_recording(self) -> None:
        self.analyze = True
        if self._analyzer is None:
            self._analyzer = self._analyzer_factory()
        self._analyzer.start_recording(self.url if self.kind == "file" else redact(self.url), self.fps or 25.0, self.size)
        self.recording = True

    def stop_recording(self, out_dir: Path) -> str:
        self.recording = False
        rec = self._analyzer.stop_recording() if self._analyzer else None
        if not rec:
            return ""
        path = rec.save(out_dir / f"{self.id}-{int(time.time())}.motion.json")
        self.last_motion_file = str(path)
        return self.last_motion_file

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "status": self.status, "fps": round(self.fps, 1),
                "size": list(self.size), "analyze": self.analyze, "recording": self.recording,
                "tracks": self.tracks_summary, "url": redact(self.url), "last_motion_file": self.last_motion_file}
