"""build_matrix.py: aggregate every real run into evidence/run_metrics.json + a Markdown table.

Re-scores each workspace's full commit history with the final (corrected) hidden
suites so every number in the report comes from one scorer. Cost figures are the
Claude Code client-side estimates from the runtime's own `result` record.

Usage: RW_REPO=... RW_SCRATCH=... python build_matrix.py
"""

import json
import os
import subprocess
from pathlib import Path

REPO = Path(os.environ["RW_REPO"])
SCRATCH = Path(os.environ["RW_SCRATCH"])
AUDIT = REPO / "audit/redundancy-2026-09-30"
RAW = AUDIT / "evidence/raw"
RUNS = SCRATCH / "runs"
HIDDEN = SCRATCH / "hidden"

# workspace -> (hidden suite, session transcripts that ran in it)
WORKSPACES = {
    "t1-n0": ("t1", ["t1-n0"]), "t1-n1": ("t1", ["t1-n1"]), "t1-n2": ("t1", ["t1-n2"]),
    "t2-n0": ("t2", ["t2-n0"]), "t2-n1": ("t2", ["t2-n1"]), "t2-n2": ("t2", ["t2-n2"]),
    "t3-n0": ("t1", ["t3-n0", "t3-n0-resume"]), "t3-n0r": ("t1", ["t3-n0r", "t3-n0r-resume"]),
    "t3-n1": ("t1", ["t3-n1", "t3-n1-resume"]), "t3-n2": ("t1", ["t3-n2", "t3-n2-resume"]),
    "t3e-n0": ("t1", ["t3e-n0", "t3e-n0-resume"]), "t3e-n1": ("t1", ["t3e-n1", "t3e-n1-resume"]),
    "t3e-n2": ("t1", ["t3e-n2", "t3e-n2-resume"]),
    "t5-n0": ("t5", ["t5-n0"]), "t5-n1": ("t5", ["t5-n1"]), "t5-n2": ("t5", ["t5-n2"]),
    "t6-n0": ("t1", ["t6-n0-A", "t6-n0-B", "t6-n0-C", "t6-n0-Cclone"]),
    "t6-n1": ("t1", ["t6-n1-A", "t6-n1-B", "t6-n1-C", "t6-n1-Cclone"]),
    "t6-n2": ("t1", ["t6-n2-A", "t6-n2-B", "t6-n2-C", "t6-n2-Cclone"]),
    "t6b-n0": ("t1", ["t6b-n0-A", "t6b-n0-B", "t6b-n0-C", "t6b-n0-Cclone"]),
    "t6b-n1": ("t1", ["t6b-n1-A", "t6b-n1-B", "t6b-n1-C", "t6b-n1-Cclone"]),
    "t6b-n2": ("t1", ["t6b-n2-A", "t6b-n2-B", "t6b-n2-C", "t6b-n2-Cclone"]),
    "t4-n0": (None, ["t4-n0-qa"]), "t4-n1": (None, ["t4-n1-qa"]), "t4-n2": (None, ["t4-n2-qa"]),
    "t4-n0-d1": (None, ["t4-n0-d1"]), "t4-n0-d2": (None, ["t4-n0-d2"]),
    "t4-n1-d1": (None, ["t4-n1-d1"]), "t4-n1-d2": (None, ["t4-n1-d2"]),
    "t4-n2-d1": (None, ["t4-n2-d1"]), "t4-n2-d2": (None, ["t4-n2-d2"]),
}


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True).stdout


def session_metrics(name):
    path = RAW / f"{name}.summary.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    wall = RAW / f"{name}.wall"
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    return {
        "session": name, "subtype": data.get("subtype"),
        "wall_s": int(wall.read_text().strip()) if wall.exists() else None,
        "cost_usd_est": data.get("cost_usd_client_estimate"),
        "turns": data.get("num_turns"), "top_level_tool_calls": data.get("top_level_tool_calls"),
        "subagents_spawned": (data.get("subagent_stats") or {}).get("spawned"),
        "models": sorted(models), "output_tokens": {m: v.get("outputTokens") for m, v in models.items()},
        "isolation_worktree_calls": sum(1 for a in data.get("agent_calls", []) if a.get("isolation") == "worktree"),
    }


def coordination_artifacts(ws):
    files = [p for d in ("work", "knowledge") for p in (ws / d).rglob("*") if p.is_file()] if ws.exists() else []
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def main():
    out = {}
    for ws_name, (suite, sessions) in WORKSPACES.items():
        ws = RUNS / ws_name
        entry = {"workspace": ws_name, "sessions": [m for m in (session_metrics(s) for s in sessions) if m]}
        if suite and ws.exists():
            lines = run("bash", str(AUDIT / "harness/score_history.sh"), str(ws), str(HIDDEN / suite), ws_name)
            entry["scores"] = [json.loads(l) for l in lines.splitlines() if l.startswith("{")]
            (RAW / f"{ws_name}.scores.final.jsonl").write_text(lines)
        entry["coordination_artifacts"] = coordination_artifacts(ws)
        entry["commits"] = run("git", "-C", str(ws), "log", "--format=%h %s | %(trailers:key=Co-Authored-By,valueonly,separator=;)").splitlines() if ws.exists() else []
        out[ws_name] = entry
    (AUDIT / "evidence/run_metrics.json").write_text(json.dumps(out, indent=1))
    rows = ["| workspace | sessions | wall s | cost $ (est) | tool calls | subagents | models | hidden: first work commit -> final | coord files |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, e in out.items():
        s = e["sessions"]
        wall = sum(x["wall_s"] or 0 for x in s)
        cost = sum(x["cost_usd_est"] or 0 for x in s)
        tools = sum(x["top_level_tool_calls"] or 0 for x in s)
        subs = sum(x["subagents_spawned"] or 0 for x in s)
        models = sorted({m for x in s for m in x["models"]})
        sc = e.get("scores") or []
        work = [x for x in sc if x["label"].split("@")[1] != "worktree"][1:2]
        hidden = (f"{work[0]['hidden_pass']}/{work[0]['hidden_total']} -> " if work else "") + (f"{sc[-1]['hidden_pass']}/{sc[-1]['hidden_total']}" if sc else "n/a")
        rows.append(f"| {name} | {len(s)} | {wall} | {cost:.2f} | {tools} | {subs} | {', '.join(m.replace('claude-', '') for m in models)} | {hidden} | {e['coordination_artifacts']['files']} |")
    (AUDIT / "evidence/run_metrics.md").write_text("\n".join(rows) + "\n")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
