"""Inject the seven TEST-2 defects into a clean ledgerlite checkout.

Usage: python seed_defects.py <ledgerlite-root>

Each replacement must match exactly once, so the seeded state is reproducible.
Defects (all violate SPEC.md, none is caught by the visible tests):
  D1 round_money uses binary float + banker's rounding          (S2)
  D2 tax is charged on the pre-discount subtotal                 (S8)
  D3 CSV export joins fields without RFC 4180 quoting            (S9)
  D4 negative quantities are accepted                            (S4)
  D5 discount_pct above 100 is accepted                          (S4)
  D6 negative amounts render as "$-5.00" instead of "-$5.00"     (S3)
  D7 tax_rate above 1 is accepted                                (S6)
"""

import sys
from pathlib import Path

REPLACEMENTS = [
    ("ledgerlite/money.py",
     "    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)\n",
     "    return Decimal(str(round(float(value), 2)))\n"),
    ("ledgerlite/money.py",
     '    sign = "-" if amount < 0 else ""\n    symbol = SYMBOLS.get(currency, f"{currency} ")\n    return f"{sign}{symbol}{abs(amount):,.2f}"\n',
     '    symbol = SYMBOLS.get(currency, f"{currency} ")\n    return f"{symbol}{amount:,.2f}"\n'),
    ("ledgerlite/invoice.py",
     "        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:\n",
     "        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity == 0:\n"),
    ("ledgerlite/invoice.py",
     "        if discount < 0 or discount > 100:\n",
     "        if discount < 0:\n"),
    ("ledgerlite/invoice.py",
     "        if self.tax_rate < 0 or self.tax_rate > 1:\n",
     "        if self.tax_rate < 0:\n"),
    ("ledgerlite/invoice.py",
     "        return round_money(self.taxable() * self.tax_rate)\n",
     "        return round_money(self.subtotal() * self.tax_rate)\n"),
    ("ledgerlite/export.py",
     '    buffer = io.StringIO()\n    writer = csv.writer(buffer, lineterminator="\\n")\n    writer.writerow(HEADER)\n',
     '    buffer = io.StringIO()\n    writer = _PlainWriter(buffer)\n    writer.writerow(HEADER)\n'),
    ("ledgerlite/export.py",
     'HEADER = ["description", "quantity", "unit_price", "discount_pct", "net"]\n',
     'HEADER = ["description", "quantity", "unit_price", "discount_pct", "net"]\n\n\n'
     'class _PlainWriter:\n    def __init__(self, buffer):\n        self.buffer = buffer\n\n'
     '    def writerow(self, row):\n        self.buffer.write(",".join(str(cell) for cell in row) + "\\n")\n'),
]


def main(root):
    base = Path(root)
    for relative, old, new in REPLACEMENTS:
        path = base / relative
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"seed anchor not unique in {relative}: {old!r}")
        path.write_text(text.replace(old, new), encoding="utf-8")
    export = base / "ledgerlite/export.py"
    text = export.read_text(encoding="utf-8")
    export.write_text(text.replace("import csv\n", ""), encoding="utf-8")
    print("seeded 7 defects")


if __name__ == "__main__":
    main(sys.argv[1])
