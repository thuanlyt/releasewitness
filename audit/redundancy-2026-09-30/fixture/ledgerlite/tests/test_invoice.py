import unittest
from decimal import Decimal

from ledgerlite.invoice import Invoice, LineItem


class InvoiceTests(unittest.TestCase):
    def test_totals_without_discount(self):
        invoice = Invoice([LineItem("Widget", 2, "10.00"), LineItem("Gadget", 1, "5.00")], "0.10")
        self.assertEqual(invoice.subtotal(), Decimal("25.00"))
        self.assertEqual(invoice.tax(), Decimal("2.50"))
        self.assertEqual(invoice.total(), Decimal("27.50"))

    def test_discount_without_tax(self):
        invoice = Invoice([LineItem("Widget", 4, "25.00", 10)])
        self.assertEqual(invoice.discount_total(), Decimal("10.00"))
        self.assertEqual(invoice.total(), Decimal("90.00"))

    def test_zero_quantity_rejected(self):
        with self.assertRaises(ValueError):
            LineItem("Nothing", 0, "1.00")

    def test_from_dict(self):
        invoice = Invoice.from_dict({"tax_rate": "0.2", "lines": [{"description": "A", "quantity": 1, "unit_price": "50"}]})
        self.assertEqual(invoice.total(), Decimal("60.00"))


if __name__ == "__main__":
    unittest.main()
