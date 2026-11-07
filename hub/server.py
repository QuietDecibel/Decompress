"""Claude Progress Hub — a tiny, dependency-free dashboard server.

Any project (or any Claude Code session) can report its plan and progress here
over plain HTTP/JSON, and the dashboard at http://HOST:PORT/ shows a live
progress bar per project, per phase, and an activity feed.

Run:
    python hub/server.py                 # http://127.0.0.1:8765
    HUB_HOST=0.0.0.0 HUB_PORT=8765 HUB_TOKEN=secret python hub/server.py

API (JSON):
    GET    /api/projects                         list all projects (with computed progress)
    GET    /api/projects/{pid}                   one project
    PUT    /api/projects/{pid}                   create/replace a project (name, description, phases, tasks)
    PATCH  /api/projects/{pid}/tasks/{tid}       update one task: {"status": "done", "note": "..."}
    POST   /api/projects/{pid}/events            append activity: {"message": "...", "agent": "..."}
    DELETE /api/projects/{pid}                   remove a project

Task status is one of: todo, in_progress, blocked, done.
If HUB_TOKEN is set, write requests must send header  Authorization: Bearer <token>.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
DATA_FILE = Path(os.environ.get("HUB_DATA", HERE / "data" / "hub.json"))
TOKEN = os.environ.get("HUB_TOKEN", "")
STATUSES = ("todo", "in_progress", "blocked", "done")
MAX_EVENTS = 200
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_lock = threading.Lock()


def _load() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"projects": {}}


def _save(db: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=2))
    tmp.replace(DATA_FILE)


def compute_progress(project: dict) -> dict:
    """Attach progress numbers to a project (overall and per phase)."""
    tasks = project.get("tasks", [])

    def summarize(ts: list[dict]) -> dict:
        counts = {s: 0 for s in STATUSES}
        for t in ts:
            counts[t.get("status", "todo") if t.get("status") in STATUSES else "todo"] += 1
        total = len(ts)
        pct = round(100 * counts["done"] / total, 1) if total else 0.0
        return {"total": total, "counts": counts, "percent": pct}

    out = dict(project)
    out["progress"] = summarize(tasks)
    phases = []
    for ph in project.get("phases", []):
        p = dict(ph)
        p["progress"] = summarize([t for t in tasks if t.get("phase") == ph.get("id")])
        phases.append(p)
    out["phases"] = phases
    return out


def _normalize_project(pid: str, body: dict, old: dict | None) -> dict:
    tasks = []
    for t in body.get("tasks", []):
        if not isinstance(t, dict) or "id" not in t:
            raise ValueError("each task needs an 'id'")
        status = t.get("status", "todo")
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r} for task {t['id']}")
        tasks.append({
            "id": str(t["id"]),
            "title": str(t.get("title", t["id"])),
            "phase": t.get("phase"),
            "status": status,
            "note": str(t.get("note", "")),
        })
    return {
        "id": pid,
        "name": str(body.get("name", pid)),
        "description": str(body.get("description", "")),
        "repo": str(body.get("repo", "")),
        "agent": str(body.get("agent", "")),
        "phases": [
            {"id": str(p["id"]), "title": str(p.get("title", p["id"]))}
            for p in body.get("phases", []) if isinstance(p, dict) and "id" in p
        ],
        "tasks": tasks,
        "events": (old or {}).get("events", []),
        "updated": time.time(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeProgressHub/1.0"

    # ---- helpers -------------------------------------------------------
    def _send(self, code: int, payload, ctype="application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, PATCH, POST, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, msg: str) -> None:
        self._send(code, {"error": msg})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _authorized(self) -> bool:
        return not TOKEN or self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _route(self) -> list[str]:
        return [p for p in self.path.split("?")[0].split("/") if p]

    def log_message(self, fmt, *args):  # quieter logs
        if os.environ.get("HUB_VERBOSE"):
            super().log_message(fmt, *args)

    # ---- verbs ---------------------------------------------------------
    def do_OPTIONS(self):
        self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")

    def do_GET(self):
        parts = self._route()
        if not parts or parts == ["index.html"]:
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if parts == ["api", "health"]:
            return self._send(200, {"ok": True})
        if parts[:2] == ["api", "projects"]:
            with _lock:
                db = _load()
            if len(parts) == 2:
                projects = sorted(db["projects"].values(), key=lambda p: -p.get("updated", 0))
                return self._send(200, {"projects": [compute_progress(p) for p in projects]})
            if len(parts) == 3 and parts[2] in db["projects"]:
                return self._send(200, compute_progress(db["projects"][parts[2]]))
        self._error(404, "not found")

    def _write(self, fn):
        if not self._authorized():
            return self._error(401, "missing or bad bearer token")
        try:
            body = self._body()
            with _lock:
                db = _load()
                code, result = fn(db, body)
                if code < 300:
                    _save(db)
            self._send(code, result)
        except (ValueError, json.JSONDecodeError) as e:
            self._error(400, str(e))

    def do_PUT(self):
        parts = self._route()
        if len(parts) == 3 and parts[:2] == ["api", "projects"] and ID_RE.match(parts[2]):
            pid = parts[2]

            def put(db, body):
                db["projects"][pid] = _normalize_project(pid, body, db["projects"].get(pid))
                return 200, compute_progress(db["projects"][pid])
            return self._write(put)
        self._error(404, "not found")

    def do_PATCH(self):
        parts = self._route()
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "tasks":
            pid, tid = parts[2], parts[4]

            def patch(db, body):
                proj = db["projects"].get(pid)
                if not proj:
                    return 404, {"error": "unknown project"}
                task = next((t for t in proj["tasks"] if t["id"] == tid), None)
                if not task:
                    return 404, {"error": "unknown task"}
                if "status" in body:
                    if body["status"] not in STATUSES:
                        raise ValueError(f"status must be one of {STATUSES}")
                    task["status"] = body["status"]
                if "note" in body:
                    task["note"] = str(body["note"])
                if "title" in body:
                    task["title"] = str(body["title"])
                proj["updated"] = time.time()
                _add_event(proj, f"{tid} → {task['status']}" + (f": {task['note']}" if body.get("note") else ""),
                           body.get("agent", ""))
                return 200, task
            return self._write(patch)
        self._error(404, "not found")

    def do_POST(self):
        parts = self._route()
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "events":
            pid = parts[2]

            def post(db, body):
                proj = db["projects"].get(pid)
                if not proj:
                    return 404, {"error": "unknown project"}
                if not body.get("message"):
                    raise ValueError("'message' is required")
                proj["updated"] = time.time()
                return 201, _add_event(proj, str(body["message"]), str(body.get("agent", "")))
            return self._write(post)
        self._error(404, "not found")

    def do_DELETE(self):
        parts = self._route()
        if len(parts) == 3 and parts[:2] == ["api", "projects"]:
            def delete(db, _):
                if db["projects"].pop(parts[2], None) is None:
                    return 404, {"error": "unknown project"}
                return 200, {"deleted": parts[2]}
            return self._write(delete)
        self._error(404, "not found")


def _add_event(proj: dict, message: str, agent: str) -> dict:
    ev = {"t": time.time(), "message": message[:500], "agent": agent[:64]}
    proj.setdefault("events", []).append(ev)
    del proj["events"][:-MAX_EVENTS]
    return ev


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    host = os.environ.get("HUB_HOST", "127.0.0.1")
    port = int(os.environ.get("HUB_PORT", "8765"))
    srv = make_server(host, port)
    print(f"Claude Progress Hub on http://{host}:{port}  (data: {DATA_FILE})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
