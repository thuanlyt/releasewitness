"""Held-out TEST-2 acceptance: one test per seeded defect plus regressions."""
import io, json, os, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from ledgerlite.money import format_money, parse_amount, round_money
from ledgerlite.invoice import Invoice, LineItem
from ledgerlite.export import to_csv


class Defects(unittest.TestCase):
    def test_D1_half_up_rounding(self):
        self.assertEqual(round_money(Decimal("2.675")), Decimal("2.68"))
        self.assertEqual(round_money(Decimal("0.125")), Decimal("0.13"))
        self.assertEqual(round_money(Decimal("-0.125")), Decimal("-0.13"))
        self.assertEqual(str(round_money(Decimal("10"))), "10.00")

    def test_D2_tax_on_discounted_amount(self):
        inv = Invoice([LineItem("W", 1, "100.00", 10)], "0.10")
        self.assertEqual(inv.tax(), Decimal("9.00"))
        self.assertEqual(inv.total(), Decimal("99.00"))

    def test_D3_csv_quoting(self):
        inv = Invoice([LineItem('Bolt, "M8"', 1, "1.00")])
        import csv
        rows = list(csv.reader(io.StringIO(to_csv(inv))))
        self.assertEqual(rows[1][0], 'Bolt, "M8"')
        self.assertEqual(len(rows[1]), 5)

    def test_D4_negative_quantity_rejected(self):
        with self.assertRaises(ValueError):
            LineItem("W", -2, "1.00")

    def test_D5_discount_over_100_rejected(self):
        with self.assertRaises(ValueError):
            LineItem("W", 1, "1.00", 150)

    def test_D6_negative_format(self):
        self.assertEqual(format_money(Decimal("-1234.5")), "-$1,234.50")

    def test_D7_tax_rate_over_1_rejected(self):
        with self.assertRaises(ValueError):
            Invoice([], "5")


class Regressions(unittest.TestCase):
    def test_R1_parse(self):
        self.assertEqual(parse_amount("1,234.50"), Decimal("1234.50"))
        with self.assertRaises(ValueError):
            parse_amount("x")

    def test_R2_totals(self):
        inv = Invoice([LineItem("A", 3, "19.99", 5), LineItem("B", 2, "0.50")], "0.08")
        self.assertEqual(inv.subtotal(), Decimal("60.97"))
        self.assertEqual(inv.discount_total(), Decimal("3.00"))
        self.assertEqual(inv.tax(), Decimal("4.64"))
        self.assertEqual(inv.total(), Decimal("62.61"))

    def test_R3_csv_shape(self):
        out = to_csv(Invoice([LineItem("Widget", 2, "10.00")])).splitlines()
        self.assertEqual(out[0], "description,quantity,unit_price,discount_pct,net")
        self.assertEqual(out[-1], "TOTAL,,,,20.00")

    def test_R4_cli(self):
        from ledgerlite.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "i.json")
            with open(p, "w") as h:
                json.dump({"tax_rate": "0.1", "lines": [{"description": "A", "quantity": 1, "unit_price": "100", "discount_pct": 10}]}, h)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main([p]), 0)
            self.assertIn("Total: $99.00", out.getvalue())
