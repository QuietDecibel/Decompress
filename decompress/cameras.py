"""Saved cameras + secure credential storage.

Non-secret details (name, address, stream URL without credentials, ONVIF info) go in cameras.json.
Usernames/passwords go to the operating system's credential store via `keyring`
(Windows Credential Manager, macOS Keychain, Linux Secret Service). If no OS store is available,
they are encrypted with Fernet (AES-128 + HMAC) in secrets.bin, with the key in secret.key
(readable only by you on Linux/macOS; protected with DPAPI on Windows).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

SERVICE = "decompress-cameras"


# ---------------------------------------------------------------- secret stores
class KeyringStore:
    name = "os-keyring"

    def __init__(self):
        import keyring
        self.kr = keyring
        self.name = f"os-keyring ({type(keyring.get_keyring()).__name__})"   # make_secret_store probes it

    def get(self, key: str) -> dict | None:
        raw = self.kr.get_password(SERVICE, key)
        return json.loads(raw) if raw else None

    def set(self, key: str, value: dict) -> None:
        self.kr.set_password(SERVICE, key, json.dumps(value))

    def delete(self, key: str) -> None:
        try:
            self.kr.delete_password(SERVICE, key)
        except Exception:
            pass


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI (current user) via ctypes."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = BLOB(len(data), buf), BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


class FileStore:
    name = "encrypted-file"

    def __init__(self, root: Path):
        from cryptography.fernet import Fernet
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        key_file, self.data_file = self.root / "secret.key", self.root / "secrets.bin"
        if key_file.exists():
            raw = key_file.read_bytes()
            key = _dpapi(raw, False) if sys.platform == "win32" else raw
        else:
            key = Fernet.generate_key()
            key_file.write_bytes(_dpapi(key, True) if sys.platform == "win32" else key)
            if sys.platform != "win32":
                os.chmod(key_file, 0o600)
        self.f = Fernet(key)
        self.lock = threading.Lock()

    def _load(self) -> dict:
        if not self.data_file.exists():
            return {}
        return json.loads(self.f.decrypt(self.data_file.read_bytes()))

    def _save(self, d: dict) -> None:
        self.data_file.write_bytes(self.f.encrypt(json.dumps(d).encode()))
        if sys.platform != "win32":
            os.chmod(self.data_file, 0o600)

    def get(self, key):
        with self.lock:
            return self._load().get(key)

    def set(self, key, value):
        with self.lock:
            d = self._load(); d[key] = value; self._save(d)

    def delete(self, key):
        with self.lock:
            d = self._load(); d.pop(key, None); self._save(d)


def make_secret_store(root: Path, prefer_keyring: bool = True):
    if prefer_keyring and os.environ.get("DECOMPRESS_SECRETS", "auto") != "file":
        try:
            store = KeyringStore()
            store.set("__probe__", {"ok": True})        # make sure it really works (headless Linux often doesn't)
            store.delete("__probe__")
            return store
        except Exception:
            pass
    return FileStore(root)


# ---------------------------------------------------------------- saved cameras
@dataclass
class SavedCamera:
    name: str
    url: str = ""                       # stream URL WITHOUT credentials
    ip: str = ""
    onvif_url: str = ""                 # device service URL
    ptz_url: str = ""
    profile: str = ""                   # ONVIF profile token used for PTZ
    ptz: bool = False
    autostart: bool = True
    has_credentials: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class CameraRegistry:
    def __init__(self, root: Path, secrets=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = self.root / "cameras.json"
        self.secrets = secrets or make_secret_store(self.root)
        self.lock = threading.Lock()
        self.cameras: dict[str, SavedCamera] = {}
        if self.file.exists():
            for d in json.loads(self.file.read_text()):
                self.cameras[d["id"]] = SavedCamera(**d)

    def _persist(self) -> None:
        self.file.write_text(json.dumps([asdict(c) for c in self.cameras.values()], indent=1))

    def add(self, cam: SavedCamera, user: str = "", password: str = "") -> SavedCamera:
        with self.lock:
            if user:
                self.secrets.set(cam.id, {"user": user, "password": password})
                cam.has_credentials = True
            self.cameras[cam.id] = cam
            self._persist()
        return cam

    def remove(self, cid: str) -> bool:
        with self.lock:
            if self.cameras.pop(cid, None) is None:
                return False
            self.secrets.delete(cid)
            self._persist()
            return True

    def credentials(self, cid: str) -> tuple[str, str]:
        c = self.secrets.get(cid) or {}
        return c.get("user", ""), c.get("password", "")

    def stream_url(self, cid: str) -> str:
        from .discovery.onvif_client import with_credentials
        cam = self.cameras[cid]
        return with_credentials(cam.url, *self.credentials(cid))

    def list(self) -> list[dict]:
        return [asdict(c) for c in self.cameras.values()]
