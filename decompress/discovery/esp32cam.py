"""ESP32, ESP32-S2 and ESP32-S3 camera boards: recognise the firmware, find the stream, read and change settings.

Camera support on these boards comes from the firmware, not the board, so Decompress talks to the firmware:

  * ``camerawebserver``: Espressif's CameraWebServer example (Arduino-ESP32 / ESP-IDF) and the many forks
    shipped with AI-Thinker, M5Stack, Freenove, Seeed, DFRobot and other boards. Settings server on port 80
    (``/status``, ``/control``, ``/capture``), MJPEG stream on the next port up (81) at ``/stream``.
  * ``tasmota``: Tasmota webcam builds. Settings via ``/cm?cmnd=Wc...``, stream on port 81 at ``/stream``.
  * ``mjpeg``: ESPHome ``esp32_camera_web_server``, yoursunny/esp32cam and other MJPEG firmware (stream only).
  * ``rtsp``: Micro-RTSP / esp32cam-rtsp (stream only; only offered when you give the address yourself).

Nothing here guesses passwords. If a camera asks for a login, the user types their own.
"""
from __future__ import annotations

import base64
import gzip
import http.client
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .rtsp import rtsp_options


class Esp32Error(Exception):
    """The camera could not be reached or refused a request."""


# esp32-camera ``framesize_t`` as numbered by current releases. Firmware built with an older esp32-camera numbers
# these differently, so Decompress reads the list from the camera's own web page and only falls back to this one.
DEFAULT_FRAMESIZES = [
    (0, "96X96", 96, 96), (1, "QQVGA", 160, 120), (2, "128X128", 128, 128), (3, "QCIF", 176, 144),
    (4, "HQVGA", 240, 176), (5, "240X240", 240, 240), (6, "QVGA", 320, 240), (7, "320X320", 320, 320),
    (8, "CIF", 400, 296), (9, "HVGA", 480, 320), (10, "VGA", 640, 480), (11, "SVGA", 800, 600),
    (12, "XGA", 1024, 768), (13, "HD", 1280, 720), (14, "SXGA", 1280, 1024), (15, "UXGA", 1600, 1200),
    (16, "FHD", 1920, 1080), (17, "P_HD", 720, 1280), (18, "P_3MP", 864, 1536), (19, "QXGA", 2048, 1536),
    (20, "QHD", 2560, 1440), (21, "WQXGA", 2560, 1600), (22, "P_FHD", 1080, 1920), (23, "QSXGA", 2560, 1920),
    (24, "5MP", 2592, 1944),
]

_GAIN = [{"value": i, "label": f"{2 ** (i + 1)}x"} for i in range(7)]
_EFFECTS = ["No effect", "Negative", "Grayscale", "Red tint", "Green tint", "Blue tint", "Sepia"]
_WB = ["Auto", "Sunny", "Cloudy", "Office", "Home"]

