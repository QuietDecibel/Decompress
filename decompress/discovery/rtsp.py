"""RTSP helpers: a minimal OPTIONS probe and common stream-path templates."""
from __future__ import annotations

import socket
from urllib.parse import quote

# Well-known stream paths. The user supplies their own camera's credentials.
PATH_TEMPLATES: dict[str, list[str]] = {
    "hikvision": ["/Streaming/Channels/101", "/Streaming/Channels/102"],
    "dahua": ["/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1"],
    "amcrest": ["/cam/realmonitor?channel=1&subtype=0"],
    "reolink": ["/h264Preview_01_main", "/h264Preview_01_sub"],
    "axis": ["/axis-media/media.amp"],
    "uniview": ["/media/video1", "/media/video2"],
    "foscam": ["/videoMain", "/videoSub"],
    "tp-link": ["/stream1", "/stream2"],
    "ubiquiti": ["/s0", "/s1"],
    "generic": ["/", "/live", "/stream1", "/h264", "/live/ch00_0", "/11", "/onvif1"],
}


def candidate_urls(ip: str, port: int = 554, vendor: str = "", user: str = "", password: str = "") -> list[str]:
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user else ""
    paths = PATH_TEMPLATES.get(vendor, []) + [p for p in PATH_TEMPLATES["generic"] if p not in PATH_TEMPLATES.get(vendor, [])]
    return [f"rtsp://{auth}{ip}:{port}{p}" for p in paths]


def rtsp_options(ip: str, port: int = 554, timeout: float = 1.0) -> tuple[bool, str]:
    """Send RTSP OPTIONS. Returns (is_rtsp, server_header)."""
    req = f"OPTIONS rtsp://{ip}:{port}/ RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: Decompress\r\n\r\n".encode()
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(req)
            resp = s.recv(4096).decode(errors="replace")
    except OSError:
        return False, ""
    if not resp.startswith("RTSP/"):
        return False, ""
    server = next((ln.split(":", 1)[1].strip() for ln in resp.splitlines() if ln.lower().startswith("server:")), "")
    return True, server
