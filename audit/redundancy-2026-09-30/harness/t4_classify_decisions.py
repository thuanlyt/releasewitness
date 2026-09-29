"""t4_classify_decisions.py <run>...: verdict and what each TEST-4 decider actually executed."""
import json, re, sys
for f in sys.argv[1:]:
    d = json.load(open(f + ".summary.json"))
    cmds = []
    for l in open(f + ".jsonl"):
        if '"tool_use"' not in l: continue
        e = json.loads(l)
        for b in e.get("message", {}).get("content", []) or []:
            if b.get("type") == "tool_use" and b["name"] == "Bash":
                cmds.append(b["input"]["command"])
    rs = [json.loads(l) for l in open(f + ".jsonl") if '"type":"result"' in l]
    text = rs[-1]["result"] if rs else ""
    verdict = "NOT READY" if re.search(r"NOT READY", text) else ("READY" if "READY" in text else "?")
    ran_qa_sh = any(re.search(r"(^|[;&|]\s*)(\./qa\.sh|bash qa\.sh|python3? soak/soak\.py)|supervisor qa", c, re.M) for c in cmds)
    ran_unit = any("unittest" in c for c in cmds)
    used_gate = any(re.search(r"supervisor report", c) for c in cmds)
    used_relwit = any(re.search(r"relwit(/cli\.py)?\s+(task|supervisor|context|validate)|relwit/cli\.py", c) for c in cmds)
    sha_diff = any(re.search(r"git diff[^\n]*[0-9a-f]{7,40}[^\n]*HEAD", c) for c in cmds)
    print(f"{f}: verdict={verdict} wall={open(f+'.wall').read().strip()}s cost={d['cost_usd_client_estimate']:.2f} bash={len(cmds)} ran_full_qa={ran_qa_sh} ran_unit={ran_unit} used_relwit={used_relwit} read_gate={used_gate} manual_sha_diff={sha_diff}")
    print("   first line:", text.strip().splitlines()[0][:200] if text else "NO RESULT")
