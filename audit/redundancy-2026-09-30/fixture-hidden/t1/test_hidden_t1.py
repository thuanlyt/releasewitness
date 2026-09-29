"""Held-out TEST-1/TEST-3 acceptance for the currency feature."""
import csv, io, json, os, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from ledgerlite.money import format_money, round_money
from ledgerlite.invoice import Invoice, LineItem
from ledgerlite.export import to_csv


class Currency(unittest.TestCase):
    def test_C1_round_minor_units(self):
        self.assertEqual(round_money(Decimal("2.675"), "USD"), Decimal("2.68"))
        self.assertEqual(round_money(Decimal("1234.5"), "JPY"), Decimal("1235"))
        self.assertEqual(round_money(Decimal("2.675")), Decimal("2.68"))

    def test_C2_format(self):
        self.assertEqual(format_money(Decimal("-1234.5"), "EUR"), "-€1,234.50")
        self.assertEqual(format_money(Decimal("1234.5"), "JPY"), "¥1,235")
        self.assertEqual(format_money(Decimal("5"), "USD"), "$5.00")

    def test_C3_unsupported(self):
        with self.assertRaises(ValueError):
            format_money(Decimal("1"), "GBP")
        with self.assertRaises(ValueError):
            Invoice([], "0", currency="GBP")

    def test_C4_invoice_jpy(self):
        inv = Invoice([LineItem("A", 3, "333.5", 10)], "0.1", currency="JPY")
        # gross 1000.5 -> 1001; discount 100.1 -> 100; taxable 901; tax 90.1 -> 90; total 991
        self.assertEqual(inv.subtotal(), Decimal("1001"))
        self.assertEqual(inv.discount_total(), Decimal("100"))
        self.assertEqual(inv.tax(), Decimal("90"))
        self.assertEqual(inv.total(), Decimal("991"))

    def test_C5_default_usd(self):
        inv = Invoice([LineItem("A", 1, "10.005")], "0")
        self.assertEqual(inv.total(), Decimal("10.01"))

    def test_C6_csv_currency_column(self):
        inv = Invoice([LineItem("A", 2, "10")], "0", currency="JPY")
        rows = list(csv.reader(io.StringIO(to_csv(inv))))
        self.assertEqual(rows[0][-1], "currency")
        self.assertEqual(rows[1][-1], "JPY")
        self.assertEqual(rows[-1][-1], "JPY")
        self.assertEqual(rows[1][2], "10")
        eur = list(csv.reader(io.StringIO(to_csv(Invoice([LineItem("A", 1, "10")], "0", currency="EUR")))))
        self.assertEqual(eur[1][2], "10.00")

    def run_cli(self, payload, *extra):
        from ledgerlite.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "i.json")
            with open(p, "w", encoding="utf-8") as h:
                json.dump(payload, h)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = main([p, *extra])
                except SystemExit as exc:  # "exits with status N" may be SystemExit(N)
                    code = exc.code
            return code, out.getvalue(), err.getvalue()

    def test_C7_cli_currency(self):
        payload = {"currency": "EUR", "tax_rate": "0", "lines": [{"description": "A", "quantity": 1, "unit_price": "1234.5"}]}
        code, out, _ = self.run_cli(payload)
        self.assertEqual(code, 0)
        self.assertIn("Total: €1,234.50", out)
        code, out, _ = self.run_cli(payload, "--currency", "JPY")
        self.assertEqual(code, 0)
        self.assertIn("Total: ¥1,235", out)
        code, _, err = self.run_cli(payload, "--currency", "XXX")
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())

    def test_C8_regression_usd(self):
        inv = Invoice([LineItem("A", 3, "19.99", 5), LineItem("B", 2, "0.50")], "0.08")
        self.assertEqual(inv.total(), Decimal("62.61"))
        code, out, _ = self.run_cli({"tax_rate": "0.1", "lines": [{"description": "A", "quantity": 1, "unit_price": "100"}]})
        self.assertIn("Total: $110.00", out)
