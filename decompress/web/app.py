"""Decompress web app: discovery, multi-view grid with live analysis, video queue, motion exports."""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from .. import discovery, progress
from ..jobs import JobQueue
from ..pipeline import Analyzer
from ..sources import Source
from ..vision import available_backends, get_backend
from ..vision.movement import MovementClassifier

ROOT = Path(os.environ.get("DECOMPRESS_HOME", Path.cwd() / "decompress_data"))
UPLOADS = ROOT / "uploads"
STATIC = Path(__file__).parent / "static"
BLENDER_SCRIPT = Path(__file__).resolve().parents[2] / "blender" / "import_motion.py"
CLASSIFIER_PATH = ROOT / "models" / "movement.json"


class NewSource(BaseModel):
    url: str
    name: str = ""
    kind: str = "camera"
    analyze: bool = True


class Toggle(BaseModel):
    on: bool


class NewJob(BaseModel):
    path: str
    write_video: bool = True


class Move(BaseModel):
    delta: int


def create_app(backend: str | None = None, hub_project: str | None = None) -> FastAPI:
    app = FastAPI(title="Decompress")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    backend = backend or os.environ.get("DECOMPRESS_BACKEND") or None

    def factory() -> Analyzer:
        clf = MovementClassifier.load(CLASSIFIER_PATH) if CLASSIFIER_PATH.exists() else None
        return Analyzer(backend=get_backend(backend), classifier=clf)

    def on_job(job):
        if hub_project:
            progress.log(hub_project, f"queue job {job['name']}: {job['state']}")

    sources: dict[str, Source] = {}
    queue = JobQueue(ROOT, analyzer_factory=factory, on_change=on_job).start()
    app.state.sources, app.state.queue = sources, queue

    # ---- UI --------------------------------------------------------------
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/backends")
    def backends():
        return {"available": available_backends(), "selected": backend or "auto",
                "classifier": CLASSIFIER_PATH.exists()}

    # ---- discovery -------------------------------------------------------
    @app.get("/api/discover")
    def discover(subnet: str | None = None, timeout: float = 2.0):
        try:
            cams = discovery.discover_all(subnet, timeout=timeout)
        except ValueError as e:
            raise HTTPException(400, str(e))
        out = []
        for c in cams:
            d = c.to_dict()
            port = next((p for p in c.ports if p in (554, 8554)), 554)
            d["candidate_urls"] = discovery.candidate_urls(c.ip, port, c.vendor_hint, "USER", "PASS")[:4]
            out.append(d)
        return {"subnet": subnet or discovery.local_subnet(), "cameras": out}

    # ---- sources / grid --------------------------------------------------
    @app.get("/api/sources")
    def list_sources():
        return [s.info() for s in sources.values()]

    @app.post("/api/sources")
    def add_source(body: NewSource):
        kind = "webcam" if body.url.isdigit() else body.kind
        s = Source(url=body.url, name=body.name, kind=kind, analyze=body.analyze).start(factory)
        sources[s.id] = s
        return s.info()

    def _get(sid: str) -> Source:
        if sid not in sources:
            raise HTTPException(404, "unknown source")
        return sources[sid]

    @app.delete("/api/sources/{sid}")
    def remove_source(sid: str):
        _get(sid).stop()
        del sources[sid]
        return {"removed": sid}

    @app.post("/api/sources/{sid}/analyze")
    def toggle_analyze(sid: str, body: Toggle):
        s = _get(sid)
        s.analyze = body.on
        return s.info()

    @app.post("/api/sources/{sid}/record")
    def toggle_record(sid: str, body: Toggle):
        s = _get(sid)
        if body.on:
            s.start_recording()
        else:
            s.stop_recording(ROOT / "outputs")
        return s.info()

    @app.get("/api/sources/{sid}/snapshot.jpg")
    def snapshot(sid: str):
        s = _get(sid)
        if not s.last_jpeg:
            raise HTTPException(503, "no frame yet")
        return Response(s.last_jpeg, media_type="image/jpeg")

    @app.get("/api/sources/{sid}/mjpeg")
    async def mjpeg(sid: str):
        s = _get(sid)

        async def gen():
            last = b""
            while sid in sources:
                if s.last_jpeg and s.last_jpeg is not last:
                    last = s.last_jpeg
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(last)).encode() + b"\r\n\r\n" + last + b"\r\n"
                await asyncio.sleep(0.04)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    # ---- import + queue --------------------------------------------------
    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...), enqueue: bool = Form(True), view: bool = Form(False)):
        name = Path(file.filename or "video.mp4").name
        dst = UPLOADS / name
        i = 1
        while dst.exists():
            dst = UPLOADS / f"{Path(name).stem}-{i}{Path(name).suffix}"
            i += 1
        with dst.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        result = {"path": str(dst)}
        if enqueue:
            result["job"] = queue.add(str(dst))
        if view:
            s = Source(url=str(dst), kind="file").start(factory)
            sources[s.id] = s
            result["source"] = s.info()
        return result

    @app.get("/api/queue")
    def list_queue():
        return queue.list()

    @app.post("/api/queue")
    def add_job(body: NewJob):
        if not Path(body.path).is_file():
            raise HTTPException(400, "file not found")
        return queue.add(body.path, write_video=body.write_video)

    @app.post("/api/queue/{jid}/cancel")
    def cancel_job(jid: str):
        return {"cancelled": queue.cancel(jid)}

    @app.post("/api/queue/{jid}/move")
    def move_job(jid: str, body: Move):
        queue.move(jid, body.delta)
        return queue.list()

    @app.get("/api/files")
    def get_file(path: str):
        p = Path(path).resolve()
        if ROOT.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(p, filename=p.name)

    @app.get("/api/blender/import_motion.py")
    def blender_script():
        return FileResponse(BLENDER_SCRIPT, filename="import_motion.py")

    # live classifier reload (after `python -m decompress train`)
    @app.post("/api/classifier/reload")
    def reload_classifier():
        if not CLASSIFIER_PATH.exists():
            raise HTTPException(404, "no trained classifier; run `python -m decompress train`")
        clf = MovementClassifier.load(CLASSIFIER_PATH)
        for s in sources.values():
            if s._analyzer:
                s._analyzer.classifier = clf
        return {"loaded": True, "labels": sorted(set(clf.y))}

    return app
