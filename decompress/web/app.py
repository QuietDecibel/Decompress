"""Decompress web app: discovery, multi-view grid with live analysis, video queue, motion exports."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from .. import discovery, progress
from ..cameras import CameraRegistry, SavedCamera
from ..discovery import esp32cam, onvif_client
from ..jobs import JobQueue
from ..pipeline import Analyzer
from ..sources import Source
from ..vision import available_backends, get_backend
from ..motion import load_motion
from ..training import clip_keypoints, leave_one_out_accuracy, load_clips
from ..vision.movement import MovementClassifier

ROOT = Path(os.environ.get("DECOMPRESS_HOME", Path.cwd() / "decompress_data"))
UPLOADS = ROOT / "uploads"
STATIC = Path(__file__).parent / "static"
BLENDER_SCRIPT = Path(__file__).resolve().parents[1] / "blender" / "import_motion.py"
CLASSIFIER_PATH = ROOT / "models" / "movement.json"
LABELS_PATH = ROOT / "models" / "labels.json"


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


class LabelClip(BaseModel):
    motion: str
    track: int
    start: int
    end: int
    label: str


class Ptz(BaseModel):
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0
    stop: bool = False


class Esp32Address(BaseModel):
    address: str                  # 192.168.1.50, 192.168.1.50:8080 or esp32cam.local
    user: str = ""
    password: str = ""


class NewEsp32(Esp32Address):
    name: str = ""
    remember: bool = True
    autostart: bool = True
    start: bool = True
    preset: str = ""              # e.g. "pose": tune the camera when adding it


class Esp32Setting(BaseModel):
    var: str
    val: int


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
            if c.stream_url:                                  # ESP32 boards: the stream address is already known
                d["candidate_urls"] = [c.stream_url]
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
        return _register(cam, body.user, body.password, body.remember, body.start)

    def _register(cam: SavedCamera, user: str, password: str, remember: bool, start: bool) -> dict:
        if remember:
            registry.add(cam, user, password)
        result = {"camera": vars(cam)}
        if start:
            if remember:
                result["source"] = _start_camera(cam).info()
            else:
                s = Source(url=onvif_client.with_credentials(cam.url, user, password), name=cam.name).start(factory)
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

    # ---- ESP32 / ESP32-S2 / ESP32-S3 cameras --------------------------------
    def _esp32_probe(address: str, user: str, password: str) -> esp32cam.Esp32Info:
        try:
            host, port = esp32cam.parse_address(address)
            info = esp32cam.identify(host, port, user, password, timeout=2.5)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except esp32cam.Esp32Error as e:
            raise HTTPException(502, str(e))
        if info is None:
            raise HTTPException(404, f"{address} answered, but no camera stream was found there")
        return info

    def _esp32(cid: str) -> SavedCamera:
        cam = registry.cameras.get(cid)
        if cam is None or cam.device != "esp32":
            raise HTTPException(404, "unknown ESP32 camera")
        return cam

    def _esp32_info(cam: SavedCamera) -> esp32cam.Esp32Info:
        info = esp32cam.Esp32Info(host=cam.ip, firmware=cam.firmware, base_url=cam.control_url,
                                  stream_url=cam.url, snapshot_url=cam.snapshot_url)
        if cam.control_url and cam.firmware in ("camerawebserver", "tasmota"):
            try:
                esp32cam.read_settings(info, *registry.credentials(cam.id))
            except esp32cam.Esp32Error as e:
                raise HTTPException(502, str(e))
        return info

    def _views_of(cid: str) -> list[Source]:
        return [s for s in sources.values() if s.camera_id == cid]

    def _esp32_apply(cam: SavedCamera, steps: list[tuple[str, int]]) -> None:
        user, password = registry.credentials(cam.id)
        try:
            for var, val in steps:
                esp32cam.set_control(cam.firmware, cam.control_url, var, val, user, password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except esp32cam.Esp32Error as e:
            raise HTTPException(502, str(e))
        if any(var == "framesize" for var, _ in steps):      # new frame size: reopen the stream cleanly
            for s in _views_of(cam.id):
                s.reconnect()

    @app.get("/api/esp32/guide")
    def esp32_guide():
        return {"boards": esp32cam.BOARDS, "firmware": esp32cam.FIRMWARE,
                "presets": {k: {"label": v["label"], "help": v["help"]} for k, v in esp32cam.PRESETS.items()}}

    @app.get("/api/esp32/scan")
    def esp32_scan(subnet: str | None = None):
        net = subnet or discovery.local_subnet()
        try:
            found = esp32cam.scan_esp32(net)
        except ValueError as e:
            raise HTTPException(400, str(e))
        saved = {c.ip: c.id for c in registry.cameras.values() if c.device == "esp32"}
        return {"subnet": net, "cameras": [dict(i.to_dict(), saved=saved.get(i.host, "")) for i in found]}

    @app.post("/api/esp32/probe")
    def esp32_probe(body: Esp32Address):
        return _esp32_probe(body.address, body.user, body.password).to_dict()

    @app.get("/api/esp32/cameras")
    def esp32_cameras():
        out = []
        for cam in registry.cameras.values():
            if cam.device == "esp32":
                views = _views_of(cam.id)
                out.append({**asdict(cam), "firmware_label": esp32cam.FIRMWARE_LABELS.get(cam.firmware, cam.firmware),
                            "source": views[0].info() if views else None})
        return {"cameras": out}

    @app.post("/api/esp32/cameras")
    def esp32_add(body: NewEsp32):
        info = _esp32_probe(body.address, body.user, body.password)
        if info.needs_login:
            raise HTTPException(401, "this camera asks for a login: enter its username and password")
        cam = SavedCamera(name=body.name.strip() or f"ESP32 {info.host}", ip=info.host, url=info.stream_url,
                          autostart=body.autostart, device="esp32", firmware=info.firmware,
                          control_url=info.base_url, snapshot_url=info.snapshot_url)
        applied: list = []
        if body.preset and info.controls:
            applied = esp32cam.preset_settings(body.preset, info.framesizes, info.controls)
            user, password = body.user, body.password
            try:
                for var, val in applied:
                    esp32cam.set_control(info.firmware, info.base_url, var, val, user, password)
            except (ValueError, esp32cam.Esp32Error) as e:
                raise HTTPException(502, f"camera found, but tuning it failed: {e}")
        result = _register(cam, body.user, body.password, body.remember, body.start)
        result["info"], result["applied"] = info.to_dict(), applied
        return result

    @app.get("/api/esp32/cameras/{cid}/settings")
    def esp32_settings(cid: str):
        return _esp32_info(_esp32(cid)).to_dict()

    @app.post("/api/esp32/cameras/{cid}/settings")
    def esp32_set(cid: str, body: Esp32Setting):
        cam = _esp32(cid)
        _esp32_apply(cam, [(body.var, body.val)])
        return _esp32_info(cam).to_dict()

    @app.post("/api/esp32/cameras/{cid}/preset/{name}")
    def esp32_preset(cid: str, name: str):
        cam = _esp32(cid)
        info = _esp32_info(cam)
        if not info.controls:
            raise HTTPException(400, "this firmware has no settings Decompress can change")
        try:
            steps = esp32cam.preset_settings(name, info.framesizes, info.controls)
        except ValueError as e:
            raise HTTPException(400, str(e))
        _esp32_apply(cam, steps)
        return {"applied": steps, **_esp32_info(cam).to_dict()}

    @app.get("/api/esp32/cameras/{cid}/snapshot.jpg")
    def esp32_snapshot(cid: str):
        cam = _esp32(cid)
        nocache = {"Cache-Control": "no-store"}
        if cam.snapshot_url:
            try:
                return Response(esp32cam.snapshot(cam.snapshot_url, *registry.credentials(cid)),
                                media_type="image/jpeg", headers=nocache)
            except esp32cam.Esp32Error:
                pass
        view = next((s for s in _views_of(cid) if s.last_jpeg), None)   # fall back to Decompress's own view
        if view:
            return Response(view.last_jpeg, media_type="image/jpeg", headers=nocache)
        raise HTTPException(503, "no picture from this camera right now")

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
    def _push_classifier(clf) -> None:
        for s in sources.values():
            if s._analyzer:
                s._analyzer.classifier = clf

    @app.post("/api/classifier/reload")
    def reload_classifier():
        if not CLASSIFIER_PATH.exists():
            raise HTTPException(404, "no trained classifier; run `python -m decompress train`")
        clf = MovementClassifier.load(CLASSIFIER_PATH)
        _push_classifier(clf)
        return {"loaded": True, "labels": sorted(set(clf.y))}

    # ---- labelling + training -----------------------------------------------
    def _inside_root(path: str) -> Path:
        p = Path(path).resolve()
        if ROOT.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404, "not found")
        return p

    @app.get("/api/motions")
    def list_motions():
        out = []
        for f in sorted((ROOT / "outputs").glob("*.motion.json"), key=lambda p: -p.stat().st_mtime):
            try:
                m = load_motion(f)
            except Exception:
                continue
            src = m.get("source", "")
            out.append({"path": str(f), "name": f.name, "source": Path(src).name if Path(src).is_file() else src,
                        "has_video": Path(src).is_file(), "fps": m.get("fps"), "image_size": m.get("image_size"),
                        "tracks": [{"id": t["id"], "label": t.get("label", "person"), "frames": len(t["frames"]),
                                    "first": t["frames"][0]["f"], "last": t["frames"][-1]["f"]} for t in m["tracks"]]})
        return out

    @app.get("/api/motions/data")
    def motion_data(path: str):
        return load_motion(_inside_root(path))

    frame_caps: dict[str, cv2.VideoCapture] = {}
    frame_lock = threading.Lock()

    @app.get("/api/motions/frame")
    def motion_frame(path: str, f: int):
        src = Path(load_motion(_inside_root(path)).get("source", ""))
        if not src.is_file():
            raise HTTPException(404, "source video not available (live recording)")
        with frame_lock:
            cap = frame_caps.get(str(src))
            if cap is None:
                cap = frame_caps[str(src)] = cv2.VideoCapture(str(src))
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, f))
            ok, frame = cap.read()
        if not ok:
            raise HTTPException(404, "frame out of range")
        h, w = frame.shape[:2]
        if w > 960:
            frame = cv2.resize(frame, (960, round(h * 960 / w)))
        return Response(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes(), media_type="image/jpeg")

    def _labels() -> list[dict]:
        return json.loads(LABELS_PATH.read_text()) if LABELS_PATH.exists() else []

    def _save_labels(items: list[dict]) -> None:
        LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
        LABELS_PATH.write_text(json.dumps(items, indent=1))

    @app.get("/api/labels")
    def get_labels():
        return _labels()

    @app.post("/api/labels")
    def add_label(body: LabelClip):
        _inside_root(body.motion)
        if body.end - body.start < 3 or not body.label.strip():
            raise HTTPException(400, "a clip needs a label and at least 3 frames")
        try:
            n = len(clip_keypoints(load_motion(body.motion), body.track, body.start, body.end))
        except KeyError:
            raise HTTPException(400, f"track {body.track} is not in this recording")
        if n < 3:
            raise HTTPException(400, f"track #{body.track} only appears in {n} frame(s) of that range; pick a range where it is visible")
        item = {"id": uuid.uuid4().hex[:8], **body.model_dump(), "label": body.label.strip()}
        _save_labels(_labels() + [item])
        return item

    @app.delete("/api/labels/{lid}")
    def delete_label(lid: str):
        _save_labels([x for x in _labels() if x.get("id") != lid])
        return {"removed": lid}

    @app.post("/api/train")
    def train():
        items = _labels()
        try:
            seqs, ys = load_clips(items)
        except (KeyError, FileNotFoundError, ValueError) as e:
            raise HTTPException(400, f"cannot load labelled clips: {e}")
        if len(set(ys)) < 2:
            raise HTTPException(400, "label clips of at least two different movements first")
        clf = MovementClassifier().fit(seqs, ys)
        CLASSIFIER_PATH.parent.mkdir(parents=True, exist_ok=True)
        clf.save(CLASSIFIER_PATH)
        _push_classifier(clf)
        acc = leave_one_out_accuracy(seqs, ys)
        return {"clips": len(seqs), "labels": {y: ys.count(y) for y in sorted(set(ys))},
                "accuracy": None if acc is None else round(acc, 3)}

    @app.get("/api/predict")
    def predict(path: str, track: int, frame: int):
        if not CLASSIFIER_PATH.exists():
            return {"label": "", "confidence": 0}
        seq = clip_keypoints(load_motion(_inside_root(path)), track, frame - 30, frame + 1)
        if len(seq) < 5:
            return {"label": "", "confidence": 0}
        label, conf = MovementClassifier.load(CLASSIFIER_PATH).predict(seq)
        return {"label": label, "confidence": round(conf, 2)}

    return app
