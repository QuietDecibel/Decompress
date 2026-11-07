"""Find IP cameras on your own local network.

Two complementary methods:
  * ONVIF WS-Discovery (multicast probe) — standards-compliant cameras answer with their service address.
  * TCP probe of common camera ports on a *private* subnet, confirmed with an RTSP OPTIONS request.

Discovery never guesses passwords; credentials are supplied by the user for cameras they own.
"""
from .models import Camera
from .onvif import ws_discover
from .scanner import scan_subnet, local_subnet
from .rtsp import candidate_urls, rtsp_options

__all__ = ["Camera", "ws_discover", "scan_subnet", "local_subnet", "candidate_urls", "rtsp_options", "discover_all"]


def discover_all(subnet: str | None = None, timeout: float = 2.0) -> list[Camera]:
    """Run both discovery methods and merge results by IP."""
    found: dict[str, Camera] = {}
    for cam in ws_discover(timeout=timeout):
        found[cam.ip] = cam
    for cam in scan_subnet(subnet or local_subnet(), timeout=min(timeout, 0.5)):
        if cam.ip in found:
            found[cam.ip].merge(cam)
        else:
            found[cam.ip] = cam
    return sorted(found.values(), key=lambda c: tuple(int(x) for x in c.ip.split(".")))
