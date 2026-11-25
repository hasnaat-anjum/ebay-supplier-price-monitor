"""Tests for store.py — atomic JSON store, backup and restore."""

import json
import tempfile
import unittest
from pathlib import Path

from supplier_monitor.models import TrackedItem
from supplier_monitor.store import Store, StoreError


def make_item(sku="SKU-1"):
    return TrackedItem(
        sku=sku,
        title=f"Product {sku}",
        ebay_price_cents=2499,
        supplier="Amazon",
        supplier_price_cents=1150,
        supplier_shipping_cents=200,
        stock_status="in_stock",
        date_added="2026-11-15",
        last_checked="2026-11-15",
    )


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "monitor.json"

    def tearDown(self):
        self.tmp.cleanup()


class TestStoreBasics(StoreTestCase):
    def test_fresh_store_is_empty(self):
        store = Store(self.path)
        self.assertEqual(store.all_items(), [])

    def test_add_and_reload_round_trip(self):
        store = Store(self.path)
        store.add(make_item())
        reloaded = Store(self.path)
        item = reloaded.get("SKU-1")
        self.assertIsNotNone(item)
        self.assertEqual(item.title, "Product SKU-1")
        self.assertEqual(item.ebay_price_cents, 2499)
        self.assertEqual(item.supplier_price_cents, 1150)

    def test_duplicate_sku_rejected(self):
        store = Store(self.path)
        store.add(make_item())
        with self.assertRaises(StoreError):
            store.add(make_item())

    def test_remove_missing_rejected(self):
        store = Store(self.path)
        with self.assertRaises(StoreError):
            store.remove("NOPE")

    def test_remove_then_reload(self):
        store = Store(self.path)
        store.add(make_item())
        store.remove("SKU-1")
        self.assertEqual(Store(self.path).all_items(), [])

    def test_update_persists(self):
        store = Store(self.path)
        store.add(make_item())
        item = store.get("SKU-1")
        item.title = "Renamed"
        store.update(item)
        self.assertEqual(Store(self.path).get("SKU-1").title, "Renamed")

    def test_all_items_sorted_by_sku(self):
        store = Store(self.path)
        store.add(make_item("ZZZ"))
        store.add(make_item("AAA"))
        self.assertEqual([i.sku for i in store.all_items()], ["AAA", "ZZZ"])


class TestRecordCheck(StoreTestCase):
    def test_check_updates_price_stock_and_history(self):
        store = Store(self.path)
        store.add(make_item())
        store.record_check("SKU-1", 1300, 250, "low_stock", "manual", "2026-11-25")
        item = store.get("SKU-1")
        self.assertEqual(item.supplier_price_cents, 1300)
        self.assertEqual(item.supplier_shipping_cents, 250)
        self.assertEqual(item.stock_status, "low_stock")
        self.assertEqual(item.last_checked, "2026-11-25")
        self.assertEqual(len(item.history), 1)
        self.assertEqual(item.history[0].source, "manual")

    def test_check_unknown_sku_rejected(self):
        store = Store(self.path)
        with self.assertRaises(StoreError):
            store.record_check("NOPE", 100, 0, "in_stock", "manual", "2026-11-25")

    def test_history_survives_reload(self):
        store = Store(self.path)
        store.add(make_item())
        store.record_check("SKU-1", 1300, 250, "in_stock", "feed", "2026-11-25")
        reloaded = Store(self.path).get("SKU-1")
        self.assertEqual(reloaded.history[0].supplier_price_cents, 1300)
        self.assertEqual(reloaded.history[0].date, "2026-11-25")


class TestConfig(StoreTestCase):
    def test_default_config(self):
        store = Store(self.path)
        self.assertEqual(str(store.fee_percent), "13.25")
        self.assertEqual(store.fixed_fee_cents, 30)

    def test_config_persists(self):
        store = Store(self.path)
        from decimal import Decimal
        store.set_config(fee_percent=Decimal("12.5"), fixed_fee_cents=40)
        reloaded = Store(self.path)
        self.assertEqual(str(reloaded.fee_percent), "12.5")
        self.assertEqual(reloaded.fixed_fee_cents, 40)

    def test_invalid_fee_rejected(self):
        store = Store(self.path)
        from decimal import Decimal
        with self.assertRaises(StoreError):
            store.set_config(fee_percent=Decimal("150"))


class TestAtomicityAndBackup(StoreTestCase):
    def test_backup_created_on_second_save(self):
        store = Store(self.path)
        store.add(make_item())
        store.add(make_item("SKU-2"))
        backup = self.path.with_suffix(".json.bak")
        self.assertTrue(backup.exists())
        data = json.loads(backup.read_text())
        self.assertEqual(len(data["items"]), 1)  # previous good state

    def test_corrupt_main_file_restores_backup(self):
        store = Store(self.path)
        store.add(make_item())
        store.add(make_item("SKU-2"))
        self.path.write_text("{not valid json", encoding="utf-8")
        restored = Store(self.path)
        self.assertEqual(len(restored.all_items()), 1)
        self.assertEqual(restored.get("SKU-1").sku, "SKU-1")

    def test_corrupt_file_without_backup_starts_empty(self):
        self.path.write_text("garbage", encoding="utf-8")
        store = Store(self.path)
        self.assertEqual(store.all_items(), [])

    def test_no_temp_files_left_after_save(self):
        store = Store(self.path)
        store.add(make_item())
        leftovers = list(Path(self.tmp.name).glob("*.tmp"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
