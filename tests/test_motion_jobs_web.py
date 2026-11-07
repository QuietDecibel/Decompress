import importlib
import json
import time

import cv2
import numpy as np
import pytest

from decompress.blender import import_motion as im   # pure helpers, no bpy needed
from decompress.jobs import JobQueue
from decompress.motion import MotionRecorder, load_motion
from decompress.pipeline import Analyzer
from decompress.training import train_from_labels
from decompress.vision import Tracker
from decompress.vision.backends import Backend
from decompress.vision.schema import J
from helpers import det, skeleton


class FakePose(Backend):
    """Finds the white rectangle in a synthetic frame and returns a skeleton there."""
    name = "fake"

    def __call__(self, frame, t_ms):
        ys, xs = np.where(frame[:, :, 0] > 200)
        if len(xs) == 0:
            return []
        return [det(skeleton(cx=float(xs.mean()), cy=float(ys.max()), scale=60, arms_up=t_ms > 500))]


def make_video(path, n=40, size=(320, 240), fps=20):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(n):
        f = np.zeros((size[1], size[0], 3), np.uint8)
        x = 60 + 4 * i
        cv2.rectangle(f, (x - 15, 60), (x + 15, 200), (255, 255, 255), -1)
        w.write(f)
    w.release()
    return path


def test_recorder_roundtrip_and_blender_prepare(tmp_path):
    tr = Tracker()
    rec = MotionRecorder("clip.mp4", 30, (640, 480))
    for i in range(20):
        rec.add(i, i / 30, tr.update([det(skeleton(cx=200 + 3 * i))], i / 30))
    path = rec.save(tmp_path / "x.motion.json")
    motion = load_motion(path)
    assert motion["tracks"][0]["frames"][0]["kp2d"][J["nose"]][2] == pytest.approx(0.9)
    prepared = im.prepare(motion)
    assert len(prepared) == 1
    pts = prepared[0]["points"]
    assert pts.shape == (20, 19, 3)                     # 17 joints + pelvis + neck
    assert prepared[0]["frames"][0] == 1
    names = prepared[0]["joints"]
    head_z = pts[0, names.index("nose"), 2]
    ankle_z = pts[0, names.index("left_ankle"), 2]
    assert head_z - ankle_z == pytest.approx(1.7 * 0.95 * 1.6 / 1.6, rel=0.05)   # ~1.6 m nose height
    assert np.all(pts[:, :, 1] == 0)                    # 2D-only -> camera plane


def test_blender_world_coords_axes():
    world = [[0.0, -0.5, 0.1]] * 17
    world[J["left_ankle"]] = [0.0, 0.8, 0.0]
    track = {"frames": [{"f": 0, "kp2d": [[0, 0, 1]] * 17, "world": world}]}
    out = im.to_blender_coords(track, (640, 480))
    assert out[0, J["nose"], 2] == pytest.approx(1.3)       # y-down -> z-up, feet at 0
    assert out[0, J["nose"], 1] == pytest.approx(0.1)       # depth -> Blender Y


def test_smooth_preserves_constant_and_shape():
    pts = np.ones((10, 3, 3))
    pts[5] = 4
    out = im.smooth(pts, 5)
    assert out.shape == pts.shape and out[0, 0, 0] == 1 and out[5, 0, 0] == pytest.approx(1.6)


def test_job_queue_end_to_end(tmp_path):
    video = make_video(tmp_path / "walk.mp4")
    q = JobQueue(tmp_path / "data", analyzer_factory=lambda: Analyzer(backend=FakePose()))
    job = q.add(str(video))
    q.run_job(job)
    assert job["state"] == "done", job["error"]
    assert job["progress"] == 1.0 and job["people"] == 1
    motion = load_motion(job["outputs"]["motion"])
    frames = motion["tracks"][0]["frames"]
    assert len(frames) == 40 and motion["fps"] == pytest.approx(20)
    assert any("arms raised" in f["movement"] for f in frames)
    cap = cv2.VideoCapture(job["outputs"]["video"])
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 40
    # queue persisted and reloadable
    assert json.loads((tmp_path / "data" / "queue.json").read_text())[0]["state"] == "done"

    # train from the recording
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([
        {"motion": job["outputs"]["motion"], "track": 1, "start": 0, "end": 10, "label": "arms down"},
        {"motion": job["outputs"]["motion"], "track": 1, "start": 15, "end": 40, "label": "arms up"}]))
    clf, n = train_from_labels(labels, k=3)
    assert n == 2
    seq = [np.array(f["kp2d"]) for f in frames[20:40]]
    assert clf.predict(seq)[0] == "arms up"


def test_queue_order_and_cancel(tmp_path):
    q = JobQueue(tmp_path)
    a, b = q.add("a.mp4"), q.add("b.mp4")
    q.move(b["id"], -1)
    assert [j["name"] for j in q.list()] == ["b.mp4", "a.mp4"]
    assert q.cancel(a["id"]) and q.list()[1]["state"] == "cancelled"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("DECOMPRESS_HOME", str(tmp_path / "home"))
    import decompress.web.app as webapp
    importlib.reload(webapp)
    monkeypatch.setattr(webapp, "get_backend", lambda name=None: FakePose())
    app = webapp.create_app()
    with TestClient(app) as c:
        c.app_ = app
        yield c


def test_web_upload_view_and_queue(client, tmp_path):
    video = make_video(tmp_path / "clip.mp4")
    r = client.post("/api/upload", files={"file": ("clip.mp4", video.read_bytes(), "video/mp4")},
                    data={"enqueue": "true", "view": "true"})
    assert r.status_code == 200, r.text
    sid, jid = r.json()["source"]["id"], r.json()["job"]["id"]
    for _ in range(100):                                    # wait for the grid tile + queue job
        jobs = client.get("/api/queue").json()
        snap = client.get(f"/api/sources/{sid}/snapshot.jpg")
        if snap.status_code == 200 and jobs[0]["state"] == "done":
            break
        time.sleep(0.1)
    assert snap.headers["content-type"] == "image/jpeg"
    job = next(j for j in jobs if j["id"] == jid)
    assert job["state"] == "done"
    assert client.get("/api/files", params={"path": job["outputs"]["motion"]}).status_code == 200
    assert client.get("/api/files", params={"path": "/etc/passwd"}).status_code == 404
    info = client.get("/api/sources").json()[0]
    assert info["status"] == "live" and info["kind"] == "file"
    r = client.post(f"/api/sources/{sid}/record", json={"on": True}); assert r.json()["recording"]
    time.sleep(0.6)
    r = client.post(f"/api/sources/{sid}/record", json={"on": False})
    assert r.json()["last_motion_file"].endswith(".motion.json")
    assert client.delete(f"/api/sources/{sid}").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/blender/import_motion.py").status_code == 200


def test_web_rejects_public_subnet(client):
    assert client.get("/api/discover", params={"subnet": "8.8.8.0/24"}).status_code == 400


def test_source_redacts_credentials(client):
    r = client.post("/api/sources", json={"url": "rtsp://admin:hunter2@127.0.0.1:1/x", "analyze": False}).json()
    assert "hunter2" not in json.dumps(r)
    client.delete(f"/api/sources/{r['id']}")
