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
from ..cameras import CameraRegistry, SavedCamera
from ..discovery import onvif_client
from ..jobs import JobQueue
from ..pipeline import Analyzer
from ..sources import Source
from ..vision import available_backends, get_backend
from ..vision.movement import MovementClassifier

ROOT = Path(os.environ.get("DECOMPRESS_HOME", Path.cwd() / "decompress_data"))
UPLOADS = ROOT / "uploads"
STATIC = Path(__file__).parent / "static"
BLENDER_SCRIPT = Path(__file__).resolve().parents[1] / "blender" / "import_motion.py"
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


class OnvifLogin(BaseModel):
    onvif_url: str
    user: str = ""
    password: str = ""


class NewCamera(BaseModel):
    name: str = ""
    ip: str = ""
    url: str = ""                 # RTSP/HTTP URL (without credentials); optional when onvif_url is given
    onvif_url: str = ""
    profile: str = ""             # ONVIF profile token; default = highest resolution
    user: str = ""
    password: str = ""
    remember: bool = True         # save the camera (credentials go to the OS keyring / encrypted store)
    autostart: bool = True
    start: bool = True


class Ptz(BaseModel):
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0
    stop: bool = False


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
    registry = CameraRegistry(ROOT / "cameras")
    app.state.registry = registry
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
            d["onvif_url"] = next((x for x in c.onvif_xaddrs if x.startswith("http") and "[" not in x), "")
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

    # ---- saved cameras (ONVIF + secure credentials) -----------------------
    def _start_camera(cam: SavedCamera) -> Source:
        s = Source(url=registry.stream_url(cam.id), name=cam.name, kind="camera",
                   camera_id=cam.id, ptz=cam.ptz).start(factory)
        sources[s.id] = s
        return s

    @app.post("/api/onvif/connect")
    def onvif_connect(body: OnvifLogin):
        try:
            cam = onvif_client.connect(body.onvif_url, body.user, body.password)
        except onvif_client.OnvifError as e:
            raise HTTPException(400, str(e))
        return {"media_url": cam.media_url, "ptz_url": cam.ptz_url,
                "profiles": [vars(p) for p in cam.profiles]}

    @app.get("/api/cameras")
    def list_cameras():
        return {"cameras": registry.list(), "secret_store": registry.secrets.name}

    @app.post("/api/cameras")
    def add_camera(body: NewCamera):
        cam = SavedCamera(name=body.name or body.ip or "camera", ip=body.ip, url=body.url,
                          onvif_url=body.onvif_url, autostart=body.autostart)
        if body.onvif_url:
            try:
                info = onvif_client.connect(body.onvif_url, body.user, body.password)
            except onvif_client.OnvifError as e:
                raise HTTPException(400, str(e))
            prof = next((p for p in info.profiles if p.token == body.profile), None) or info.best_profile()
            if prof is None:
                raise HTTPException(400, "camera has no RTSP stream profile")
            cam.url = cam.url or prof.stream_uri
            cam.profile, cam.ptz_url = prof.token, info.ptz_url
            cam.ptz = bool(info.ptz_url and prof.ptz)
        if not cam.url:
            raise HTTPException(400, "give a stream url or an onvif_url")
        if "@" in cam.url.split("//", 1)[-1].split("/", 1)[0]:
            raise HTTPException(400, "put the username/password in the user/password fields, not the URL")
        if body.remember:
            registry.add(cam, body.user, body.password)
        result = {"camera": vars(cam)}
        if body.start:
            if body.remember:
                result["source"] = _start_camera(cam).info()
            else:
                s = Source(url=onvif_client.with_credentials(cam.url, body.user, body.password), name=cam.name).start(factory)
                sources[s.id] = s
                result["source"] = s.info()
        return result

    @app.post("/api/cameras/{cid}/view")
    def view_camera(cid: str):
        if cid not in registry.cameras:
            raise HTTPException(404, "unknown camera")
        return _start_camera(registry.cameras[cid]).info()

    @app.delete("/api/cameras/{cid}")
    def delete_camera(cid: str):
        for sid in [k for k, s in sources.items() if s.camera_id == cid]:
            sources.pop(sid).stop()
        if not registry.remove(cid):
            raise HTTPException(404, "unknown camera")
        return {"removed": cid}

    @app.post("/api/sources/{sid}/ptz")
    def ptz(sid: str, body: Ptz):
        s = _get(sid)
        cam = registry.cameras.get(s.camera_id)
        if not (cam and cam.ptz):
            raise HTTPException(400, "this view has no PTZ")
        user, password = registry.credentials(cam.id)
        try:
            if body.stop:
                onvif_client.ptz_stop(cam.ptz_url, cam.profile, user, password)
            else:
                onvif_client.ptz_move(cam.ptz_url, cam.profile, body.pan, body.tilt, body.zoom, user, password)
        except onvif_client.OnvifError as e:
            raise HTTPException(502, str(e))
        return {"ok": True}

    for saved in list(registry.cameras.values()):         # bring back saved camera views on startup
        if saved.autostart:
            _start_camera(saved)

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
