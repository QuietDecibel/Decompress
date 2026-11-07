"""ONVIF WS-Discovery: multicast a Probe and collect ProbeMatches."""
from __future__ import annotations

import re
import socket
import time
import uuid
from urllib.parse import urlparse, unquote

from .models import Camera

MCAST = ("239.255.255.250", 3702)

PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header>
  <w:MessageID>uuid:{msg_id}</w:MessageID>
  <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
  <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
 </e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>"""

_XADDRS = re.compile(r"<[^>]*XAddrs[^>]*>(.*?)</[^>]*XAddrs>", re.S)
_SCOPES = re.compile(r"<[^>]*Scopes[^>]*>(.*?)</[^>]*Scopes>", re.S)


def parse_probe_match(xml: str, sender_ip: str) -> Camera | None:
    """Turn a ProbeMatch SOAP response into a Camera (pure function, unit-tested)."""
    if "ProbeMatch" not in xml:
        return None
    xaddrs = [a for m in _XADDRS.findall(xml) for a in m.split()]
    scopes = [s for m in _SCOPES.findall(xml) for s in m.split()]
    ip = sender_ip
    for a in xaddrs:  # prefer the address the device itself advertises
        host = urlparse(a).hostname
        if host and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
            ip = host
            break
    name = ""
    for s in scopes:
        if "/name/" in s:
            name = unquote(s.rsplit("/name/", 1)[1])
    ports = sorted({urlparse(a).port or 80 for a in xaddrs})
    return Camera(ip=ip, ports=ports, methods=["onvif"], onvif_xaddrs=xaddrs, scopes=scopes, name=name)


def ws_discover(timeout: float = 2.0, iface_ip: str | None = None) -> list[Camera]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    if iface_ip:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
    sock.settimeout(0.3)
    found: dict[str, Camera] = {}
    try:
        sock.sendto(PROBE.format(msg_id=uuid.uuid4()).encode(), MCAST)
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, (ip, _) = sock.recvfrom(65535)
            except socket.timeout:
                continue
            cam = parse_probe_match(data.decode(errors="replace"), ip)
            if cam:
                if cam.ip in found:
                    found[cam.ip].merge(cam)
                else:
                    found[cam.ip] = cam
    except OSError:
        pass  # no multicast route (e.g. container) — just return what we have
    finally:
        sock.close()
    return list(found.values())
