from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Camera:
    ip: str
    ports: list[int] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)   # "onvif", "portscan"
    onvif_xaddrs: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    rtsp: bool = False
    rtsp_server: str = ""
    http_server: str = ""
    name: str = ""

    @property
    def vendor_hint(self) -> str:
        text = " ".join(self.scopes + [self.rtsp_server, self.http_server]).lower()
        for v in ("hikvision", "dahua", "axis", "reolink", "amcrest", "uniview", "foscam", "tp-link", "wyze", "ubiquiti"):
            if v in text:
                return v
        return ""

    def merge(self, other: "Camera") -> None:
        for attr in ("ports", "methods", "onvif_xaddrs", "scopes"):
            cur = getattr(self, attr)
            cur.extend(x for x in getattr(other, attr) if x not in cur)
        self.ports.sort()
        self.rtsp = self.rtsp or other.rtsp
        self.rtsp_server = self.rtsp_server or other.rtsp_server
        self.http_server = self.http_server or other.http_server
        self.name = self.name or other.name

    def to_dict(self) -> dict:
        d = asdict(self)
        d["vendor_hint"] = self.vendor_hint
        return d
