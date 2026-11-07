"""Persistent queue of videos to analyse offline (every frame, with motion export)."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import cv2

from .pipeline import Analyzer

STATES = ("queued", "running", "done", "failed", "cancelled")


class JobQueue:
    def __init__(self, root: Path, analyzer_factory=None, on_change=None):
        self.root = Path(root)
        self.out_dir = self.root / "outputs"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "queue.json"
        self.analyzer_factory = analyzer_factory or Analyzer
        self.on_change = on_change or (lambda job: None)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.jobs: list[dict] = json.loads(self.db.read_text()) if self.db.exists() else []
        for j in self.jobs:                       # resume after a crash/restart
            if j["state"] == "running":
                j["state"] = "queued"
        self._worker: threading.Thread | None = None

    def _persist(self) -> None:
        self.db.write_text(json.dumps(self.jobs, indent=1))

    def add(self, path: str, name: str = "", write_video: bool = True) -> dict:
        job = {"id": uuid.uuid4().hex[:8], "path": str(path), "name": name or Path(path).name, "state": "queued",
               "progress": 0.0, "added": time.time(), "write_video": write_video, "outputs": {}, "error": "",
               "people": 0}
        with self.lock:
            self.jobs.append(job)
            self._persist()
        self.wake.set()
        return job

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            for j in self.jobs:
                if j["id"] == job_id and j["state"] in ("queued", "running"):
                    j["state"] = "cancelled"
                    self._persist()
                    return True
        return False

    def move(self, job_id: str, delta: int) -> None:
        with self.lock:
            i = next((i for i, j in enumerate(self.jobs) if j["id"] == job_id), None)
            if i is None:
                return
            k = max(0, min(len(self.jobs) - 1, i + delta))
            self.jobs.insert(k, self.jobs.pop(i))
            self._persist()

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(j) for j in self.jobs]

    def start(self) -> "JobQueue":
        self._worker = threading.Thread(target=self._loop, daemon=True, name="job-queue")
        self._worker.start()
        return self

    def _next(self) -> dict | None:
        with self.lock:
            return next((j for j in self.jobs if j["state"] == "queued"), None)

    def _loop(self) -> None:
        while True:
            job = self._next()
            if not job:
                self.wake.wait(2.0)
                self.wake.clear()
                continue
            self.run_job(job)

    def run_job(self, job: dict) -> None:
        with self.lock:
            job["state"], job["started"] = "running", time.time()
            self._persist()
        self.on_change(job)
        try:
            self._process(job)
            final = "cancelled" if job["state"] == "cancelled" else "done"
        except Exception as e:  # noqa: BLE001 — surface any failure on the job
            job["error"] = f"{type(e).__name__}: {e}"
            final = "failed"
        with self.lock:
            job["state"], job["finished"] = final, time.time()
            self._persist()
        self.on_change(job)

    def _process(self, job: dict) -> None:
        cap = cv2.VideoCapture(job["path"])
        if not cap.isOpened():
            raise IOError(f"cannot open {job['path']}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        an = self.analyzer_factory()
        an.start_recording(job["path"], fps, size)
        stem = self.out_dir / f"{job['id']}-{Path(job['path']).stem}"
        writer = None
        if job.get("write_video"):
            writer = cv2.VideoWriter(str(stem) + ".annotated.mp4", cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        i = 0
        try:
            while job["state"] != "cancelled":
                ok, frame = cap.read()
                if not ok:
                    break
                out, _ = an.process(frame, i / fps, annotate=writer is not None)
                if writer is not None:
                    writer.write(out)
                i += 1
                if total:
                    job["progress"] = round(min(1.0, i / total), 3)
        finally:
            an.close()
            cap.release()
            if writer is not None:
                writer.release()
        rec = an.stop_recording()
        motion = rec.save(str(stem) + ".motion.json")
        job["people"] = len(rec.to_dict()["tracks"])
        job["outputs"] = {"motion": str(motion)}
        if writer is not None:
            job["outputs"]["video"] = str(stem) + ".annotated.mp4"
        job["progress"] = 1.0
