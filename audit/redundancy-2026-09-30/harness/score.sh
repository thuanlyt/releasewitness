#!/usr/bin/env bash
# score.sh <workspace> <hidden-suite-dir> <label>
# Prints one JSON line: visible and hidden test results for the workspace's
# current working tree. Hidden suites are held out and never copied into a run.
ws="$1" hidden="$2" label="$3"
HR="$(cd "$(dirname "$0")" && pwd)/hidden_runner.py"
cd "$ws"
vis=$(python -m unittest discover -s tests 2>&1 | tail -3 | tr '\n' ' ')
hid_out=$(cd "$ws" && PYTHONPATH="$ws" python "$HR" "$hidden" 2>/dev/null)
python - "$label" "$vis" "$hid_out" "$(git rev-parse --short HEAD)" "$(git status --porcelain | grep -v '^?? work/' | wc -l)" <<'PY'
import json, re, sys
label, vis, hid, head, dirty = sys.argv[1:6]
try:
    tests = json.loads(hid.strip().splitlines()[-1])
except Exception:
    tests = {"RUNNER_ERROR": "ERROR"}
print(json.dumps({"label": label, "head": head, "dirty_nonwork_paths": int(dirty),
                  "visible": "OK" if re.search(r"\bOK\b", vis) else vis.strip()[-80:],
                  "hidden_pass": sum(v == "ok" for v in tests.values()), "hidden_total": len(tests),
                  "hidden_failed": sorted(k for k, v in tests.items() if v != "ok")}))
PY
