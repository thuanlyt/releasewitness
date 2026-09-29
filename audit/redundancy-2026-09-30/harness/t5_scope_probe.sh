#!/usr/bin/env bash
# TEST 5 (deterministic): what RelWit's one-writer-per-scope control actually enforces.
source "$(dirname "$0")/lib.sh"; set +e
ws=$("$AUDIT/harness/make_ws.sh" t5det clean n1 | tail -1); cd "$ws"; R="$RELWIT_VENV/bin/relwit"
r() { echo "\$ relwit $*"; "$R" "$@" 2>&1 | sed 's/^/  /'; echo "  exit=${PIPESTATUS[0]}"; }
r agent register --id w1 --role worker --scope ledgerlite/money.py --scope tests/test_money.py
r agent register --id w2 --role worker --scope ledgerlite --scope tests
r task new --title X --level L1 --owner supervisor --scope ledgerlite/money.py --scope tests/test_money.py --acceptance a
r task new --title Y --level L1 --owner supervisor --scope ledgerlite --acceptance b
r task claim RW-0001 --agent w1
echo "== P1: overlapping writer claim (parent subtree of an active scope)"
r task claim RW-0002 --agent w2
echo "== P2: worker edits an OUT-OF-SCOPE file but declares only in-scope files"
echo "# sneaky" >> ledgerlite/export.py; echo "# ok" >> ledgerlite/money.py
r task report RW-0001 --agent w1 --result completed --summary s --next-action review --file ledgerlite/money.py --check "unittest: pass"
echo "== P3: same, but the worker honestly declares the out-of-scope file"
r task new --title Z --level L1 --owner supervisor --scope tests/test_money.py --acceptance c
r task claim RW-0003 --agent w1
r task report RW-0003 --agent w1 --result completed --summary s --next-action review --file ledgerlite/export.py --check "x"
echo "== P4: identity is self-declared: the worker records review evidence as 'reviewer'"
r task evidence RW-0001 --kind review --agent reviewer --value "looks fine"
r task update RW-0001 --status needs_review --agent reviewer
r task update RW-0001 --status done --agent reviewer
echo "== git diff --stat vs declared files"; git diff --stat
