"""Tests for cli.py — end-to-end runs through main().

All tests use a temporary --data file and a fixed --today, so they
never touch the real data/ directory.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from supplier_monitor.cli import main

TODAY = "2026-11-25"


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = str(Path(self.tmp.name) / "monitor.json")
        self.base = ["--data", self.data, "--today", TODAY]

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        """Run main() and return (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(self.base) + list(args))
        return code, out.getvalue(), err.getvalue()

    def add_item(self, sku="LED-STRIP-5M", price="24.99", supp="11.50"):
        code, out, _ = self.run_cli(
            "add", sku, "--title", "RGB LED Strip Lights 5m",
            "--ebay-price", price, "--supplier", "Amazon",
            "--supplier-price", supp, "--shipping", "2.00",
        )
        self.assertEqual(code, 0, out)
        return out


class TestAddListUpdateRemove(CliTestCase):
    def test_add_and_list(self):
        self.add_item()
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("LED-STRIP-5M", out)
        self.assertIn("$24.99", out)
        self.assertIn("31.5%", out)

    def test_add_duplicate_is_an_error_not_a_crash(self):
        self.add_item()
        code, _, err = self.run_cli(
            "add", "LED-STRIP-5M", "--title", "Again",
            "--ebay-price", "10.00", "--supplier", "Amazon",
            "--supplier-price", "5.00",
        )
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_add_bad_price_is_an_error(self):
        code, _, err = self.run_cli(
            "add", "BAD", "--title", "Bad", "--ebay-price", "abc",
            "--supplier", "Amazon", "--supplier-price", "1.00",
        )
        self.assertEqual(code, 2)
        self.assertIn("Error", err)

    def test_update_ebay_price(self):
        self.add_item()
        code, out, _ = self.run_cli("update", "LED-STRIP-5M", "--ebay-price", "29.99")
        self.assertEqual(code, 0)
        _, out2, _ = self.run_cli("list")
        self.assertIn("$29.99", out2)

    def test_update_unknown_sku(self):
        code, _, err = self.run_cli("update", "NOPE", "--ebay-price", "9.99")
        self.assertEqual(code, 2)
        self.assertIn("no tracked item", err)

    def test_remove_with_yes(self):
        self.add_item()
        code, out, _ = self.run_cli("remove", "LED-STRIP-5M", "--yes")
        self.assertEqual(code, 0)
        _, out2, _ = self.run_cli("list")
        self.assertNotIn("LED-STRIP-5M", out2)

    def test_list_empty_store(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("No tracked items", out)


class TestCheckAndAlerts(CliTestCase):
    def test_manual_check_records_history(self):
        self.add_item()
        code, out, _ = self.run_cli(
            "check", "LED-STRIP-5M", "--price", "13.00", "--stock", "in_stock"
        )
        self.assertEqual(code, 0)
        self.assertIn("$11.50 -> $13.00", out)
        _, out2, _ = self.run_cli("history", "LED-STRIP-5M")
        self.assertIn("$13.00", out2)
        self.assertIn("trend", out2)

    def test_check_unknown_sku(self):
        code, _, err = self.run_cli("check", "NOPE", "--price", "1.00")
        self.assertEqual(code, 2)

    def test_margin_breach_alert_after_check(self):
        self.add_item()
        self.run_cli("check", "LED-STRIP-5M", "--price", "16.50", "--stock", "in_stock")
        code, out, _ = self.run_cli("alerts")
        self.assertEqual(code, 0)
        self.assertIn("margin", out.lower())
        self.assertIn("Raise eBay price to $26.21", out)

    def test_no_alerts_when_healthy(self):
        self.add_item()
        # A fresh item has never been checked, so check it once first.
        self.run_cli("check", "LED-STRIP-5M", "--price", "11.50",
                     "--stock", "in_stock")
        code, out, _ = self.run_cli("alerts")
        self.assertEqual(code, 0)
        self.assertIn("No alerts", out)


class TestFeedAndDemo(CliTestCase):
    def test_demo_loads_ten_items(self):
        code, out, _ = self.run_cli("demo")
        self.assertEqual(code, 0)
        self.assertIn("10 demo items", out)
        _, out2, _ = self.run_cli("list")
        self.assertIn("EARBUDS-PRO", out2)

    def test_demo_refuses_when_store_not_empty(self):
        self.add_item()
        code, out, _ = self.run_cli("demo")
        self.assertEqual(code, 0)
        self.assertIn("already has items", out)

    def test_check_feed_applies_and_skips_unknown(self):
        self.run_cli("demo")
        feed = Path(self.tmp.name) / "feed.csv"
        feed.write_text(
            "sku,supplier_price,stock_status,shipping\n"
            "LED-STRIP-5M,16.50,in_stock,2.00\n"
            "EARBUDS-PRO,8.25,out_of_stock,1.50\n"
            "GHOST-SKU,5.00,in_stock,1.00\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("check-feed", str(feed))
        self.assertEqual(code, 0)
        self.assertIn("2 item(s) updated", out)
        self.assertIn("GHOST-SKU", out)
        _, alerts, _ = self.run_cli("alerts")
        self.assertIn("OUT OF STOCK", alerts)
        self.assertIn("Raise eBay price to $26.21", alerts)

    def test_check_feed_missing_file(self):
        code, _, err = self.run_cli("check-feed", str(Path(self.tmp.name) / "nope.csv"))
        self.assertEqual(code, 2)
        self.assertIn("not found", err)


class TestReports(CliTestCase):
    def test_dashboard(self):
        self.run_cli("demo")
        code, out, _ = self.run_cli("dashboard")
        self.assertEqual(code, 0)
        self.assertIn("Tracked items:        10", out)
        self.assertIn("Items at risk:", out)
        self.assertIn("Monthly profit at risk:", out)

    def test_suppliers_scorecard(self):
        self.run_cli("demo")
        code, out, _ = self.run_cli("suppliers")
        self.assertEqual(code, 0)
        self.assertIn("Amazon", out)
        self.assertIn("100/100", out)

    def test_export_html_is_self_contained(self):
        self.run_cli("demo")
        target = Path(self.tmp.name) / "report.html"
        code, _, _ = self.run_cli("export", "--format", "html", "--out", str(target))
        self.assertEqual(code, 0)
        text = target.read_text()
        self.assertIn("<!DOCTYPE html>", text)
        self.assertIn("LED-STRIP-5M", text)
        self.assertNotIn("<script", text)

    def test_export_csv_and_json(self):
        self.run_cli("demo")
        csv_path = Path(self.tmp.name) / "out.csv"
        json_path = Path(self.tmp.name) / "out.json"
        self.assertEqual(
            self.run_cli("export", "--format", "csv", "--out", str(csv_path))[0], 0
        )
        self.assertEqual(
            self.run_cli("export", "--format", "json", "--out", str(json_path))[0], 0
        )
        self.assertIn("LED-STRIP-5M", csv_path.read_text())
        payload = json.loads(json_path.read_text())
        self.assertEqual(len(payload["items"]), 10)

    def test_import_and_export_items_round_trip(self):
        self.run_cli("demo")
        csv_path = Path(self.tmp.name) / "items.csv"
        self.assertEqual(self.run_cli("export-items", str(csv_path))[0], 0)
        # Import into a fresh store and compare counts.
        fresh = str(Path(self.tmp.name) / "fresh.json")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--data", fresh, "--today", TODAY,
                         "import-items", str(csv_path)])
        self.assertEqual(code, 0)
        self.assertIn("10 new item(s)", out.getvalue())


class TestConfigAndHelp(CliTestCase):
    def test_config_show_and_set(self):
        code, out, _ = self.run_cli("config")
        self.assertIn("13.25%", out)
        code, out, _ = self.run_cli("config", "--fee-percent", "12.00", "--fixed-fee", "0.25")
        self.assertEqual(code, 0)
        self.assertIn("12.00%", out)
        _, out2, _ = self.run_cli("config")
        self.assertIn("12.00%", out2)

    def test_config_bad_fee(self):
        code, _, err = self.run_cli("config", "--fee-percent", "abc")
        self.assertEqual(code, 2)

    def test_help_exits_zero(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                main(list(self.base) + ["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("add", out.getvalue())

    def test_no_subcommand_shows_help(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--data", self.data, "--today", TODAY])
        self.assertEqual(code, 0)
        self.assertIn("usage", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
