#!/usr/bin/env bash
# t4_drift.sh <workspace>: a teammate commits a regression after QA passed.
# ROUND_HALF_UP -> ROUND_DOWN breaks tests/test_money.py::test_round_simple.
ws="$1"; cd "$ws"
sed -i 's/rounding=ROUND_HALF_UP)/rounding=ROUND_DOWN)/; s/from decimal import ROUND_HALF_UP, Decimal/from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal/' ledgerlite/money.py
git commit -qam "Simplify money rounding"
python -m unittest discover -s tests >/dev/null 2>&1 && echo "UNEXPECTED: tests still pass" || echo "drift committed: visible tests now fail"
