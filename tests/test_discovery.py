import socket
import threading

import pytest

from decompress.discovery import candidate_urls, rtsp_options
from decompress.discovery.models import Camera
from decompress.discovery.onvif import parse_probe_match
from decompress.discovery.scanner import probe_host, validate_subnet

PROBE_MATCH = """<?xml version="1.0"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><SOAP-ENV:Body><d:ProbeMatches><d:ProbeMatch>
 <d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/Front%20Door onvif://www.onvif.org/hardware/HIKVISION-DS2CD</d:Scopes>
 <d:XAddrs>http://192.168.1.64/onvif/device_service http://[fe80::1]/onvif/device_service</d:XAddrs>
 </d:ProbeMatch></d:ProbeMatches></SOAP-ENV:Body></SOAP-ENV:Envelope>"""


def test_parse_probe_match():
    cam = parse_probe_match(PROBE_MATCH, "192.168.1.200")
    assert cam.ip == "192.168.1.64"
    assert cam.name == "Front Door"
    assert cam.vendor_hint == "hikvision"
    assert 80 in cam.ports and cam.methods == ["onvif"]
    assert parse_probe_match("<hello/>", "1.2.3.4") is None


def test_validate_subnet_only_private_and_small():
    assert validate_subnet("192.168.1.0/24").num_addresses == 256
    with pytest.raises(ValueError):
        validate_subnet("8.8.8.0/24")
    with pytest.raises(ValueError):
        validate_subnet("10.0.0.0/8")


def test_candidate_urls_encode_credentials():
    urls = candidate_urls("10.0.0.5", 554, "reolink", "admin", "p@ss:word")
    assert urls[0] == "rtsp://admin:p%40ss%3Aword@10.0.0.5:554/h264Preview_01_main"
    assert len(urls) == len(set(urls))


def test_merge():
    a = Camera(ip="10.0.0.2", ports=[80], methods=["onvif"])
    a.merge(Camera(ip="10.0.0.2", ports=[554, 80], methods=["portscan"], rtsp=True))
    assert a.ports == [80, 554] and a.methods == ["onvif", "portscan"] and a.rtsp


@pytest.fixture
def fake_rtsp():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            with c:
                if c.recv(1024).startswith(b"OPTIONS"):
                    c.sendall(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nServer: Hikvision-Webs\r\nPublic: DESCRIBE\r\n\r\n")
    threading.Thread(target=serve, daemon=True).start()
    yield srv.getsockname()[1]
    srv.close()


def test_rtsp_probe(fake_rtsp):
    assert rtsp_options("127.0.0.1", fake_rtsp) == (True, "Hikvision-Webs")
    cam = probe_host("127.0.0.1", ports=[fake_rtsp], rtsp_ports={fake_rtsp})
    assert cam and cam.rtsp and cam.vendor_hint == "hikvision"


def test_closed_port_not_camera():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    assert rtsp_options("127.0.0.1", port, timeout=0.3) == (False, "")
    assert probe_host("127.0.0.1", ports=[port], timeout=0.2) is None
