import unittest

from ledgerlite.export import to_csv
from ledgerlite.invoice import Invoice, LineItem


class ExportTests(unittest.TestCase):
    def test_csv_simple(self):
        invoice = Invoice([LineItem("Widget", 2, "10.00")])
        lines = to_csv(invoice).splitlines()
        self.assertEqual(lines[0], "description,quantity,unit_price,discount_pct,net")
        self.assertEqual(lines[1], "Widget,2,10.00,0,20.00")
        self.assertEqual(lines[-1], "TOTAL,,,,20.00")


if __name__ == "__main__":
    unittest.main()
