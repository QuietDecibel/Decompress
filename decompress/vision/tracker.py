"""Multi-object tracker: greedy IoU + centre-distance association with stable IDs."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .schema import Detection, iou


@dataclass
class Track:
    id: int
    det: Detection
    hits: int = 1
    misses: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=90))   # (t, Detection)
    label: str = ""          # movement description / classifier label

    @property
    def confirmed(self) -> bool:
        return self.hits >= 3


class Tracker:
    def __init__(self, iou_thresh: float = 0.25, max_misses: int = 15, max_center_dist: float = 0.06):
        self.iou_thresh = iou_thresh
        self.max_misses = max_misses
        self.max_center_dist = max_center_dist   # fraction of frame diagonal
        self.tracks: list[Track] = []
        self._next = 1

    def update(self, dets: list[Detection], t: float, frame_diag: float = 1000.0) -> list[Track]:
        pairs = []
        for ti, tr in enumerate(self.tracks):
            for di, d in enumerate(dets):
                if d.label != tr.det.label:
                    continue
                score = iou(tr.det.box, d.box)
                if score < self.iou_thresh:
                    dist = np.hypot(*(np.subtract(tr.det.center, d.center))) / frame_diag
                    if dist > self.max_center_dist:
                        continue
                    score = 0.01 * (1 - dist)   # weak match by proximity
                pairs.append((score, ti, di))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti); used_d.add(di)
            tr = self.tracks[ti]
            tr.det, tr.hits, tr.misses = dets[di], tr.hits + 1, 0
            tr.history.append((t, dets[di]))
        for ti, tr in enumerate(self.tracks):
            if ti not in used_t:
                tr.misses += 1
        for di, d in enumerate(dets):
            if di not in used_d:
                tr = Track(id=self._next, det=d)
                tr.history.append((t, d))
                self._next += 1
                self.tracks.append(tr)
        self.tracks = [tr for tr in self.tracks if tr.misses <= self.max_misses]
        return [tr for tr in self.tracks if tr.misses == 0]
