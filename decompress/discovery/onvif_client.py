"""Minimal ONVIF client: stream URLs and PTZ for cameras you own, using your own credentials.

Uses WS-Security UsernameToken (PasswordDigest), which is what ONVIF Profile S cameras expect.
Only the handful of calls Decompress needs are implemented:
  GetCapabilities -> media / PTZ service addresses
  GetProfiles     -> profile tokens (+ whether a profile has PTZ)
  GetStreamUri    -> RTSP URL for a profile
  ContinuousMove / Stop (PTZ)
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
}
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
DIGEST = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
B64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"


class OnvifError(Exception):
    pass


@dataclass
class Profile:
    token: str
    name: str = ""
    width: int = 0
    height: int = 0
    ptz: bool = False
    stream_uri: str = ""


@dataclass
class OnvifCamera:
    device_url: str
    media_url: str = ""
    ptz_url: str = ""
    profiles: list[Profile] = field(default_factory=list)

    def best_profile(self) -> Profile | None:
        with_uri = [p for p in self.profiles if p.stream_uri]
        return max(with_uri, key=lambda p: p.width * p.height, default=None)


def password_digest(password: str, nonce: bytes, created: str) -> str:
    return base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()


def security_header(user: str, password: str, nonce: bytes | None = None, created: str | None = None) -> str:
    if not user:
        return ""
    nonce = nonce or os.urandom(16)
    created = created or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return (f'<wsse:Security xmlns:wsse="{WSSE}" xmlns:wsu="{WSU}" s:mustUnderstand="1"><wsse:UsernameToken>'
            f"<wsse:Username>{_x(user)}</wsse:Username>"
            f'<wsse:Password Type="{DIGEST}">{password_digest(password, nonce, created)}</wsse:Password>'
            f'<wsse:Nonce EncodingType="{B64}">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
            f"<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>")


def _x(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def soap_call(url: str, body: str, user: str = "", password: str = "", timeout: float = 5.0) -> ET.Element:
    env = (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{NS["s"]}" xmlns:tds="{NS["tds"]}" '
           f'xmlns:trt="{NS["trt"]}" xmlns:tt="{NS["tt"]}" xmlns:tptz="{NS["tptz"]}">'
           f"<s:Header>{security_header(user, password)}</s:Header><s:Body>{body}</s:Body></s:Envelope>")
    req = urllib.request.Request(url, data=env.encode(), method="POST",
                                 headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        fault = re.search(rb"<[^>]*Text[^>]*>(.*?)</", raw, re.S)
        reason = fault.group(1).decode(errors="replace") if fault else e.reason
        if e.code in (400, 401, 403, 500) and re.search(rb"NotAuthorized|Sender|auth", raw, re.I):
            raise OnvifError(f"camera rejected the credentials ({reason})") from None
        raise OnvifError(f"HTTP {e.code}: {reason}") from None
    except (urllib.error.URLError, OSError) as e:
        raise OnvifError(f"cannot reach {url}: {getattr(e, 'reason', e)}") from None
    root = ET.fromstring(raw)
    body_el = root.find("s:Body", NS)
    if body_el is None:
        raise OnvifError("not a SOAP response")
    fault = body_el.find("s:Fault", NS)
    if fault is not None:
        text = "".join(fault.itertext()).strip()
        raise OnvifError(("camera rejected the credentials: " if "NotAuthorized" in text else "") + text[:200])
    return body_el


def _same_host(url: str, device_url: str) -> str:
    """Cameras behind NAT often advertise internal addresses; keep the host we actually reached."""
    u, d = urlsplit(url), urlsplit(device_url)
    if u.hostname and d.hostname and u.hostname != d.hostname:
        netloc = d.hostname + (f":{u.port}" if u.port else (f":{d.port}" if d.port else ""))
        return urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))
    return url


def with_credentials(uri: str, user: str, password: str) -> str:
    if not user or "@" in urlsplit(uri).netloc:
        return uri
    p = urlsplit(uri)
    return urlunsplit((p.scheme, f"{quote(user, safe='')}:{quote(password, safe='')}@{p.netloc}", p.path, p.query, p.fragment))


def connect(device_url: str, user: str = "", password: str = "", timeout: float = 5.0) -> OnvifCamera:
    """Query a camera and return its profiles with RTSP stream URLs (credentials not embedded)."""
    cam = OnvifCamera(device_url=device_url)
    caps = soap_call(device_url, '<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>',
                     user, password, timeout)
    media = caps.find(".//tt:Media/tt:XAddr", NS)
    ptz = caps.find(".//tt:PTZ/tt:XAddr", NS)
    cam.media_url = _same_host(media.text.strip(), device_url) if media is not None and media.text else device_url
    cam.ptz_url = _same_host(ptz.text.strip(), device_url) if ptz is not None and ptz.text else ""

    profiles = soap_call(cam.media_url, "<trt:GetProfiles/>", user, password, timeout)
    for p in profiles.findall(".//trt:Profiles", NS):
        res = p.find(".//tt:VideoEncoderConfiguration/tt:Resolution", NS)
        w = int(res.findtext("tt:Width", "0", NS)) if res is not None else 0
        h = int(res.findtext("tt:Height", "0", NS)) if res is not None else 0
        cam.profiles.append(Profile(token=p.get("token", ""), name=p.findtext("tt:Name", "", NS), width=w, height=h,
                                    ptz=p.find("tt:PTZConfiguration", NS) is not None))
    for prof in cam.profiles:
        body = ("<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport>"
                f"<tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{_x(prof.token)}"
                "</trt:ProfileToken></trt:GetStreamUri>")
        try:
            uri = soap_call(cam.media_url, body, user, password, timeout).findtext(".//tt:Uri", "", NS).strip()
            prof.stream_uri = _same_host(uri, device_url) if uri else ""
        except OnvifError:
            continue
    if not cam.profiles:
        raise OnvifError("camera returned no media profiles")
    return cam


def ptz_move(ptz_url: str, profile: str, pan: float, tilt: float, zoom: float, user: str = "", password: str = "") -> None:
    clamp = lambda v: max(-1.0, min(1.0, float(v)))  # noqa: E731
    body = (f"<tptz:ContinuousMove><tptz:ProfileToken>{_x(profile)}</tptz:ProfileToken><tptz:Velocity>"
            f'<tt:PanTilt x="{clamp(pan)}" y="{clamp(tilt)}"/><tt:Zoom x="{clamp(zoom)}"/>'
            "</tptz:Velocity></tptz:ContinuousMove>")
    soap_call(ptz_url, body, user, password)


def ptz_stop(ptz_url: str, profile: str, user: str = "", password: str = "") -> None:
    soap_call(ptz_url, f"<tptz:Stop><tptz:ProfileToken>{_x(profile)}</tptz:ProfileToken>"
                       "<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop>", user, password)
