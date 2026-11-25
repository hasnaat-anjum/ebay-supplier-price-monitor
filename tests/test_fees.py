"""Tests for fees.py — exact-cent money math."""

import unittest
from decimal import Decimal

from supplier_monitor.fees import (
    DEFAULT_FEE_PERCENT,
    ebay_fee_cents,
    format_money,
    is_margin_unrecoverable,
    margin_percent,
    net_profit_cents,
    parse_money,
    suggested_ebay_price_cents,
)


class TestParseMoney(unittest.TestCase):
    def test_plain_amount(self):
        self.assertEqual(parse_money("24.99"), 2499)

    def test_dollar_sign_and_spaces(self):
        self.assertEqual(parse_money("  $11.50 "), 1150)

    def test_whole_dollars(self):
        self.assertEqual(parse_money("5"), 500)

    def test_zero(self):
        self.assertEqual(parse_money("0"), 0)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            parse_money("-1.00")

    def test_three_decimals_rejected(self):
        with self.assertRaises(ValueError):
            parse_money("1.999")

    def test_garbage_rejected(self):
        with self.assertRaises(ValueError):
            parse_money("abc")

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            parse_money("")


class TestFormatMoney(unittest.TestCase):
    def test_round_amount(self):
        self.assertEqual(format_money(2500), "$25.00")

    def test_cents(self):
        self.assertEqual(format_money(361), "$3.61")

    def test_negative(self):
        self.assertEqual(format_money(-150), "-$1.50")


class TestFeesAndMargin(unittest.TestCase):
    def test_ebay_fee_example(self):
        # Spec example: $24.99 -> round(24.99 * 13.25%) + $0.30 = $3.61.
        self.assertEqual(ebay_fee_cents(2499), 361)

    def test_ebay_fee_custom_rate(self):
        self.assertEqual(
            ebay_fee_cents(10000, fee_percent=Decimal("10"), fixed_fee_cents=0),
            1000,
        )

    def test_net_profit_example(self):
        # $24.99 - $3.61 - $11.50 - $2.00 = $7.88.
        self.assertEqual(net_profit_cents(2499, 1150, 200), 788)

    def test_margin_example(self):
        # $7.88 / $24.99 = 31.5%.
        self.assertAlmostEqual(margin_percent(2499, 1150, 200), 31.5, places=1)

    def test_negative_profit(self):
        self.assertLess(net_profit_cents(1000, 900, 500), 0)

    def test_margin_zero_price(self):
        self.assertEqual(margin_percent(0, 100, 0), 0.0)


class TestSuggestedPrice(unittest.TestCase):
    def test_restores_floor(self):
        # Supplier $16.50 + shipping $2.00, floor 15%:
        # (16.50 + 2.00 + 0.30) / (1 - 0.1325 - 0.15) = $26.21 (rounded up).
        suggested = suggested_ebay_price_cents(1650, 200, 15.0)
        self.assertEqual(suggested, 2621)
        # And the suggested price really does restore the margin.
        self.assertGreaterEqual(
            margin_percent(suggested, 1650, 200), 15.0
        )

    def test_impossible_margin_returns_none(self):
        # Fee 13.25% + floor 90% > 100% of the price: no price works.
        self.assertIsNone(suggested_ebay_price_cents(1000, 0, 90.0))

    def test_unrecoverable_when_over_3x(self):
        self.assertTrue(is_margin_unrecoverable(1000, 3001))
        self.assertFalse(is_margin_unrecoverable(1000, 3000))
        self.assertTrue(is_margin_unrecoverable(1000, None))


if __name__ == "__main__":
    unittest.main()
