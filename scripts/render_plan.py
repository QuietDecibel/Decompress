"""Regenerate PLAN.md from plan.json (the source of truth for the Progress Hub)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = {"done": "[x]", "in_progress": "[~]", "todo": "[ ]", "blocked": "[!]"}

plan = json.loads((ROOT / "plan.json").read_text())
done = sum(t["status"] == "done" for t in plan["tasks"])
lines = ["# Decompress — Project Plan", "",
         "Source of truth: [`plan.json`](plan.json). Push it to the Progress Hub with",
         "`decompress-hub-client sync plan.json`, and regenerate this file with `python scripts/render_plan.py`.",
         f"Overall: **{done}/{len(plan['tasks'])} done ({100 * done / len(plan['tasks']):.0f}%)**. "
         "Legend: `[x]` done · `[~]` in progress · `[ ]` to do.", ""]
for ph in plan["phases"]:
    ts = [t for t in plan["tasks"] if t["phase"] == ph["id"]]
    lines += [f"## {ph['id']} — {ph['title']}  ({sum(t['status'] == 'done' for t in ts)}/{len(ts)})", ""]
    lines += [f"- {ICON[t['status']]} **{t['id']}** {t['title']}" + (f" — _{t['note']}_" if t["note"] else "") for t in ts]
    lines.append("")
(ROOT / "PLAN.md").write_text("\n".join(lines))
print(f"PLAN.md: {done}/{len(plan['tasks'])} done")
