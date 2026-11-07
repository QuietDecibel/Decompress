"""Build training sets from recorded motion files.

labels.json is a list of clips you have labelled:
    [{"motion": "decompress_data/outputs/ab12-clip.motion.json", "track": 1,
      "start": 0, "end": 90, "label": "waving"}, ...]
start/end are source frame numbers (inclusive start, exclusive end); omit them to use the whole track.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .motion import load_motion
from .vision.movement import MovementClassifier


def clip_keypoints(motion: dict, track: int, start: int | None = None, end: int | None = None) -> list[np.ndarray]:
    tr = next((t for t in motion["tracks"] if t["id"] == track), None)
    if tr is None:
        raise KeyError(f"track {track} not in motion file")
    return [np.array(f["kp2d"], dtype=np.float64) for f in tr["frames"]
            if (start is None or f["f"] >= start) and (end is None or f["f"] < end)]


def train_from_labels(labels_path: str | Path, k: int = 5) -> tuple[MovementClassifier, int]:
    labels = json.loads(Path(labels_path).read_text())
    base = Path(labels_path).parent
    cache: dict[str, dict] = {}
    seqs, ys = [], []
    for item in labels:
        mp = item["motion"] if Path(item["motion"]).is_absolute() or Path(item["motion"]).exists() else str(base / item["motion"])
        motion = cache.setdefault(mp, load_motion(mp))
        seq = clip_keypoints(motion, item["track"], item.get("start"), item.get("end"))
        if len(seq) >= 3:
            seqs.append(seq)
            ys.append(item["label"])
    if not seqs:
        raise ValueError("no usable labelled clips")
    return MovementClassifier(k=k).fit(seqs, ys), len(seqs)
