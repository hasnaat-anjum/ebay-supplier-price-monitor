"""CSV import and export for tracked items and supplier price feeds.

Header aliases are tolerated so real-world exports work without
re-formatting: "SKU", "Item SKU", "sku" all mean the same column, and
so on. Unknown SKUs in a price feed are reported and skipped, never
a crash.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .fees import parse_money
from .models import TrackedItem

# Canonical field -> accepted header spellings (lower-cased, stripped).
ITEM_HEADER_ALIASES = {
    "sku": ("sku", "item sku", "product sku", "id"),
    "title": ("title", "item title", "product", "product title", "name"),
    "ebay_item_id": ("ebay_item_id", "ebay item id", "ebay id", "item id"),
    "ebay_price": ("ebay_price", "ebay price", "ebay price ($)", "sale price", "price"),
    "supplier": ("supplier", "supplier name"),
    "supplier_url": ("supplier_url", "supplier url", "url", "link"),
    "supplier_price": ("supplier_price", "supplier price", "cost", "supplier cost"),
    "supplier_shipping": (
        "supplier_shipping", "supplier shipping",
        "shipping", "shipping cost", "supplier shipping cost",
    ),
    "stock_status": ("stock_status", "stock status", "stock", "availability"),
    "min_margin_percent": (
        "min_margin_percent", "min margin %", "min margin",
        "minimum margin %", "minimum margin",
    ),
    "check_interval_days": (
        "check_interval_days", "check interval days",
        "check interval (days)", "interval days",
    ),
    "notes": ("notes", "note", "comments"),
    "date_added": ("date_added", "date added"),
    "last_checked": ("last_checked", "last checked"),
}

FEED_HEADER_ALIASES = {
    "sku": ("sku", "item sku", "product sku"),
    "supplier_price": ("supplier_price", "supplier price", "price", "cost"),
    "stock_status": ("stock_status", "stock status", "stock", "availability"),
    "shipping": ("shipping", "shipping cost", "supplier shipping"),
}


def _normalise_headers(fieldnames: list[str] | None, aliases: dict) -> dict[str, str]:
    """Map each canonical field name to the actual CSV column header
    that matches one of its aliases. Raises ValueError if a required
    field has no matching column."""
    if not fieldnames:
        raise ValueError("CSV file has no header row")
    lookup = {name.strip().lower(): name for name in fieldnames}
    mapping: dict[str, str] = {}
    for canonical, options in aliases.items():
        for option in options:
            if option in lookup:
                mapping[canonical] = lookup[option]
                break
    return mapping


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _money_field(row: dict, column: str | None, default: int = 0) -> int:
    if column is None:
        return default
    text = _clean(row.get(column))
    return parse_money(text) if text else default


def import_items_csv(path: str | Path, today: str) -> tuple[list[TrackedItem], list[str]]:
    """Read tracked items from a CSV file.

    Returns (items, errors). Rows with invalid data are skipped and
    reported in ``errors`` instead of aborting the whole import.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        mapping = _normalise_headers(reader.fieldnames, ITEM_HEADER_ALIASES)
        if "sku" not in mapping or "title" not in mapping:
            raise ValueError("CSV must have at least 'sku' and 'title' columns")
        items: list[TrackedItem] = []
        errors: list[str] = []
        for line_no, row in enumerate(reader, start=2):
            try:
                sku = _clean(row.get(mapping["sku"]))
                if not sku:
                    raise ValueError("empty SKU")
                stock = _clean(row.get(mapping.get("stock_status", ""))) or "unknown"
                stock = stock.lower().replace(" ", "_")
                if stock not in ("in_stock", "low_stock", "out_of_stock", "unknown"):
                    stock = "unknown"
                min_margin = _clean(row.get(mapping.get("min_margin_percent", "")))
                interval = _clean(row.get(mapping.get("check_interval_days", "")))
                item = TrackedItem(
                    sku=sku,
                    title=_clean(row.get(mapping["title"])),
                    ebay_item_id=_clean(row.get(mapping.get("ebay_item_id", ""))),
                    ebay_price_cents=_money_field(row, mapping.get("ebay_price")),
                    supplier=_clean(row.get(mapping.get("supplier", ""))) or "Other",
                    supplier_url=_clean(row.get(mapping.get("supplier_url", ""))),
                    supplier_price_cents=_money_field(
                        row, mapping.get("supplier_price")
                    ),
                    supplier_shipping_cents=_money_field(
                        row, mapping.get("supplier_shipping")
                    ),
                    stock_status=stock,
                    min_margin_percent=float(min_margin) if min_margin else 15.0,
                    check_interval_days=int(interval) if interval else 7,
                    notes=_clean(row.get(mapping.get("notes", ""))),
                    date_added=_clean(row.get(mapping.get("date_added", ""))) or today,
                    last_checked=_clean(row.get(mapping.get("last_checked", ""))),
                )
                items.append(item)
            except (ValueError, TypeError) as exc:
                errors.append(f"line {line_no}: {exc} — row skipped")
        return items, errors


