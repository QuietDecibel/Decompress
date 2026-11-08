import base64
import importlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from decompress.cameras import CameraRegistry, FileStore, SavedCamera
from decompress.discovery import onvif_client as oc

USER, PASSWORD = "admin", "s3cret!"


class FakeOnvif(BaseHTTPRequestHandler):
    """Tiny ONVIF device: checks WS-Security PasswordDigest, answers the calls Decompress makes."""
    calls: list = []

    def log_message(self, *a):
        pass

    def _reply(self, code, body):
        xml = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
               f'xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
               f"<s:Body>{body}</s:Body></s:Envelope>").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/soap+xml")
        self.send_header("Content-Length", str(len(xml)))
        self.end_headers()
        self.wfile.write(xml)

    def do_POST(self):
        req = self.rfile.read(int(self.headers["Content-Length"])).decode()
        user = re.search(r"<wsse:Username>(.*?)<", req)
        digest = re.search(r"<wsse:Password[^>]*>(.*?)<", req)
        nonce = re.search(r"<wsse:Nonce[^>]*>(.*?)<", req)
        created = re.search(r"<wsu:Created>(.*?)<", req)
        ok = user and user.group(1) == USER and digest.group(1) == oc.password_digest(
            PASSWORD, base64.b64decode(nonce.group(1)), created.group(1))
        if not ok:
            return self._reply(400, "<s:Fault><s:Code><s:Value>s:Sender</s:Value></s:Code>"
                                    "<s:Reason><s:Text>ter:NotAuthorized</s:Text></s:Reason></s:Fault>")
        host = f"http://10.9.9.9:{self.server.server_port}"     # advertise a "wrong" internal host on purpose
        op = re.search(r"<s:Body><(\w+:\w+)", req).group(1)
        FakeOnvif.calls.append((op, req))
        if op == "tds:GetCapabilities":
            return self._reply(200, f"<tt:Capabilities><tt:Media><tt:XAddr>{host}/onvif/media</tt:XAddr></tt:Media>"
                                    f"<tt:PTZ><tt:XAddr>{host}/onvif/ptz</tt:XAddr></tt:PTZ></tt:Capabilities>")
        if op == "trt:GetProfiles":
            prof = ('<trt:Profiles token="{t}"><tt:Name>{t}</tt:Name><tt:VideoEncoderConfiguration><tt:Resolution>'
                    "<tt:Width>{w}</tt:Width><tt:Height>{h}</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration>"
                    "<tt:PTZConfiguration/></trt:Profiles>")
            return self._reply(200, prof.format(t="sub", w=640, h=360) + prof.format(t="main", w=1920, h=1080))
        if op == "trt:GetStreamUri":
            tok = re.search(r"<trt:ProfileToken>(.*?)<", req).group(1)
            return self._reply(200, f"<tt:Uri>rtsp://10.9.9.9:554/{tok}</tt:Uri>")
        if op in ("tptz:ContinuousMove", "tptz:Stop"):
            return self._reply(200, "")
        return self._reply(400, "<s:Fault><s:Reason><s:Text>unsupported</s:Text></s:Reason></s:Fault>")


@pytest.fixture
def onvif():
    FakeOnvif.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOnvif)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/onvif/device_service"
    srv.shutdown()


def test_password_digest_known_vector():
    # base64(sha1(nonce + created + password))
    assert oc.password_digest("pw", b"\x00" * 16, "2026-01-01T00:00:00.000Z") == \
        base64.b64encode(__import__("hashlib").sha1(b"\x00" * 16 + b"2026-01-01T00:00:00.000Zpw").digest()).decode()


def test_connect_profiles_and_stream_uris(onvif):
    cam = oc.connect(onvif, USER, PASSWORD)
    assert [p.token for p in cam.profiles] == ["sub", "main"]
    best = cam.best_profile()
    assert best.token == "main" and best.width == 1920 and best.ptz
    assert best.stream_uri == "rtsp://127.0.0.1:554/main"        # internal host rewritten to the one we reached
    assert cam.ptz_url.startswith("http://127.0.0.1:")
    assert "@" not in best.stream_uri


def test_bad_password_is_reported(onvif):
    with pytest.raises(oc.OnvifError, match="credentials"):
        oc.connect(onvif, USER, "wrong")


