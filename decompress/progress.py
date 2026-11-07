"""Best-effort reporting to the Claude Progress Hub (never raises; silent if the hub is down)."""
from __future__ import annotations

import json
import os
import urllib.request

HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:8765").rstrip("/")


def _send(method: str, path: str, body: dict) -> bool:
    req = urllib.request.Request(HUB_URL + path, data=json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json"})
    if os.environ.get("HUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['HUB_TOKEN']}")
    try:
        urllib.request.urlopen(req, timeout=2).close()
        return True
    except Exception:
        return False


def log(project: str, message: str) -> bool:
    return _send("POST", f"/api/projects/{project}/events", {"message": message, "agent": "decompress-app"})


def set_task(project: str, task: str, status: str, note: str = "") -> bool:
    return _send("PATCH", f"/api/projects/{project}/tasks/{task}", {"status": status, "note": note})
