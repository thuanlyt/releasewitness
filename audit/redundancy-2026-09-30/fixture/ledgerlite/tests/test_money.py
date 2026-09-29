import unittest
from decimal import Decimal

from ledgerlite.money import format_money, parse_amount, round_money


class MoneyTests(unittest.TestCase):
    def test_parse_plain_and_separated(self):
        self.assertEqual(parse_amount("12.50"), Decimal("12.50"))
        self.assertEqual(parse_amount("1,234.50"), Decimal("1234.50"))
        self.assertEqual(parse_amount(7), Decimal(7))

    def test_parse_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_amount("abc")
        with self.assertRaises(ValueError):
            parse_amount("  ")

    def test_round_simple(self):
        self.assertEqual(round_money(Decimal("10.004")), Decimal("10.00"))
        self.assertEqual(round_money(Decimal("10.006")), Decimal("10.01"))

    def test_format_positive(self):
        self.assertEqual(format_money(Decimal("1234.5")), "$1,234.50")


if __name__ == "__main__":
    unittest.main()
