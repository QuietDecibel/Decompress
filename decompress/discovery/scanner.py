"""Probe common camera ports on a private subnet (your own LAN only)."""
from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor

from .models import Camera
from .rtsp import rtsp_options

CAMERA_PORTS = [554, 8554, 80, 8000, 8080, 443, 37777, 34567]
RTSP_PORTS = {554, 8554}
MAX_HOSTS = 1024


def local_subnet() -> str:
    """Best guess at this machine's LAN as a /24."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packets are sent for UDP connect
        ip = s.getsockname()[0]
    except OSError:
        ip = "192.168.1.1"
    finally:
        s.close()
    return str(ipaddress.ip_network(f"{ip}/24", strict=False))


def validate_subnet(subnet: str) -> ipaddress.IPv4Network:
    net = ipaddress.ip_network(subnet, strict=False)
    if not isinstance(net, ipaddress.IPv4Network) or not net.is_private:
        raise ValueError(f"{subnet} is not a private IPv4 network; only scan networks you own")
    if net.num_addresses > MAX_HOSTS:
        raise ValueError(f"{subnet} has {net.num_addresses} addresses; limit is {MAX_HOSTS}")
    return net


def _open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_server(ip: str, port: int, timeout: float) -> str:
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"HEAD / HTTP/1.0\r\nHost: {ip}\r\n\r\n".encode())
            resp = s.recv(2048).decode(errors="replace")
    except OSError:
        return ""
    return next((ln.split(":", 1)[1].strip() for ln in resp.splitlines() if ln.lower().startswith("server:")), "")


def probe_host(ip: str, ports=CAMERA_PORTS, timeout: float = 0.4, rtsp_ports=RTSP_PORTS) -> Camera | None:
    open_ports = [p for p in ports if _open(ip, p, timeout)]
    if not open_ports:
        return None
    cam = Camera(ip=ip, ports=open_ports, methods=["portscan"])
    for p in open_ports:
        if p in rtsp_ports and not cam.rtsp:
            cam.rtsp, cam.rtsp_server = rtsp_options(ip, p, timeout * 2)
        if p in (80, 8000, 8080) and not cam.http_server:
            cam.http_server = _http_server(ip, p, timeout * 2)
    # Only report hosts that look like cameras: an RTSP server or a camera-only port.
    if cam.rtsp or {37777, 34567} & set(open_ports) or cam.vendor_hint:
        return cam
    return None


def scan_subnet(subnet: str, timeout: float = 0.4, workers: int = 128) -> list[Camera]:
    net = validate_subnet(subnet)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = ex.map(lambda ip: probe_host(str(ip), timeout=timeout), net.hosts())
    return [c for c in results if c]