# Every setting Decompress can show, in display order. Only the ones a camera reports are shown for it.
CONTROLS: dict[str, dict] = {
    "framesize": {"label": "Resolution", "type": "select"},
    "quality": {"label": "JPEG quality (lower = sharper)", "type": "range", "min": 4, "max": 63},
    "brightness": {"label": "Brightness", "type": "range", "min": -2, "max": 2},
    "contrast": {"label": "Contrast", "type": "range", "min": -2, "max": 2},
    "saturation": {"label": "Saturation", "type": "range", "min": -2, "max": 2},
    "sharpness": {"label": "Sharpness", "type": "range", "min": -2, "max": 2},
    "special_effect": {"label": "Effect", "type": "select",
                       "options": [{"value": i, "label": t} for i, t in enumerate(_EFFECTS)]},
    "awb": {"label": "Auto white balance", "type": "toggle"},
    "awb_gain": {"label": "AWB gain", "type": "toggle"},
    "wb_mode": {"label": "White balance mode", "type": "select",
                "options": [{"value": i, "label": t} for i, t in enumerate(_WB)]},
    "aec": {"label": "Auto exposure", "type": "toggle"},
    "aec2": {"label": "Auto exposure (DSP)", "type": "toggle"},
    "ae_level": {"label": "Exposure level", "type": "range", "min": -2, "max": 2},
    "aec_value": {"label": "Exposure (manual)", "type": "range", "min": 0, "max": 1200},
    "agc": {"label": "Auto gain", "type": "toggle"},
    "agc_gain": {"label": "Gain (manual)", "type": "range", "min": 0, "max": 30},
    "gainceiling": {"label": "Gain ceiling", "type": "select", "options": _GAIN},
    "bpc": {"label": "Black pixel correction", "type": "toggle"},
    "wpc": {"label": "White pixel correction", "type": "toggle"},
    "raw_gma": {"label": "Gamma correction", "type": "toggle"},
    "lenc": {"label": "Lens correction", "type": "toggle"},
    "hmirror": {"label": "Mirror", "type": "toggle"},
    "vflip": {"label": "Flip", "type": "toggle"},
    "dcw": {"label": "Downsize (DCW)", "type": "toggle"},
    "colorbar": {"label": "Colour bar test pattern", "type": "toggle"},
    "led_intensity": {"label": "Flash LED", "type": "range", "min": 0, "max": 255},
}

# Decompress setting -> Tasmota webcam command. Settings a Tasmota build doesn't know are simply not shown.
TASMOTA_COMMANDS = {
    "framesize": "WcResolution", "vflip": "WcFlip", "hmirror": "WcMirror", "brightness": "WcBrightness",
    "contrast": "WcContrast", "saturation": "WcSaturation", "special_effect": "WcSpecialEffect", "awb": "WcAWB",
    "awb_gain": "WcAWBGain", "wb_mode": "WcWBMode", "aec": "WcAEC", "aec2": "WcAECDSP", "ae_level": "WcAELevel",
    "aec_value": "WcAECValue", "agc": "WcAGC", "agc_gain": "WcAGCGain", "gainceiling": "WcGainCeiling",
    "bpc": "WcBPC", "wpc": "WcWPC", "raw_gma": "WcRawGMA", "lenc": "WcLensCorrection", "dcw": "WcDCW",
    "colorbar": "WcColorbar",
}

FIRMWARE_LABELS = {
    "camerawebserver": "Espressif CameraWebServer",
    "tasmota": "Tasmota webcam",
    "mjpeg": "MJPEG stream (ESPHome, esp32cam or similar)",
    "rtsp": "RTSP stream (Micro-RTSP / esp32cam-rtsp)",
    "unknown": "Needs a login",
}

# What each preset aims for. The resolution is matched to the nearest one the camera offers.
PRESETS = {
    "pose": {"label": "Pose tracking", "size": (640, 480), "quality": 12,
             "help": "640×480, good JPEG quality, automatic exposure and white balance. Best balance of speed and accuracy."},
    "fast": {"label": "Fast", "size": (320, 240), "quality": 15,
             "help": "320×240 for the highest frame rate on weak boards or Wi-Fi. People must be close to the camera."},
    "detail": {"label": "Detail", "size": (1280, 720), "quality": 10,
               "help": "1280×720 for people further away. Expect a lower frame rate, especially on the classic ESP32."},
}
_AUTO = {"awb": 1, "awb_gain": 1, "aec": 1, "agc": 1, "bpc": 1, "wpc": 1, "raw_gma": 1, "lenc": 1, "dcw": 1, "colorbar": 0}

