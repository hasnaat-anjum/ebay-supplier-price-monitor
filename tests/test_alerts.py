"""Tests for alerts.py — alert rules and the supplier scorecard."""

import unittest
from decimal import Decimal

from supplier_monitor.alerts import compute_alerts, supplier_scorecard
from supplier_monitor.models import PriceCheck, TrackedItem

FEE = Decimal("13.25")
FIXED = 30
TODAY = "2026-11-25"


def make_item(**overrides):
    data = {
        "sku": "LED-STRIP-5M",
        "title": "RGB LED Strip Lights 5m",
        "ebay_price_cents": 2499,
        "supplier": "Amazon",
        "supplier_price_cents": 1150,
        "supplier_shipping_cents": 200,
        "stock_status": "in_stock",
        "min_margin_percent": 15.0,
        "check_interval_days": 7,
        "date_added": "2026-11-15",
        "last_checked": "2026-11-25",
        "history": [
            PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
        ],
    }
    data.update(overrides)
    return TrackedItem(**data)


class TestAlerts(unittest.TestCase):
    def test_healthy_item_has_no_alerts(self):
        self.assertEqual(compute_alerts([make_item()], TODAY, FEE, FIXED), [])

    def test_stock_out_is_critical_with_pause_action(self):
        alerts = compute_alerts(
            [make_item(stock_status="out_of_stock")], TODAY, FEE, FIXED
        )
        kinds = {a.kind: a for a in alerts}
        self.assertIn("stock_out", kinds)
        self.assertEqual(kinds["stock_out"].severity, "critical")
        self.assertIn("Pause listing", kinds["stock_out"].action)

    def test_low_stock_is_warning(self):
        alerts = compute_alerts(
            [make_item(stock_status="low_stock")], TODAY, FEE, FIXED
        )
        kinds = {a.kind: a for a in alerts}
        self.assertEqual(kinds["low_stock"].severity, "warning")

    def test_margin_breach_suggests_new_price(self):
        # Supplier jumped to $16.50: margin 11.5%, below the 15% floor.
        item = make_item(
            supplier_price_cents=1650,
            history=[
                PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
                PriceCheck("2026-11-25", 1650, 200, "in_stock", "feed"),
            ],
        )
        alerts = compute_alerts([item], TODAY, FEE, FIXED)
        breach = [a for a in alerts if a.kind == "margin_breach"]
        self.assertEqual(len(breach), 1)
        self.assertIn("Raise eBay price to $26.21", breach[0].action)

    def test_unrecoverable_margin_suggests_new_supplier(self):
        # Supplier $20.00 + $5.00 shipping against a $10.00 eBay price:
        # restoring the 15% floor would need $35.26, over 3x the price.
        item = make_item(
            ebay_price_cents=1000,
            supplier_price_cents=2000,
            supplier_shipping_cents=500,
        )
        alerts = compute_alerts([item], TODAY, FEE, FIXED)
        breach = [a for a in alerts if a.kind == "margin_breach"]
        self.assertEqual(breach[0].severity, "critical")
        self.assertIn("Find a new supplier", breach[0].action)

    def test_price_rise_alert(self):
        item = make_item(
            supplier_price_cents=1300,
            history=[
                PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
                PriceCheck("2026-11-25", 1300, 200, "in_stock", "feed"),
            ],
        )
        alerts = compute_alerts([item], TODAY, FEE, FIXED)
        rise = [a for a in alerts if a.kind == "price_rise"]
        self.assertEqual(len(rise), 1)
        self.assertIn("+13.0%", rise[0].message)

    def test_price_rise_that_wipes_margin_is_critical(self):
        item = make_item(
            supplier_price_cents=1650,
            history=[
                PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
                PriceCheck("2026-11-25", 1650, 200, "in_stock", "feed"),
            ],
        )
        alerts = compute_alerts([item], TODAY, FEE, FIXED)
        rise = [a for a in alerts if a.kind == "price_rise"]
        self.assertEqual(rise[0].severity, "critical")
        self.assertIn("wiped out the margin", rise[0].message)

    def test_price_drop_is_info(self):
        item = make_item(
            supplier_price_cents=1000,
            history=[
                PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
                PriceCheck("2026-11-25", 1000, 200, "in_stock", "feed"),
            ],
        )
        alerts = compute_alerts([item], TODAY, FEE, FIXED)
        drop = [a for a in alerts if a.kind == "price_drop"]
        self.assertEqual(len(drop), 1)
        self.assertEqual(drop[0].severity, "info")

    def test_stale_check_after_interval(self):
        item = make_item(last_checked="2026-11-15")  # 10 days ago, interval 7
        alerts = compute_alerts([item], TODAY, FEE, FIXED)
        stale = [a for a in alerts if a.kind == "stale_check"]
        self.assertEqual(len(stale), 1)
        self.assertIn("10 days", stale[0].message)

    def test_check_exactly_at_interval_is_not_stale(self):
        item = make_item(last_checked="2026-11-18")  # exactly 7 days ago
        kinds = {a.kind for a in compute_alerts([item], TODAY, FEE, FIXED)}
        self.assertNotIn("stale_check", kinds)

    def test_never_checked_is_stale(self):
        item = make_item(last_checked="")
        kinds = {a.kind for a in compute_alerts([item], TODAY, FEE, FIXED)}
        self.assertIn("stale_check", kinds)

    def test_alerts_sorted_critical_first(self):
        bad = make_item(sku="AAA", stock_status="out_of_stock")
        ok = make_item(sku="ZZZ", supplier_price_cents=1000, history=[
            PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
            PriceCheck("2026-11-25", 1000, 200, "in_stock", "feed"),
        ])
        alerts = compute_alerts([ok, bad], TODAY, FEE, FIXED)
        self.assertEqual(alerts[0].severity, "critical")