def export_items_csv(items: list[TrackedItem], path: str | Path) -> int:
    """Write the tracked-items list to CSV. Returns the row count."""
    fieldnames = [
        "sku", "title", "ebay_item_id", "ebay_price", "supplier",
        "supplier_url", "supplier_price", "supplier_shipping",
        "stock_status", "min_margin_percent", "check_interval_days",
        "notes", "date_added", "last_checked",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow({
                "sku": item.sku,
                "title": item.title,
                "ebay_item_id": item.ebay_item_id,
                "ebay_price": f"{Decimal(item.ebay_price_cents) / 100:.2f}",
                "supplier": item.supplier,
                "supplier_url": item.supplier_url,
                "supplier_price": f"{Decimal(item.supplier_price_cents) / 100:.2f}",
                "supplier_shipping": (
                    f"{Decimal(item.supplier_shipping_cents) / 100:.2f}"
                ),
                "stock_status": item.stock_status,
                "min_margin_percent": item.min_margin_percent,
                "check_interval_days": item.check_interval_days,
                "notes": item.notes,
                "date_added": item.date_added,
                "last_checked": item.last_checked,
            })
    return len(items)


def parse_feed_csv(path: str | Path) -> tuple[list[dict], list[str]]:
    """Read a supplier price-feed CSV.

    Returns (rows, errors). Each row is a dict with keys ``sku``,
    ``supplier_price_cents``, ``shipping_cents`` and ``stock_status``.
    Bad rows are reported in ``errors`` and skipped.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        mapping = _normalise_headers(reader.fieldnames, FEED_HEADER_ALIASES)
        if "sku" not in mapping or "supplier_price" not in mapping:
            raise ValueError(
                "feed CSV must have 'sku' and 'supplier_price' columns"
            )
        rows: list[dict] = []
        errors: list[str] = []
        for line_no, row in enumerate(reader, start=2):
            try:
                sku = _clean(row.get(mapping["sku"]))
                if not sku:
                    raise ValueError("empty SKU")
                stock = _clean(row.get(mapping.get("stock_status", ""))) or "unknown"
                stock = stock.lower().replace(" ", "_")
                if stock not in ("in_stock", "low_stock", "out_of_stock", "unknown"):
                    raise ValueError(f"unknown stock status {stock!r}")
                rows.append({
                    "sku": sku,
                    "supplier_price_cents": _money_field(
                        row, mapping["supplier_price"]
                    ),
                    "shipping_cents": _money_field(row, mapping.get("shipping")),
                    "stock_status": stock,
                })
            except (ValueError, TypeError) as exc:
                errors.append(f"line {line_no}: {exc} — row skipped")
        return rows, errors


def format_money_plain(cents: int) -> str:
    """Money without the $ sign, for CSV/JSON output."""
    return f"{cents / 100:.2f}"