# Board reference for the Boards table. "model" is the board choice in CameraWebServer's board_config.h.
BOARDS = [
    {"name": "AI-Thinker ESP32-CAM (and clones)", "chip": "ESP32", "sensor": "OV2640", "model": "CAMERA_MODEL_AI_THINKER",
     "notes": "Needs an ESP32-CAM-MB or USB-serial adapter; hold IO0 to GND to flash. Flash LED on GPIO 4."},
    {"name": "ESP32-WROVER-KIT", "chip": "ESP32", "sensor": "OV2640 / OV3660", "model": "CAMERA_MODEL_WROVER_KIT", "notes": ""},
    {"name": "ESP-EYE", "chip": "ESP32", "sensor": "OV2640", "model": "CAMERA_MODEL_ESP_EYE", "notes": ""},
    {"name": "ESP32-S3-EYE", "chip": "ESP32-S3", "sensor": "OV2640", "model": "CAMERA_MODEL_ESP32S3_EYE",
     "notes": "Native USB. Arduino: PSRAM = OPI PSRAM."},
    {"name": "Freenove ESP32-S3-WROOM CAM", "chip": "ESP32-S3", "sensor": "OV2640 / OV3660", "model": "CAMERA_MODEL_ESP32S3_EYE",
     "notes": "Uses the ESP32-S3-EYE pin map. Arduino: PSRAM = OPI PSRAM."},
    {"name": "Freenove ESP32-WROVER CAM", "chip": "ESP32", "sensor": "OV2640", "model": "CAMERA_MODEL_WROVER_KIT", "notes": ""},
    {"name": "Seeed XIAO ESP32S3 Sense", "chip": "ESP32-S3", "sensor": "OV2640 / OV3660 / OV5640", "model": "CAMERA_MODEL_XIAO_ESP32S3",
     "notes": "Tiny; runs warm when streaming. Arduino: PSRAM = OPI PSRAM."},
    {"name": "M5Stack Camera (PSRAM)", "chip": "ESP32", "sensor": "OV2640 / OV3660", "model": "CAMERA_MODEL_M5STACK_PSRAM", "notes": ""},
    {"name": "M5Stack Camera V2", "chip": "ESP32", "sensor": "OV2640 / OV3660", "model": "CAMERA_MODEL_M5STACK_V2_PSRAM", "notes": ""},
    {"name": "M5Stack Camera Wide", "chip": "ESP32", "sensor": "OV2640", "model": "CAMERA_MODEL_M5STACK_WIDE", "notes": "Wide-angle lens."},
    {"name": "M5Stack ESP32CAM (no PSRAM)", "chip": "ESP32", "sensor": "OV2640", "model": "CAMERA_MODEL_M5STACK_ESP32CAM",
     "notes": "No PSRAM: keep to QVGA/CIF."},
    {"name": "M5Stack UnitCam", "chip": "ESP32", "sensor": "OV2640", "model": "CAMERA_MODEL_M5STACK_UNITCAM", "notes": "No PSRAM: keep to QVGA/CIF."},
    {"name": "M5Stack CamS3 Unit", "chip": "ESP32-S3", "sensor": "OV2640", "model": "CAMERA_MODEL_M5STACK_CAMS3_UNIT", "notes": ""},
    {"name": "TTGO T-Journal", "chip": "ESP32", "sensor": "OV2640", "model": "CAMERA_MODEL_TTGO_T_JOURNAL", "notes": ""},
    {"name": "DFRobot FireBeetle 2 ESP32-S3", "chip": "ESP32-S3", "sensor": "OV2640", "model": "CAMERA_MODEL_DFRobot_FireBeetle2_ESP32S3", "notes": ""},
    {"name": "DFRobot Romeo ESP32-S3", "chip": "ESP32-S3", "sensor": "OV2640", "model": "CAMERA_MODEL_DFRobot_Romeo_ESP32S3", "notes": ""},
    {"name": "ESP32-S3-CAM-LCD", "chip": "ESP32-S3", "sensor": "OV2640", "model": "CAMERA_MODEL_ESP32S3_CAM_LCD", "notes": ""},
    {"name": "ESP32-S2 camera board (Kaluga)", "chip": "ESP32-S2", "sensor": "OV2640", "model": "CAMERA_MODEL_ESP32S2_CAM_BOARD",
     "notes": "Single core: expect lower frame rates."},
    {"name": "Any other ESP32 / S2 / S3 camera board", "chip": "any", "sensor": "any supported by esp32-camera",
     "model": "your board's pin map", "notes": "Works with any firmware below; Decompress talks to the firmware, not the board."},
]

