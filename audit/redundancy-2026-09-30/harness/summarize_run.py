"""summarize_run.py <stream.jsonl> -> one JSON line of authoritative run metrics.

Reads a Claude Code `--output-format stream-json --verbose` transcript. Model
names, tokens and cost come only from the runtime's own `result` record; if the
run was killed before emitting it, those fields are reported as unavailable.
"""
import json, sys
from collections import Counter

path = sys.argv[1]
tools, agent_calls, result, segments = Counter(), [], None, []
for raw in open(path, encoding="utf-8", errors="replace"):
    raw = raw.strip()
    if not raw.startswith("{"):
        continue
    try:
        event = json.loads(raw)
    except ValueError:
        continue
    if event.get("type") == "assistant":
        for block in event.get("message", {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tools[block.get("name")] += 1
                if block.get("name") in ("Agent", "Task"):
                    inp = block.get("input", {})
                    agent_calls.append({k: inp.get(k) for k in ("subagent_type", "model", "isolation", "run_in_background", "description")})
    if event.get("type") == "result":
        result = event  # modelUsage/cost are cumulative; turns/duration are per segment
        segments.append((event.get("num_turns") or 0, event.get("duration_ms") or 0))
out = {"transcript": path.split("/")[-1], "top_level_tool_calls": sum(tools.values()), "tools": dict(tools), "agent_calls": agent_calls}
if result:
    out.update({
        "subtype": result.get("subtype"), "is_error": result.get("is_error"), "num_turns": sum(s[0] for s in segments), "result_segments": len(segments),
        "duration_ms": sum(s[1] for s in segments), "cost_usd_client_estimate": result.get("total_cost_usd"),
        "models": {m: {k: v.get(k) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")} for m, v in (result.get("modelUsage") or {}).items()},
        "subagent_stats": {k: (result.get("subagent_stats") or {}).get(k) for k in ("spawned", "completed", "failed", "killed")},
        "session_id": result.get("session_id"),
    })
else:
    out.update({"subtype": "NO_RESULT_RECORD (killed/interrupted)", "models": "unavailable"})
print(json.dumps(out))
