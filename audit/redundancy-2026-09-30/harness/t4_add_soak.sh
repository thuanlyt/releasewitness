#!/usr/bin/env bash
# t4_add_soak.sh <workspace>: make QA genuinely expensive (~8 min) for TEST 4.
ws="$1"
mkdir -p "$ws/soak"
cat > "$ws/soak/soak.py" <<'PY'
"""Soak check: recompute many invoices for ~480 s and verify totals stay stable."""
import sys, time
from decimal import Decimal
sys.path.insert(0, ".")
from ledgerlite.invoice import Invoice, LineItem
deadline, n = time.monotonic() + 480, 0
while time.monotonic() < deadline:
    inv = Invoice([LineItem("A", 3, "19.99", 5), LineItem("B", 2, "0.50")], "0.08")
    assert inv.total() == Decimal("62.61"), inv.total()
    n += 1
print(f"soak ok: {n} invoices")
PY
cat > "$ws/qa.sh" <<'SH'
#!/usr/bin/env bash
# Full QA for ledgerlite: unit tests + ~8 minute soak check.
set -e
python -m unittest discover -s tests
python soak/soak.py
SH
chmod +x "$ws/qa.sh"
cat >> "$ws/README.md" <<'MD'
- Full QA (used for releases): `./qa.sh` — unit tests plus a soak check that takes about 8 minutes.
MD
if [[ -f "$ws/relwit.config.json" ]]; then
  python - "$ws" <<'PY'
import json, sys
p = f"{sys.argv[1]}/relwit.config.json"; c = json.load(open(p))
c["supervisor"]["qa_commands"] = [{"mode": "argv", "argv": ["bash", "qa.sh"]}]
c["supervisor"]["qa_timeout_seconds"] = 900
json.dump(c, open(p, "w"), indent=2)
PY
fi
( cd "$ws" && git add -A && git commit -qm "Add full QA script with soak check" )