FIRMWARE = [
    {"name": "Espressif CameraWebServer (Arduino-ESP32 / ESP-IDF example, and board-maker forks)",
     "stream": "http://<ip>:81/stream", "settings": "All camera settings, presets, flash LED, snapshots",
     "notes": "Recommended. Arduino IDE: File → Examples → ESP32 → Camera → CameraWebServer."},
    {"name": "Tasmota webcam build (tasmota32-webcam)", "stream": "http://<ip>:81/stream",
     "settings": "Resolution, flip, mirror, image and exposure settings", "notes": "Set WcStream 1 on the camera to turn the stream on."},
    {"name": "ESPHome (esp32_camera + esp32_camera_web_server)", "stream": "http://<ip>:8080/",
     "settings": "Stream only; change settings in the ESPHome YAML", "notes": "Use mode: stream on the web server component."},
    {"name": "yoursunny/esp32cam and other MJPEG sketches", "stream": "/stream, /cam.mjpeg, /mjpeg/1, …",
     "settings": "Stream only", "notes": "Decompress tries the common stream paths."},
    {"name": "Micro-RTSP / esp32cam-rtsp", "stream": "rtsp://<ip>:554/mjpeg/1",
     "settings": "Stream only", "notes": "Add by address; RTSP-only devices are not listed by the ESP32 scan."},
]

_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # LAN devices: never go through a proxy


# ---------------------------------------------------------------- data
@dataclass
class Esp32Info:
    host: str
    firmware: str                      # camerawebserver | tasmota | mjpeg | rtsp | unknown
    base_url: str = ""                 # settings server, e.g. http://192.168.1.50 ("" if the firmware has none)
    stream_url: str = ""
    snapshot_url: str = ""
    sensor: str = ""
    framesizes: list[dict] = field(default_factory=list)
    framesizes_from: str = ""          # "camera" (read from the camera) or "assumed" (default numbering)
    controls: list[str] = field(default_factory=list)
    status: dict = field(default_factory=dict)
    needs_login: bool = False

    def control_list(self) -> list[dict]:
        out = []
        for var in self.controls:
            meta = dict(CONTROLS[var], var=var, value=self.status.get(var))
            if var == "framesize":
                meta["options"] = [{"value": f["value"], "label": f["label"]} for f in self.framesizes]
            out.append(meta)
        return out

    def to_dict(self) -> dict:
        return {"host": self.host, "firmware": self.firmware, "firmware_label": FIRMWARE_LABELS.get(self.firmware, self.firmware),
                "base_url": self.base_url, "stream_url": self.stream_url, "snapshot_url": self.snapshot_url,
                "sensor": self.sensor, "framesizes": self.framesizes, "framesizes_from": self.framesizes_from,
                "controls": self.control_list(), "needs_login": self.needs_login}


# ---------------------------------------------------------------- HTTP
def _safe(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.hostname}{f':{p.port}' if p.port else ''}{p.path}"


def _get(url: str, timeout: float, user: str = "", password: str = "", headers_only: bool = False,
         limit: int = 4_000_000) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "Decompress"})
    if user:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode())
    try:
        r = _opener.open(req, timeout=timeout)
        with r:
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            body = b"" if headers_only else r.read(limit)
            code = r.status
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        e.close()
        return e.code, hdrs, b""
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise Esp32Error(f"cannot reach {_safe(url)}: {getattr(e, 'reason', e)}") from None
    if body[:2] == b"\x1f\x8b":                     # CameraWebServer serves its page gzipped whatever we ask for
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError):
            pass
    return code, hdrs, body


def _json(body: bytes) -> dict | None:
    try:
        d = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _hostport(host: str, port: int) -> str:
    return host if port == 80 else f"{host}:{port}"


