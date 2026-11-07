import json
import threading
import urllib.request

import pytest


@pytest.fixture
def hub(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("HUB_DATA", str(tmp_path / "hub.json"))
    monkeypatch.setenv("HUB_TOKEN", "s3cret")
    import server
    importlib.reload(server)
    srv = server.make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(base, method, path, body=None, token="s3cret"):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


PLAN = {"name": "Demo", "phases": [{"id": "P1", "title": "One"}, {"id": "P2", "title": "Two"}],
        "tasks": [{"id": "a", "phase": "P1", "status": "done"}, {"id": "b", "phase": "P1"},
                  {"id": "c", "phase": "P2", "status": "in_progress"}, {"id": "d", "phase": "P2"}]}


def test_put_and_progress(hub):
    code, p = call(hub, "PUT", "/api/projects/demo", PLAN)
    assert code == 200
    assert p["progress"]["percent"] == 25.0
    assert [ph["progress"]["percent"] for ph in p["phases"]] == [50.0, 0.0]
    assert p["progress"]["counts"]["in_progress"] == 1


def test_patch_task_and_events(hub):
    call(hub, "PUT", "/api/projects/demo", PLAN)
    code, t = call(hub, "PATCH", "/api/projects/demo/tasks/b", {"status": "done", "note": "shipped"})
    assert code == 200 and t["status"] == "done"
    code, _ = call(hub, "POST", "/api/projects/demo/events", {"message": "hello", "agent": "claude"})
    assert code == 201
    code, p = call(hub, "GET", "/api/projects/demo", token=None)
    assert p["phases"][0]["progress"]["percent"] == 100.0
    assert [e["message"] for e in p["events"]][-1] == "hello"
    # re-syncing the plan keeps the activity history
    call(hub, "PUT", "/api/projects/demo", PLAN)
    _, p = call(hub, "GET", "/api/projects/demo")
    assert len(p["events"]) == 2


def test_validation_and_auth(hub):
    assert call(hub, "PUT", "/api/projects/demo", PLAN, token="wrong")[0] == 401
    assert call(hub, "PUT", "/api/projects/demo", {"tasks": [{"id": "x", "status": "nope"}]})[0] == 400
    call(hub, "PUT", "/api/projects/demo", PLAN)
    assert call(hub, "PATCH", "/api/projects/demo/tasks/zzz", {"status": "done"})[0] == 404
    assert call(hub, "GET", "/api/projects", token=None)[1]["projects"][0]["id"] == "demo"


def test_dashboard_served(hub):
    with urllib.request.urlopen(hub + "/") as r:
        assert b"Claude Progress Hub" in r.read()
