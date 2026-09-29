"""TEST 4 (deterministic): source A -> QA PASS -> drift to B -> release decision.

For each drift scenario, a fresh workspace is created, QA is recorded both by
RelWit (`relwit supervisor qa`) and by the native stamp script, the drift is
applied, and each mechanism is asked whether the recorded QA still applies to
the current release source.

Usage: RW_REPO=... RW_SCRATCH=... python t4_stale_qa_matrix.py > results.json
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(os.environ["RW_REPO"])
SCRATCH = Path(os.environ["RW_SCRATCH"])
HARNESS = REPO / "audit/redundancy-2026-09-30/harness"
RELWIT = SCRATCH / "relwit-venv/bin/relwit"
NATIVE = HARNESS / "native_qa_stamp.sh"


def sh(ws, *args, check=True, env=None):
    result = subprocess.run(args, cwd=ws, capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise RuntimeError(f"{args}: {result.stdout}{result.stderr}")
    return result


def append(ws, rel, text):
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def s1(ws):
    append(ws, "ledgerlite/money.py", "\n# drift\n")
    sh(ws, "git", "commit", "-qam", "drift")


def s2(ws):
    append(ws, "ledgerlite/money.py", "\n# drift\n")


def s3(ws):
    append(ws, "ledgerlite/extra.py", "VALUE = 1\n")


def s4(ws):
    cfg = json.loads((ws / "relwit.config.json").read_text())
    cfg["supervisor"]["qa_commands"][0]["argv"] = ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_money.py"]
    (ws / "relwit.config.json").write_text(json.dumps(cfg, indent=2))
    sh(ws, "git", "commit", "-qam", "narrow QA")
    os.environ["QA_CMD_OVERRIDE"] = "python -m unittest discover -s tests -p test_money.py"


def s5(ws):
    original = (ws / "ledgerlite/money.py").read_text()
    append(ws, "ledgerlite/money.py", "\n# drift\n")
    (ws / "ledgerlite/money.py").write_text(original)


def s6(ws):
    append(ws, "ledgerlite/__pycache__/scratch.pyc", "x")


def s7(ws):
    sh(ws, "git", "commit", "-q", "--amend", "-m", "baseline (reworded)")


def s8(ws):
    append(ws, "work/checkpoints/note.md", "note\n")


def s9(ws):
    append(ws, "ledgerlite/_local.py", "RATE_OVERRIDE = '0.5'\n")


def s11(ws):
    sh(ws, "git", "commit", "-qam", "commit exactly the QA'd dirty content")


def s12(ws):
    path = ws / "tests/test_money.py"
    text = path.read_text()
    path.write_text(re.sub(r"    def test_format_positive.*?\n\n\n", "\n\n", text, flags=re.S))
    sh(ws, "git", "commit", "-qam", "drop a test")


# (id, description, mutation, truth: does the release source differ from what QA verified?, setup)
SCENARIOS = [
    ("S1", "new commit changes source after QA", s1, "STALE", None),
    ("S2", "uncommitted edit to tracked source", s2, "STALE", None),
    ("S3", "new non-ignored untracked source file", s3, "STALE", None),
    ("S4", "QA command narrowed after QA", s4, "STALE", None),
    ("S5", "edit then exact revert (content identical)", s5, "VALID", None),
    ("S6", "Git-ignored build artifact changes", s6, "VALID", None),
    ("S7", "amend commit message only (identical tree)", s7, "VALID", None),
    ("S8", "RelWit volatile path (work/) changes", s8, "VALID", None),
    ("S9", "Git-ignored file that is real source changes", s9, "STALE", "ignore_local"),
    ("S11", "QA ran on dirty tree, same content then committed", s11, "VALID", "dirty_before_qa"),
    ("S12", "test weakened after QA", s12, "STALE", None),
]


def relwit_state(ws):
    sh(ws, str(RELWIT), "supervisor", "report")
    text = (ws / "work/SUPERVISOR_REPORT.md").read_text()
    gates = dict(re.findall(r"- \[.\] `(\w+)`: `([^`]+)`", text))
    return gates


def main():
    rows = []
    for sid, desc, mutate, truth, setup in SCENARIOS:
        name = f"t4det-{sid}"
        ws = Path(sh(HARNESS, "bash", str(HARNESS / "make_ws.sh"), name, "clean", "n1").stdout.strip().splitlines()[-1])
        if setup == "ignore_local":
            append(ws, ".gitignore", "ledgerlite/_local.py\n")
            append(ws, "ledgerlite/_local.py", "RATE_OVERRIDE = None\n")
            sh(ws, "git", "commit", "-qam", "ignore local override")
        if setup == "dirty_before_qa":
            append(ws, "ledgerlite/money.py", "\n# dirty during QA\n")
        os.environ.pop("QA_CMD_OVERRIDE", None)
        os.environ["NATIVE_EXCLUDE"] = "work"
        qa = sh(ws, str(RELWIT), "supervisor", "qa", check=False)
        native = sh(ws, "bash", str(NATIVE), "run", check=False)
        before = relwit_state(ws)
        mutate(ws)
        env = dict(os.environ)
        if "QA_CMD_OVERRIDE" in env:
            env["QA_CMD"] = env["QA_CMD_OVERRIDE"]
        after = relwit_state(ws)
        check = sh(ws, "bash", str(NATIVE), "check", check=False, env=env)
        rw = "VALID" if after.get("qa_source_state") == "pass" else "STALE"
        nat = "VALID" if check.returncode == 0 else "STALE"
        rows.append({
            "id": sid, "scenario": desc, "truth": truth,
            "relwit_qa_exit": qa.returncode, "relwit_before": before.get("qa_source_state"),
            "relwit_qa_source_state_after": after.get("qa_source_state"),
            "relwit_durability_after": after.get("release_source_durability"),
            "relwit_verdict": rw, "relwit_correct": rw == truth,
            "native_stamp_exit": native.returncode, "native_check_exit": check.returncode,
            "native_verdict": nat, "native_correct": nat == truth,
        })
    json.dump(rows, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