def parse_address(text: str) -> tuple[str, int]:
    """'192.168.1.50', '192.168.1.50:8080', 'http://cam.local/' -> (host, port). Raises ValueError."""
    t = text.strip()
    if "://" in t:
        t = t.split("://", 1)[1]
    t = t.split("/", 1)[0].split("?", 1)[0]
    if "@" in t:
        raise ValueError("put the username and password in their own fields, not in the address")
    host, _, port = t.partition(":")
    if not _HOST_RE.match(host or ""):
        raise ValueError(f"'{text}' is not a camera address; use an IP address or host name")
    if port and not (port.isdigit() and 0 < int(port) < 65536):
        raise ValueError(f"bad port in '{text}'")
    return host, int(port) if port else 80


# ---------------------------------------------------------------- firmware specifics
def tasmota_cmd(base: str, cmnd: str, user: str = "", password: str = "", timeout: float = 2.0) -> dict | None:
    q = {"cmnd": cmnd}
    if user:
        q.update(user=user, password=password)
    code, _, body = _get(f"{base}/cm?{urllib.parse.urlencode(q)}", timeout)
    return _json(body) if code == 200 else None


def _pick(d: dict, key: str):
    return next((v for k, v in d.items() if k.lower() == key.lower()), None)


def _read_index(info: Esp32Info, user: str, password: str, timeout: float) -> None:
    """CameraWebServer's page holds the sensor's name and the resolution list in this firmware's own numbering."""
    try:
        code, _, body = _get(info.base_url + "/", timeout, user, password)
    except Esp32Error:
        return
    if code != 200:
        return
    html = body.decode("utf-8", "replace")
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if title:
        m = re.search(r"\b(OV\d{4}|GC\d{4}|BF\d{4}|HM\d{4}|SC\d{3,4}|NT\d{5})\b", title.group(1), re.I)
        if m:
            info.sensor = m.group(1).upper().replace("OV2460", "OV2640")   # the stock page title has this typo
    sel = re.search(r"<select[^>]*id=[\"']framesize[\"'][^>]*>(.*?)</select>", html, re.S | re.I)
    if sel:
        sizes = []
        for val, label in re.findall(r"<option[^>]*value=[\"']?(\d+)[\"']?[^>]*>([^<]+)</option>", sel.group(1), re.I):
            wh = re.search(r"(\d+)\s*[xX×]\s*(\d+)", label)
            sizes.append({"value": int(val), "label": label.strip(),
                          "width": int(wh.group(1)) if wh else 0, "height": int(wh.group(2)) if wh else 0})
        if sizes:
            info.framesizes, info.framesizes_from = sorted(sizes, key=lambda f: f["value"]), "camera"


def _default_framesizes(info: Esp32Info) -> None:
    if not info.framesizes:
        info.framesizes = [{"value": v, "label": f"{n} ({w}×{h})", "width": w, "height": h} for v, n, w, h in DEFAULT_FRAMESIZES]
        info.framesizes_from = "assumed"


def read_settings(info: Esp32Info, user: str = "", password: str = "", timeout: float = 3.0) -> Esp32Info:
    """Fill in the current settings, the setting list, the sensor and the resolution list."""
    if info.firmware == "camerawebserver":
        code, _, body = _get(info.base_url + "/status", timeout, user, password)
        if code == 401:
            raise Esp32Error("the camera rejected the username or password")
        status = _json(body) if code == 200 else None
        if not status:
            raise Esp32Error(f"the camera did not return its settings (HTTP {code})")
        info.status = status
        info.controls = [k for k in CONTROLS if k in status]
        _read_index(info, user, password, timeout)
        _default_framesizes(info)
    elif info.firmware == "tasmota":
        status = {}
        for var, cmd in TASMOTA_COMMANDS.items():
            r = tasmota_cmd(info.base_url, cmd, user, password, timeout)
            val = _pick(r, cmd) if r else None
            if isinstance(val, (int, float)) or (isinstance(val, str) and val.lstrip("-").isdigit()):
                status[var] = int(val)
        info.status, info.controls = status, [k for k in CONTROLS if k in status]
        _default_framesizes(info)
    return info


