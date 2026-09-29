"""Held-out TEST-5 acceptance for four concurrent feature tasks X, Y, Z, W."""
import csv, io, json, os, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from ledgerlite.money import parse_amount
from ledgerlite.invoice import Invoice, LineItem
from ledgerlite import export


class Concurrent(unittest.TestCase):
    def test_X_to_json(self):
        inv = Invoice([LineItem("A", 2, "10.00", 10)], "0.1")
        data = json.loads(export.to_json(inv))
        self.assertEqual(data["subtotal"], "20.00")
        self.assertEqual(data["discount"], "2.00")
        self.assertEqual(data["tax"], "1.80")
        self.assertEqual(data["total"], "19.80")
        self.assertEqual(data["lines"][0], {"description": "A", "quantity": 2, "unit_price": "10.00", "discount_pct": "10", "net": "18.00"})

    def test_Y_parse_accounting(self):
        self.assertEqual(parse_amount("(5.00)"), Decimal("-5.00"))
        self.assertEqual(parse_amount("$1,234.50"), Decimal("1234.50"))
        self.assertEqual(parse_amount("($1,234.50)"), Decimal("-1234.50"))
        with self.assertRaises(ValueError):
            parse_amount("(5.00")

    def test_Z_cli_json(self):
        from ledgerlite.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "i.json")
            with open(p, "w", encoding="utf-8") as h:
                json.dump({"tax_rate": "0", "lines": [{"description": "A", "quantity": 1, "unit_price": "5"}]}, h)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main([p, "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue())["total"], "5.00")

    def test_W_line_count(self):
        inv = Invoice([LineItem("A", 1, "1"), LineItem("B", 1, "2")])
        self.assertEqual(inv.line_count, 2)
        rows = list(csv.reader(io.StringIO(export.to_csv(inv))))
        self.assertEqual(rows[-1], ["TOTAL", "2", "", "", "3.00"])

    def test_regressions(self):
        inv = Invoice([LineItem("A", 3, "19.99", 5), LineItem("B", 2, "0.50")], "0.08")
        self.assertEqual(inv.total(), Decimal("62.61"))
        self.assertEqual(export.to_csv(Invoice([LineItem("Widget", 2, "10.00")])).splitlines()[1], "Widget,2,10.00,0,20.00")
