"""Atomic JSON store for the monitor data.

The store lives at ``data/monitor.json`` (or a custom path). Every save
writes to a temporary file in the same directory and then swaps it into
place with ``os.replace``, so a crash mid-write can never leave a
half-written file. The previous good file is kept as ``.bak``; if the
main file is corrupt on load, the backup is restored automatically.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from .fees import DEFAULT_FEE_PERCENT, DEFAULT_FIXED_FEE_CENTS
from .models import PriceCheck, TrackedItem

STORE_VERSION = 1


class StoreError(Exception):
    """Raised when the store cannot be read or written."""


class Store:
    """Holds the tracked items and the fee configuration."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.items: dict[str, TrackedItem] = {}
        self.fee_percent = DEFAULT_FEE_PERCENT
        self.fixed_fee_cents = DEFAULT_FIXED_FEE_CENTS
        self._load()

    # -- loading ---------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return  # fresh store; nothing to load yet
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._restore_backup()
            return
        try:
            self._load_data(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f"store file is invalid: {exc}") from exc

    def _restore_backup(self) -> None:
        """Try the .bak file; if it is also unusable, start empty."""
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        if not backup.exists():
            return
        try:
            data = json.loads(backup.read_text(encoding="utf-8"))
            self._load_data(data)
            # Promote the good backup back to the main file.
            shutil.copyfile(backup, self.path)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
            return

    def _load_data(self, data: dict) -> None:
        config = data.get("config", {})
        self.fee_percent = Decimal(str(config.get("fee_percent", "13.25")))
        self.fixed_fee_cents = int(config.get("fixed_fee_cents", 30))
        self.items = {}
        for raw in data.get("items", []):
            item = TrackedItem.from_dict(raw)
            self.items[item.sku] = item

    # -- saving ----------------------------------------------------------
    def save(self) -> None:
        """Atomically write the store, keeping the previous good copy
        as a .bak file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STORE_VERSION,
            "config": {
                "fee_percent": str(self.fee_percent),
                "fixed_fee_cents": self.fixed_fee_cents,
            },
            "items": [item.to_dict() for item in self.items.values()],
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                backup = self.path.with_suffix(self.path.suffix + ".bak")
                shutil.copyfile(self.path, backup)
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- item access ------------------------------------------------------
    def get(self, sku: str) -> TrackedItem | None:
        return self.items.get(sku)

    def add(self, item: TrackedItem) -> None:
        if item.sku in self.items:
            raise StoreError(f"an item with SKU {item.sku!r} already exists")
        self.items[item.sku] = item
        self.save()

    def update(self, item: TrackedItem) -> None:
        if item.sku not in self.items:
            raise StoreError(f"no item with SKU {item.sku!r}")
        self.items[item.sku] = item
        self.save()

    def remove(self, sku: str) -> TrackedItem:
        item = self.items.pop(sku, None)
        if item is None:
            raise StoreError(f"no item with SKU {sku!r}")
        self.save()
        return item

    def all_items(self) -> list[TrackedItem]:
        return sorted(self.items.values(), key=lambda item: item.sku)

    # -- checks -----------------------------------------------------------
    def record_check(
        self,
        sku: str,
        supplier_price_cents: int,
        shipping_cents: int,
        stock_status: str,
        source: str,
        today: str,
    ) -> PriceCheck:
        """Record one supplier check: update the item's current
        supplier price/stock, stamp last_checked, and append a
        timestamped entry to its price history."""
        item = self.get(sku)
        if item is None:
            raise StoreError(f"no item with SKU {sku!r}")
        check = PriceCheck(
            date=today,
            supplier_price_cents=supplier_price_cents,
            shipping_cents=shipping_cents,
            stock_status=stock_status,
            source=source,
        )
        item.supplier_price_cents = supplier_price_cents
        item.supplier_shipping_cents = shipping_cents
        item.stock_status = stock_status
        item.last_checked = today
        item.history.append(check)
        self.save()
        return check

    # -- config -----------------------------------------------------------
    def set_config(
        self,
        fee_percent: Decimal | None = None,
        fixed_fee_cents: int | None = None,
    ) -> None:
        if fee_percent is not None:
            if not Decimal(0) <= fee_percent < 100:
                raise StoreError("eBay fee % must be between 0 and 100")
            self.fee_percent = fee_percent
        if fixed_fee_cents is not None:
            if fixed_fee_cents < 0:
                raise StoreError("fixed fee must not be negative")
            self.fixed_fee_cents = fixed_fee_cents
        self.save()
