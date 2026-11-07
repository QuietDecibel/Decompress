#!/usr/bin/env python3
"""Single-file, stdlib-only client for the Claude Progress Hub.

Copy this file into any project. Usage:

    python hub_client.py sync plan.json                 # push the whole plan (phases + tasks)
    python hub_client.py task <project> <task> done     # set a task status (todo|in_progress|blocked|done)
    python hub_client.py task <project> <task> in_progress --note "wiring RTSP"
    python hub_client.py log <project> "Finished discovery module"
    python hub_client.py show                           # print progress bars in the terminal

Environment:
    HUB_URL    default http://127.0.0.1:8765
    HUB_TOKEN  bearer token if the hub was started with one
    HUB_AGENT  name shown next to activity (e.g. "claude:decompress")
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:8765").rstrip("/")
HUB_TOKEN = os.environ.get("HUB_TOKEN", "")
HUB_AGENT = os.environ.get("HUB_AGENT", "")


def request(method: str, path: str, body: dict | None = None, timeout: float = 5.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HUB_URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if HUB_TOKEN:
        req.add_header("Authorization", f"Bearer {HUB_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"hub error {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot reach hub at {HUB_URL}: {e.reason}")


def sync_plan(plan: dict) -> dict:
    pid = plan["id"]
    body = {k: plan.get(k) for k in ("name", "description", "repo", "phases", "tasks") if k in plan}
    body["agent"] = HUB_AGENT
    return request("PUT", f"/api/projects/{pid}", body)


def set_task(project: str, task: str, status: str, note: str | None = None) -> dict:
    body = {"status": status, "agent": HUB_AGENT}
    if note is not None:
        body["note"] = note
    return request("PATCH", f"/api/projects/{project}/tasks/{task}", body)


def log(project: str, message: str) -> dict:
    return request("POST", f"/api/projects/{project}/events", {"message": message, "agent": HUB_AGENT})


def _bar(pct: float, width: int = 30) -> str:
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def show() -> None:
    for p in request("GET", "/api/projects")["projects"]:
        pr = p["progress"]
        print(f"\n{p['name']}  {_bar(pr['percent'])} {pr['percent']:5.1f}%  ({pr['counts']['done']}/{pr['total']})")
        for ph in p["phases"]:
            pp = ph["progress"]
            print(f"   {ph['title'][:34]:<34} {_bar(pp['percent'], 20)} {pp['percent']:5.1f}%")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync"); s.add_argument("plan")
    t = sub.add_parser("task"); t.add_argument("project"); t.add_argument("task")
    t.add_argument("status", choices=["todo", "in_progress", "blocked", "done"]); t.add_argument("--note")
    lg = sub.add_parser("log"); lg.add_argument("project"); lg.add_argument("message")
    sub.add_parser("show")
    a = ap.parse_args(argv)
    if a.cmd == "sync":
        with open(a.plan) as f:
            r = sync_plan(json.load(f))
        print(f"synced {r['id']}: {r['progress']['percent']}% done")
    elif a.cmd == "task":
        print(json.dumps(set_task(a.project, a.task, a.status, a.note)))
    elif a.cmd == "log":
        log(a.project, a.message); print("logged")
    elif a.cmd == "show":
        show()


if __name__ == "__main__":
    main(sys.argv[1:])