class TestScorecard(unittest.TestCase):
    def test_empty_scorecard(self):
        self.assertEqual(supplier_scorecard([], TODAY), [])

    def test_perfect_supplier_scores_100(self):
        rows = supplier_scorecard([make_item()], TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reliability"], 100)
        self.assertEqual(rows[0]["items"], 1)

    def test_stock_out_costs_10_points(self):
        item = make_item(history=[
            PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
            PriceCheck("2026-11-25", 1150, 200, "out_of_stock", "feed"),
        ])
        rows = supplier_scorecard([item], TODAY)
        self.assertEqual(rows[0]["stock_outs_30d"], 1)
        self.assertEqual(rows[0]["reliability"], 90)

    def test_big_rise_costs_5_points(self):
        item = make_item(
            supplier_price_cents=1400,
            history=[
                PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
                PriceCheck("2026-11-25", 1400, 200, "in_stock", "feed"),
            ],
        )
        rows = supplier_scorecard([item], TODAY)
        # +21.7% drift counts as a big rise.
        self.assertAlmostEqual(rows[0]["avg_drift_percent"], 21.7, places=1)
        self.assertEqual(rows[0]["reliability"], 95)

    def test_reliability_floor_is_zero(self):
        history = [PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo")]
        for day in range(16, 26):
            history.append(
                PriceCheck(f"2026-11-{day}", 1150, 200, "out_of_stock", "feed")
            )
        rows = supplier_scorecard([make_item(history=history)], TODAY)
        self.assertEqual(rows[0]["reliability"], 0)

    def test_old_history_ignored(self):
        item = make_item(history=[
            PriceCheck("2026-10-01", 1000, 200, "out_of_stock", "feed"),
            PriceCheck("2026-11-15", 1150, 200, "in_stock", "demo"),
        ])
        rows = supplier_scorecard([item], TODAY)
        self.assertEqual(rows[0]["stock_outs_30d"], 0)
        self.assertEqual(rows[0]["reliability"], 100)


if __name__ == "__main__":
    unittest.main()