def test_ptz_calls(onvif):
    cam = oc.connect(onvif, USER, PASSWORD)
    oc.ptz_move(cam.ptz_url, "main", 0.5, -2, 0, USER, PASSWORD)
    oc.ptz_stop(cam.ptz_url, "main", USER, PASSWORD)
    move = next(r for op, r in FakeOnvif.calls if op == "tptz:ContinuousMove")
    assert 'x="0.5" y="-1.0"' in move                              # velocity clamped to [-1, 1]
    assert FakeOnvif.calls[-1][0] == "tptz:Stop"


def test_with_credentials_encodes():
    assert oc.with_credentials("rtsp://cam/x", "a@b", "p:w") == "rtsp://a%40b:p%3Aw@cam/x"
    assert oc.with_credentials("rtsp://u:p@cam/x", "z", "z") == "rtsp://u:p@cam/x"


def test_registry_never_stores_plaintext(tmp_path):
    reg = CameraRegistry(tmp_path, secrets=FileStore(tmp_path))
    cam = reg.add(SavedCamera(name="Porch", url="rtsp://10.0.0.5/stream"), USER, PASSWORD)
    for f in tmp_path.iterdir():
        assert PASSWORD.encode() not in f.read_bytes(), f.name
    reg2 = CameraRegistry(tmp_path, secrets=FileStore(tmp_path))     # reload from disk
    assert reg2.stream_url(cam.id) == "rtsp://admin:s3cret%21@10.0.0.5/stream"
    assert reg2.remove(cam.id) and reg2.credentials(cam.id) == ("", "")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("DECOMPRESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DECOMPRESS_SECRETS", "file")
    import decompress.web.app as webapp
    importlib.reload(webapp)
    monkeypatch.setattr(webapp, "get_backend", lambda name=None: __import__("decompress.vision.backends", fromlist=["x"]).HogBackend())
    with TestClient(webapp.create_app()) as c:
        yield c, webapp


def test_api_camera_flow(client, onvif, tmp_path):
    c, webapp = client
    r = c.post("/api/onvif/connect", json={"onvif_url": onvif, "user": USER, "password": PASSWORD})
    assert r.status_code == 200 and len(r.json()["profiles"]) == 2
    assert c.post("/api/onvif/connect", json={"onvif_url": onvif, "user": USER, "password": "nope"}).status_code == 400

    r = c.post("/api/cameras", json={"name": "Garage", "onvif_url": onvif, "user": USER, "password": PASSWORD,
                                     "start": True})
    assert r.status_code == 200, r.text
    cam, src = r.json()["camera"], r.json()["source"]
    assert cam["url"] == "rtsp://127.0.0.1:554/main" and cam["ptz"] and src["ptz"]
    assert PASSWORD not in r.text
    listing = c.get("/api/cameras").json()
    assert listing["secret_store"] == "encrypted-file" and listing["cameras"][0]["has_credentials"]
    assert PASSWORD.encode() not in (tmp_path / "home" / "cameras" / "cameras.json").read_bytes()

    assert c.post(f"/api/sources/{src['id']}/ptz", json={"pan": 0.3}).json() == {"ok": True}
    assert c.post(f"/api/sources/{src['id']}/ptz", json={"stop": True}).status_code == 200
    assert [op for op, _ in FakeOnvif.calls][-2:] == ["tptz:ContinuousMove", "tptz:Stop"]

    # credentials in the URL are refused (they belong in the secure store)
    assert c.post("/api/cameras", json={"url": "rtsp://a:b@1.2.3.4/x"}).status_code == 400
    assert c.delete(f"/api/cameras/{cam['id']}").status_code == 200
    assert c.get("/api/sources").json() == []


def test_saved_cameras_autostart(client, tmp_path):
    c, webapp = client
    c.post("/api/cameras", json={"name": "Yard", "url": "rtsp://127.0.0.1:1/x", "user": "u", "password": "p",
                                 "start": False})
    from fastapi.testclient import TestClient
    with TestClient(webapp.create_app()) as c2:          # "restart" the app
        srcs = c2.get("/api/sources").json()
        assert [s["name"] for s in srcs] == ["Yard"] and srcs[0]["camera_id"]
        assert "p@" not in srcs[0]["url"]
