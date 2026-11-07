from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from ..vision.schema import BONES, JOINTS

FORMAT = "decompress-motion"
VERSION = 1


class MotionRecorder:
    """Accumulates per-track keypoints. Call add() each frame, save() at the end."""

    def __init__(self, source: str, fps: float, image_size: tuple[int, int]):
        self.source = source
        self.fps = fps
        self.image_size = image_size
        self.tracks: dict[int, dict] = {}
        self.started = time.time()

    def add(self, frame_idx: int, t: float, tracks) -> None:
        for tr in tracks:
            d = tr.det
            if d.keypoints is None:
                continue
            rec = self.tracks.setdefault(tr.id, {"id": tr.id, "label": d.label, "frames": []})
            rec["frames"].append({
                "f": frame_idx, "t": round(t, 4),
                "kp2d": np.round(d.keypoints, 2).tolist(),
                "world": None if d.world is None else np.round(d.world, 4).tolist(),
                "movement": tr.label,
            })

    def to_dict(self, min_frames: int = 10) -> dict:
        return {
            "format": FORMAT, "version": VERSION, "source": self.source, "fps": self.fps,
            "image_size": list(self.image_size), "joints": JOINTS, "bones": BONES,
            "recorded_at": self.started,
            "tracks": [t for t in self.tracks.values() if len(t["frames"]) >= min_frames],
        }

    def save(self, path: str | Path, min_frames: int = 10) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(min_frames)))
        return path


def load_motion(path: str | Path) -> dict:
    d = json.loads(Path(path).read_text())
    if d.get("format") != FORMAT:
        raise ValueError(f"{path} is not a {FORMAT} file")
    return d
