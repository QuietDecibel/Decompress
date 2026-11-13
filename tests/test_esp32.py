"""ESP32 / ESP32-S3 camera support, tested against fake firmwares on localhost."""
import base64
import gzip
import importlib
import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pytest

from decompress.discovery import esp32cam as ec
from decompress.discovery.scanner import probe_host

# The stock page uses the firmware's own numbering; these are OLD esp32-camera numbers on purpose,
# to prove Decompress reads them from the camera instead of assuming the current enum.
SIZES = {5: (320, 240), 8: (640, 480), 11: (1280, 720), 13: (1600, 1200)}
INDEX = ("<!doctype html><html><head><title>ESP32 OV2460</title></head><body>"
         '<select id="framesize" class="default-action">'
         '<option value="13">UXGA(1600x1200)</option><option value="11">HD(1280x720)</option>'
         '<option value="8" selected="selected">VGA(640x480)</option><option value="5">QVGA(320x240)</option>'
         "</select></body></html>")


def jpeg(w, h):
    img = np.zeros((h, w, 3), np.uint8)
    cv2.rectangle(img, (w // 4, h // 4), (3 * w // 4, 3 * h // 4), (40, 160, 220), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="text/plain", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def stream(self, cam):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=123456789000000000000987654321")
        self.end_headers()
        try:
            for _ in range(2000):
                frame = jpeg(*SIZES[cam.status["framesize"]])
                self.wfile.write(b"--123456789000000000000987654321\r\nContent-Type: image/jpeg\r\n"
                                 b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                time.sleep(0.05)                                      # ~20 fps
        except OSError:
            pass


class StockCam:
    """Espressif CameraWebServer: settings on port P, stream on P+1."""

    def __init__(self, auth=None):
        self.auth = auth
        self.status = {"xclk": 20, "pixformat": 4, "framesize": 8, "quality": 12, "brightness": 0, "contrast": 0,
                       "saturation": 0, "special_effect": 0, "wb_mode": 0, "awb": 1, "awb_gain": 1, "aec": 1, "aec2": 0,
                       "ae_level": 0, "aec_value": 168, "agc": 1, "agc_gain": 0, "gainceiling": 0, "bpc": 0, "wpc": 1,
                       "raw_gma": 1, "lenc": 1, "hmirror": 0, "vflip": 0, "dcw": 1, "colorbar": 0, "led_intensity": 0}
        self.calls = []
        cam = self

        class Ctrl(Handler):
            def do_GET(self):
                if cam.auth and self.headers.get("Authorization") != "Basic " + base64.b64encode(cam.auth.encode()).decode():
                    return self.send(401, b"", extra={"WWW-Authenticate": 'Basic realm="cam"'})
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path == "/status":
                    return self.send(200, json.dumps(cam.status).encode(), "application/json")
                if u.path == "/control":
                    cam.calls.append((q["var"], int(q["val"])))
                    if q["var"] not in cam.status:
                        return self.send(500)
                    cam.status[q["var"]] = int(q["val"])
                    return self.send(200)
                if u.path == "/capture":
                    return self.send(200, jpeg(*SIZES[cam.status["framesize"]]), "image/jpeg")
                if u.path == "/":
                    return self.send(200, gzip.compress(INDEX.encode()), "text/html", {"Content-Encoding": "gzip"})
                return self.send(404)

        class Stream(Handler):
            def do_GET(self):
                if self.path == "/stream":
                    return self.stream(cam)
                return self.send(404)

        while True:                                                   # need two consecutive free ports
            self.ctrl = ThreadingHTTPServer(("127.0.0.1", 0), Ctrl)
            self.port = self.ctrl.server_port
            try:
                self.strm = ThreadingHTTPServer(("127.0.0.1", self.port + 1), Stream)
                break
            except OSError:
                self.ctrl.server_close()
        for s in (self.ctrl, self.strm):
            s.daemon_threads = True
            threading.Thread(target=s.serve_forever, daemon=True).start()

    def close(self):
        for s in (self.ctrl, self.strm):
            s.shutdown()
            s.server_close()


@pytest.fixture
def stock():
    cam = StockCam()
    yield cam
    cam.close()


def serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------ unit tests
def test_parse_address():
    assert ec.parse_address("192.168.1.50") == ("192.168.1.50", 80)
    assert ec.parse_address(" http://cam.local:8080/stream ") == ("cam.local", 8080)
    for bad in ("", "a b", "http://u:p@1.2.3.4/", "1.2.3.4:99999", "-x"):
        with pytest.raises(ValueError):
            ec.parse_address(bad)


def test_identify_camerawebserver(stock):
    info = ec.identify("127.0.0.1", stock.port)
    assert info.firmware == "camerawebserver"
    assert info.stream_url == f"http://127.0.0.1:{stock.port + 1}/stream"
    assert info.snapshot_url == f"http://127.0.0.1:{stock.port}/capture"
    assert info.sensor == "OV2640"                                   # page title typo "OV2460" corrected
    assert info.framesizes_from == "camera"
    assert {f["value"]: (f["width"], f["height"]) for f in info.framesizes} == SIZES
    assert "led_intensity" in info.controls and "xclk" not in info.controls and "sharpness" not in info.controls
    d = info.to_dict()
    fs = next(c for c in d["controls"] if c["var"] == "framesize")
    assert fs["value"] == 8 and len(fs["options"]) == 4


def test_set_control_validates_and_applies(stock):
    base = f"http://127.0.0.1:{stock.port}"
    ec.set_control("camerawebserver", base, "framesize", 5)
    ec.set_control("camerawebserver", base, "vflip", 1)
    assert stock.status["framesize"] == 5 and stock.status["vflip"] == 1
    for var, val in (("quality", 99), ("vflip", 2), ("nope", 1), ("special_effect", 9)):
        with pytest.raises(ValueError):
            ec.set_control("camerawebserver", base, var, val)
    with pytest.raises(ec.Esp32Error):                               # a real setting this camera doesn't have
        ec.set_control("camerawebserver", base, "sharpness", 1)
    with pytest.raises(ec.Esp32Error):
        ec.set_control("mjpeg", "", "quality", 10)


def test_presets_match_the_cameras_own_sizes(stock):
    info = ec.identify("127.0.0.1", stock.port)
    pose = dict(ec.preset_settings("pose", info.framesizes, info.controls))
    assert pose["framesize"] == 8 and pose["quality"] == 12 and pose["awb"] == 1 and pose["colorbar"] == 0
    assert dict(ec.preset_settings("fast", info.framesizes, info.controls))["framesize"] == 5
    assert dict(ec.preset_settings("detail", info.framesizes, info.controls))["framesize"] == 11
    assert ec.preset_settings("pose", info.framesizes, ["quality"]) == [("quality", 12)]
    with pytest.raises(ValueError):
        ec.preset_settings("bogus", info.framesizes, info.controls)


def test_login_is_detected_and_used():
    cam = StockCam(auth="admin:pw")
    try:
        info = ec.identify("127.0.0.1", cam.port)
        assert info.needs_login and info.firmware == "unknown"
        info = ec.identify("127.0.0.1", cam.port, "admin", "pw")
        assert info.firmware == "camerawebserver" and info.controls
        ec.set_control("camerawebserver", info.base_url, "brightness", 2, "admin", "pw")
        assert cam.status["brightness"] == 2
        with pytest.raises(ec.Esp32Error, match="rejected"):
            ec.read_settings(info, "admin", "wrong")
    finally:
        cam.close()


def test_tasmota():
    sent = []
    values = {"WcStream": 1, "WcResolution": 8, "WcFlip": 0, "WcMirror": 1, "WcBrightness": 0}

    class Tas(Handler):
        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            if u.path != "/cm":
                return self.send(404)
            cmnd = dict(urllib.parse.parse_qsl(u.query))["cmnd"]
            name, _, arg = cmnd.partition(" ")
            if name not in values:
                return self.send(200, b'{"Command":"Unknown"}', "application/json")
            if arg:
                sent.append(cmnd)
                values[name] = int(arg)
            return self.send(200, json.dumps({name: values[name]}).encode(), "application/json")

    srv = serve(Tas)
    try:
        info = ec.identify("127.0.0.1", srv.server_port)
        assert info.firmware == "tasmota"
        assert info.controls == ["framesize", "brightness", "hmirror", "vflip"]
        assert info.status["hmirror"] == 1 and info.framesizes_from == "assumed"
        ec.set_control("tasmota", info.base_url, "framesize", 6)
        assert sent == ["WcResolution 6"] and values["WcResolution"] == 6
        with pytest.raises(ec.Esp32Error):
            ec.set_control("tasmota", info.base_url, "quality", 10)      # no such Tasmota command
    finally:
        srv.shutdown()


def test_generic_mjpeg_firmware():
    class Generic(Handler):
        def do_GET(self):
            if self.path == "/cam.mjpeg":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                return
            if self.path == "/cam.jpg":
                return self.send(200, jpeg(160, 120), "image/jpeg")
            return self.send(404)

    srv = serve(Generic)
    try:
        info = ec.identify("127.0.0.1", srv.server_port, allow_rtsp=False)
        assert info.firmware == "mjpeg" and info.controls == []
        assert info.stream_url.endswith(f":{srv.server_port}/cam.mjpeg")
        assert info.snapshot_url.endswith("/cam.jpg")
        assert ec.snapshot(info.snapshot_url).startswith(b"\xff\xd8")
    finally:
        srv.shutdown()


def test_other_web_servers_are_not_cameras():
    class Router(Handler):
        def do_GET(self):
            return self.send(404, b"<html>router</html>", "text/html")

    srv = serve(Router)
    try:
        assert ec.identify("127.0.0.1", srv.server_port, allow_rtsp=False) is None
        assert probe_host("127.0.0.1", ports=[srv.server_port], http_ports={srv.server_port}) is None
    finally:
        srv.shutdown()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    with pytest.raises(ec.Esp32Error):
        ec.identify("127.0.0.1", closed, timeout=0.3, allow_rtsp=False)


def test_network_scans_recognise_esp32(stock):
    cam = probe_host("127.0.0.1", ports=[stock.port], http_ports={stock.port})
    assert cam and "esp32" in cam.methods and cam.vendor_hint == "esp32"
    assert cam.stream_url == f"http://127.0.0.1:{stock.port + 1}/stream"
    found = ec.scan_esp32("127.0.0.1/32", port=stock.port, timeout=0.3)
    assert [i.firmware for i in found] == ["camerawebserver"]
    with pytest.raises(ValueError):
        ec.scan_esp32("8.8.8.0/24")


def test_live_view_measures_fps_and_follows_resolution(stock):
    from decompress.sources import Source
    stock.status["framesize"] = 5
    s = Source(url=f"http://127.0.0.1:{stock.port + 1}/stream", analyze=False).start()
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not (s.size == (320, 240) and s.fps != 25.0):
            time.sleep(0.2)
        assert s.size == (320, 240)
        assert 5 <= s.fps <= 30, s.fps                                # measured, not the 25 fps placeholder
        stock.status["framesize"] = 8
        s.reconnect()
        deadline = time.time() + 20
        while time.time() < deadline and s.size != (640, 480):
            time.sleep(0.2)
        assert s.size == (640, 480)
        assert s.last_jpeg.startswith(b"\xff\xd8")
    finally:
        s.stop()


# ------------------------------------------------------------------ web API
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("DECOMPRESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DECOMPRESS_SECRETS", "file")
    import decompress.web.app as webapp
    importlib.reload(webapp)
    monkeypatch.setattr(webapp, "get_backend", lambda name=None: __import__("decompress.vision.backends", fromlist=["x"]).HogBackend())
    with TestClient(webapp.create_app()) as c:
        yield c


def test_api_esp32_flow(client, stock):
    c = client
    assert c.get("/").text.count('data-tab="esp32"') == 1
    guide = c.get("/api/esp32/guide").json()
    assert any(b["chip"] == "ESP32-S3" for b in guide["boards"]) and "pose" in guide["presets"]

    addr = f"127.0.0.1:{stock.port}"
    r = c.post("/api/esp32/probe", json={"address": addr})
    assert r.status_code == 200 and r.json()["firmware"] == "camerawebserver"
    assert c.post("/api/esp32/probe", json={"address": "not an address"}).status_code == 400

    stock.status["framesize"] = 13
    r = c.post("/api/esp32/cameras", json={"address": addr, "name": "Desk", "preset": "pose", "start": False})
    assert r.status_code == 200, r.text
    cam = r.json()["camera"]
    assert cam["device"] == "esp32" and cam["url"] == f"http://127.0.0.1:{stock.port + 1}/stream"
    assert stock.status["framesize"] == 8 and ["framesize", 8] in r.json()["applied"]

    listing = c.get("/api/esp32/cameras").json()["cameras"]
    assert [x["name"] for x in listing] == ["Desk"] and listing[0]["source"] is None

    s = c.get(f"/api/esp32/cameras/{cam['id']}/settings").json()
    assert s["sensor"] == "OV2640" and any(x["var"] == "led_intensity" for x in s["controls"])
    r = c.post(f"/api/esp32/cameras/{cam['id']}/settings", json={"var": "brightness", "val": 1})
    assert r.status_code == 200 and next(x for x in r.json()["controls"] if x["var"] == "brightness")["value"] == 1
    assert c.post(f"/api/esp32/cameras/{cam['id']}/settings", json={"var": "quality", "val": 200}).status_code == 400
    assert c.post(f"/api/esp32/cameras/{cam['id']}/settings", json={"var": "sharpness", "val": 1}).status_code == 502
    r = c.post(f"/api/esp32/cameras/{cam['id']}/preset/fast")
    assert r.status_code == 200 and stock.status["framesize"] == 5
    assert c.post(f"/api/esp32/cameras/{cam['id']}/preset/nope").status_code == 400

    snap = c.get(f"/api/esp32/cameras/{cam['id']}/snapshot.jpg")
    assert snap.status_code == 200 and snap.content.startswith(b"\xff\xd8")

    assert c.get("/api/esp32/cameras/nope/settings").status_code == 404
    assert c.delete(f"/api/cameras/{cam['id']}").status_code == 200
    assert c.get("/api/esp32/cameras").json()["cameras"] == []


def test_api_esp32_needs_login(client):
    cam = StockCam(auth="admin:pw")
    try:
        addr = f"127.0.0.1:{cam.port}"
        assert c_post(client, addr).status_code == 401
        r = c_post(client, addr, user="admin", password="pw")
        assert r.status_code == 200 and r.json()["camera"]["has_credentials"]
        assert "pw" not in json.dumps(r.json()["camera"])
        cid = r.json()["camera"]["id"]
        assert client.get(f"/api/esp32/cameras/{cid}/settings").status_code == 200     # uses the stored login
    finally:
        cam.close()


def c_post(client, addr, **kw):
    return client.post("/api/esp32/cameras", json={"address": addr, "start": False, **kw})
