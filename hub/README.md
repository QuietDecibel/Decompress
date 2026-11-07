# Claude Progress Hub

A dependency-free (Python stdlib) dashboard that any project, or any Claude Code session, can report its
plan and progress to. It shows a live progress bar per project and per phase, task lists, and an activity feed.

```bash
python hub/server.py                                   # http://127.0.0.1:8765
```

```bash
HUB_HOST=0.0.0.0 HUB_TOKEN=change-me python hub/server.py   # share on your network, writes need the token
```

## Connect another project

1. Copy `hub_client.py` into that project (stdlib only).
2. Describe the plan in a `plan.json`:
   ```json
   {"id": "my-proj", "name": "My Project",
    "phases": [{"id": "P1", "title": "Setup"}],
    "tasks": [{"id": "P1.1", "phase": "P1", "title": "Scaffold", "status": "done"}]}
   ```
3. Push it and keep it updated:
   ```bash
   export HUB_URL=http://<hub-host>:8765 HUB_TOKEN=change-me HUB_AGENT=claude:my-proj
   python hub_client.py sync plan.json
   python hub_client.py task my-proj P1.1 done
   python hub_client.py log my-proj "Finished scaffolding"
   ```

### Ask Claude sessions to report automatically

Add this to the project's `CLAUDE.md`:

```markdown
## Progress reporting
This project reports to the Claude Progress Hub. When you start a task from plan.json, run
`python hub_client.py task <project-id> <task-id> in_progress`; when you finish it, set it to `done`,
update plan.json to match, and log a one-line summary with `python hub_client.py log <project-id> "<summary>"`.
If the hub is unreachable, carry on and just update plan.json.
```

Cloud Claude sessions can reach the hub only at a public URL (for example a small VM or a tunnel), and
that host must be allowed in the session's network settings. Always set `HUB_TOKEN` when the hub is exposed.

## API

| Method | Path | Body |
|---|---|---|
| GET | `/api/projects` | — |
| GET | `/api/projects/{id}` | — |
| PUT | `/api/projects/{id}` | `{name, description, repo, phases[], tasks[]}` |
| PATCH | `/api/projects/{id}/tasks/{taskId}` | `{status, note?}` (status: `todo`, `in_progress`, `blocked`, `done`) |
| POST | `/api/projects/{id}/events` | `{message, agent?}` |
| DELETE | `/api/projects/{id}` | — |