def set_control(firmware: str, base_url: str, var: str, val: int, user: str = "", password: str = "",
                timeout: float = 3.0) -> None:
    """Change one setting on the camera. ValueError for a bad request, Esp32Error if the camera refuses."""
    meta = CONTROLS.get(var)
    if meta is None:
        raise ValueError(f"unknown setting '{var}'")
    val = int(val)
    if meta["type"] == "toggle" and val not in (0, 1):
        raise ValueError(f"{var} must be 0 or 1")
    if meta["type"] == "range" and not meta["min"] <= val <= meta["max"]:
        raise ValueError(f"{var} must be between {meta['min']} and {meta['max']}")
    if meta.get("options") and val not in [o["value"] for o in meta["options"]]:
        raise ValueError(f"{val} is not a valid value for {var}")
    if var == "framesize" and not 0 <= val <= 40:
        raise ValueError("framesize out of range")
    if firmware == "camerawebserver":
        code, _, _ = _get(f"{base_url}/control?{urllib.parse.urlencode({'var': var, 'val': val})}", timeout, user, password)
        if code != 200:
            raise Esp32Error(f"the camera refused {var}={val} (HTTP {code})")
    elif firmware == "tasmota" and var in TASMOTA_COMMANDS:
        r = tasmota_cmd(base_url, f"{TASMOTA_COMMANDS[var]} {val}", user, password, timeout)
        if not r or _pick(r, "Command") == "Unknown":
            raise Esp32Error(f"the camera refused {var}={val}")
    else:
        raise Esp32Error("this firmware has no settings Decompress can change; change them on the camera")


def preset_settings(name: str, framesizes: list[dict], controls: list[str]) -> list[tuple[str, int]]:
    """The (setting, value) changes a preset makes on this camera, resolution first."""
    p = PRESETS.get(name)
    if p is None:
        raise ValueError(f"unknown preset '{name}'")
    steps: list[tuple[str, int]] = []
    sized = [f for f in framesizes if f.get("width")]
    if "framesize" in controls and sized:
        tw, th = p["size"]
        landscape = [f for f in sized if f["width"] >= f["height"]] or sized
        best = min(landscape, key=lambda f: (abs(f["width"] * f["height"] - tw * th), f["width"] > tw))
        steps.append(("framesize", best["value"]))
    if "quality" in controls:
        steps.append(("quality", p["quality"]))
    steps += [(k, v) for k, v in _AUTO.items() if k in controls]
    return steps


def snapshot(url: str, user: str = "", password: str = "", timeout: float = 5.0) -> bytes:
    code, hdrs, body = _get(url, timeout, user, password)
    if code != 200 or not body.startswith(b"\xff\xd8"):
        raise Esp32Error(f"no picture from the camera (HTTP {code})")
    return body


# ---------------------------------------------------------------- identify / scan
def _content_type(url: str, timeout: float, user: str, password: str) -> str:
    try:
        code, hdrs, _ = _get(url, timeout, user, password, headers_only=True)
    except Esp32Error:
        return ""
    return hdrs.get("content-type", "").lower() if code == 200 else ("401" if code == 401 else "")


def _stream_candidates(host: str, port: int) -> list[str]:
    paths = [(port + 1, "/stream"), (port, "/stream"), (port, "/mjpeg/1"), (port, "/cam.mjpeg"), (port + 1, "/cam.mjpeg"),
             (port, "/video"), (port, "/mjpeg"), (8080, "/"), (port + 1, "/")]
    return list(dict.fromkeys(f"http://{_hostport(host, p)}{path}" for p, path in paths))


def _snapshot_candidates(host: str, port: int) -> list[str]:
    paths = [(port, "/capture"), (port, "/cam.jpg"), (port, "/snapshot.jpg"), (port, "/snapshot"), (port, "/jpg"), (8081, "/")]
    return [f"http://{_hostport(host, p)}{path}" for p, path in paths]


