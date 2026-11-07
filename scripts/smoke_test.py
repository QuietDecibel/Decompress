"""Smoke-test an *installed* Decompress (run with the venv's python that has the wheel installed).

Checks the console entry points work and that packaged data files (web UI, hub dashboard,
Blender add-on) made it into the install — the things unit tests run from source can't catch.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

PY = sys.executable
OK = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global OK
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    OK += 0 if cond else 1


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", "decompress", *args], capture_output=True, text=True, timeout=120)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str, tries: int = 60) -> tuple[int, bytes]:
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.status, r.read()
        except Exception:
            time.sleep(0.5)
    return 0, b""


def serve(args: list[str], url: str, env: dict) -> tuple[int, bytes, subprocess.Popen]:
    p = subprocess.Popen([PY, "-m", "decompress", *args], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    status, body = get(url)
    return status, body, p


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="decompress-smoke-"))
    os.chdir(tmp)                                   # make sure nothing is imported from the source tree
    env = {**os.environ, "DECOMPRESS_HOME": str(tmp / "data"), "HUB_DATA": str(tmp / "hub.json")}
    print(f"Smoke test using {PY}")

    import decompress
    check("package imported from site-packages", "site-packages" in decompress.__file__, decompress.__file__)

    r = run("--version")
    check("decompress --version", r.returncode == 0 and r.stdout.startswith("decompress "), r.stderr)
    r = run("backends")
    check("decompress backends", r.returncode == 0 and "hog" in r.stdout, r.stderr)
    r = run("blender-addon", "--out", str(tmp / "addon.zip"))
    names = zipfile.ZipFile(tmp / "addon.zip").namelist() if r.returncode == 0 else []
    check("Blender add-on zip", names == ["decompress_motion/__init__.py"], r.stderr)

    port = free_port()
    status, body, p = serve(["hub", "--port", str(port)], f"http://127.0.0.1:{port}/", env)
    check("hub serves dashboard", status == 200 and b"Claude Progress Hub" in body, p.stderr and "")
    p.terminate(); p.wait(10)

    port = free_port()
    status, body, p = serve(["--backend", "hog", "serve", "--port", str(port)], f"http://127.0.0.1:{port}/", env)
    check("app serves web UI", status == 200 and b"Decompress" in body)
    status, body = get(f"http://127.0.0.1:{port}/api/blender/import_motion.py", tries=3)
    check("app serves Blender importer", status == 200 and b"bl_info" in body)
    status, _ = get(f"http://127.0.0.1:{port}/api/queue", tries=3)
    check("app API responds", status == 200)
    p.terminate(); p.wait(10)

    print("SMOKE TEST " + ("PASSED" if OK == 0 else f"FAILED ({OK} check(s))"))
    return 1 if OK else 0


if __name__ == "__main__":
    sys.exit(main())
