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
    seqs, ys = load_clips(labels, Path(labels_path).parent)
    if not seqs:
        raise ValueError("no usable labelled clips")
    return MovementClassifier(k=k).fit(seqs, ys), len(seqs)


def load_clips(items: list[dict], base: Path | None = None) -> tuple[list[list[np.ndarray]], list[str]]:
    cache: dict[str, dict] = {}
    seqs, ys = [], []
    for item in items:
        mp = item["motion"]
        if base is not None and not Path(mp).is_absolute() and not Path(mp).exists():
            mp = str(base / mp)
        motion = cache.setdefault(mp, load_motion(mp))
        seq = clip_keypoints(motion, item["track"], item.get("start"), item.get("end"))
        if len(seq) >= 3:
            seqs.append(seq)
            ys.append(item["label"])
    return seqs, ys


def leave_one_out_accuracy(seqs, ys, k: int = 5) -> float | None:
    """Hold out each clip in turn, train on the rest, predict it. None if any label has a single clip."""
    if len(set(ys)) < 2 or min(ys.count(y) for y in set(ys)) < 2:
        return None
    hits = 0
    for i in range(len(seqs)):
        clf = MovementClassifier(k=k).fit(seqs[:i] + seqs[i + 1:], ys[:i] + ys[i + 1:])
        hits += clf.predict(seqs[i])[0] == ys[i]
    return hits / len(seqs)