def identify(host: str, port: int = 80, user: str = "", password: str = "", timeout: float = 2.0,
             quick: bool = False, allow_rtsp: bool = True) -> Esp32Info | None:
    """Work out which ESP32 camera firmware answers at host:port.

    Returns None if nothing there looks like a camera. Raises Esp32Error if nothing answers at all.
    quick=True only checks the two firmwares with a settings API plus the usual stream port (for network scans).
    """
    base = f"http://{_hostport(host, port)}"
    reachable, needs_login = True, False
    try:
        code, _, body = _get(base + "/status", timeout, user, password)
    except Esp32Error:
        reachable, code, body = False, 0, b""
    status = _json(body) if code == 200 else None
    if status and "framesize" in status and "quality" in status:
        info = Esp32Info(host=host, firmware="camerawebserver", base_url=base,
                         stream_url=f"http://{_hostport(host, port + 1)}/stream", snapshot_url=base + "/capture")
        if quick:
            info.status, info.controls = status, [k for k in CONTROLS if k in status]
            return info
        return read_settings(info, user, password, timeout)
    if code == 401:      # a protected settings server: ask for the login rather than settle for a bare stream
        return Esp32Info(host=host, firmware="unknown", base_url=base, needs_login=True)

    if reachable:
        try:
            t = tasmota_cmd(base, "WcStream", user, password, timeout)
        except Esp32Error:
            t = None
        if t and _pick(t, "WcStream") is not None:
            # Tasmota always streams on port 81; port + 1 is the same thing on a default install.
            info = Esp32Info(host=host, firmware="tasmota", base_url=base,
                             stream_url=f"http://{_hostport(host, port + 1)}/stream", snapshot_url=base + "/snapshot.jpg")
            return info if quick else read_settings(info, user, password, timeout)

    port_open: dict[int, bool] = {port: reachable}

    def is_open(url: str) -> bool:                    # try each port once; closed ports are slow on Windows
        p = urllib.parse.urlsplit(url).port or 80
        if p not in port_open:
            port_open[p] = _open(host, p, timeout)
        return port_open[p]

    streams = _stream_candidates(host, port)[:1] if quick else _stream_candidates(host, port)
    for url in streams:
        if not is_open(url):
            continue
        ctype = _content_type(url, timeout, user, password)
        needs_login = needs_login or ctype == "401"
        if ctype.startswith("multipart/x-mixed-replace"):
            info = Esp32Info(host=host, firmware="mjpeg", stream_url=url)
            if not quick:
                info.snapshot_url = next((s for s in _snapshot_candidates(host, port) if is_open(s)
                                          and _content_type(s, timeout, user, password).startswith("image/jpeg")), "")
            return info

    if allow_rtsp and not quick:
        for p in (554, 8554):
            ok, _ = rtsp_options(host, p, timeout)
            if ok:
                return Esp32Info(host=host, firmware="rtsp", stream_url=f"rtsp://{_hostport(host, p)}/mjpeg/1")

    if needs_login:
        return Esp32Info(host=host, firmware="unknown", base_url=base, needs_login=True)
    if not reachable and not quick:
        raise Esp32Error(f"nothing answered at {base}")
    return None


def _open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_esp32(subnet: str, port: int = 80, timeout: float = 0.6, workers: int = 64) -> list[Esp32Info]:
    """Find ESP32 cameras on a private subnet you own (same limits as the main scanner)."""
    from .scanner import validate_subnet
    net = validate_subnet(subnet)

    def one(ip: str) -> Esp32Info | None:
        if not any(_open(ip, p, timeout) for p in dict.fromkeys((port, port + 1, 8080))):
            return None
        try:
            return identify(ip, port, timeout=timeout * 3, allow_rtsp=False)
        except Esp32Error:
            return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        found = [i for i in ex.map(one, (str(h) for h in net.hosts())) if i]
    return sorted(found, key=lambda i: tuple(int(x) if x.isdigit() else 0 for x in i.host.split(".")))
