"""Command-line interface for eBay Supplier Price Monitor.

Every subcommand prints human-readable output and exits 0 on success.
Bad input prints a clear "Error: ..." message and exits 2 — never a
traceback. Run ``python -m supplier_monitor --help`` for the full list.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import __version__
from .alerts import compute_alerts, supplier_scorecard
from .fees import (
    format_money,
    margin_percent,
    net_profit_cents,
    parse_money,
)
from .fetch import fetch_supplier_price
from .import_csv import export_items_csv, import_items_csv, parse_feed_csv
from .models import PriceCheck, TrackedItem, validate_iso_date
from .reports import dashboard_text, export_csv, export_html, export_json, history_text
from .store import Store, StoreError

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "monitor.json"

# -- demo data ------------------------------------------------------------
# Ten realistic tracked items. Seeded relative to "today" so the demo
# always shows a meaningful picture: items were added and last checked
# 10 days ago, so any item the week-1 feed does not touch is stale.
DEMO_ITEMS = [
    {
        "sku": "LED-STRIP-5M", "title": "RGB LED Strip Lights 5m with Remote",
        "ebay_item_id": "155512345678", "ebay_price": "24.99",
        "supplier": "Amazon", "supplier_url": "https://example.com/supplier/led-strip-5m",
        "supplier_price": "11.50", "shipping": "2.00",
    },
    {
        "sku": "EARBUDS-PRO", "title": "Wireless Earbuds Pro with Charging Case",
        "ebay_item_id": "155512345679", "ebay_price": "19.99",
        "supplier": "AliExpress", "supplier_url": "https://example.com/supplier/earbuds-pro",
        "supplier_price": "8.25", "shipping": "1.50",
    },
    {
        "sku": "PHONE-CASE-IPH15", "title": "Clear Phone Case for iPhone 15",
        "ebay_item_id": "155512345680", "ebay_price": "12.99",
        "supplier": "AliExpress", "supplier_url": "https://example.com/supplier/phone-case-iph15",
        "supplier_price": "3.80", "shipping": "1.00",
    },
    {
        "sku": "KITCHEN-CHOPPER", "title": "Manual Vegetable Chopper 500ml",
        "ebay_item_id": "155512345681", "ebay_price": "29.99",
        "supplier": "Walmart", "supplier_url": "https://example.com/supplier/kitchen-chopper",
        "supplier_price": "14.00", "shipping": "3.00",
    },
    {
        "sku": "BT-SPEAKER-MINI", "title": "Mini Bluetooth Speaker Waterproof",
        "ebay_item_id": "155512345682", "ebay_price": "21.99",
        "supplier": "Amazon", "supplier_url": "https://example.com/supplier/bt-speaker-mini",
        "supplier_price": "9.75", "shipping": "2.50",
    },
    {
        "sku": "MOUSE-WIRELESS", "title": "Wireless Mouse Silent Click 2.4GHz",
        "ebay_item_id": "155512345683", "ebay_price": "15.99",
        "supplier": "AliExpress", "supplier_url": "https://example.com/supplier/mouse-wireless",
        "supplier_price": "6.40", "shipping": "1.25",
    },
    {
        "sku": "YOGA-MAT-PRO", "title": "Non-Slip Yoga Mat 6mm with Carry Strap",
        "ebay_item_id": "155512345684", "ebay_price": "27.99",
        "supplier": "Walmart", "supplier_url": "https://example.com/supplier/yoga-mat-pro",
        "supplier_price": "12.00", "shipping": "4.00",
    },
    {
        "sku": "USB-C-HUB-7IN1", "title": "USB-C Hub 7-in-1 with 4K HDMI",
        "ebay_item_id": "155512345685", "ebay_price": "34.99",
        "supplier": "Amazon", "supplier_url": "https://example.com/supplier/usb-c-hub-7in1",
        "supplier_price": "16.50", "shipping": "2.75",
    },
    {
        "sku": "PET-HAIR-ROLLER", "title": "Reusable Pet Hair Remover Roller",
        "ebay_item_id": "155512345686", "ebay_price": "9.99",
        "supplier": "eBay Wholesale", "supplier_url": "https://example.com/supplier/pet-hair-roller",
        "supplier_price": "2.90", "shipping": "0.99",
    },
    {
        "sku": "DESK-LAMP-LED", "title": "LED Desk Lamp with USB Charging Port",
        "ebay_item_id": "155512345687", "ebay_price": "22.99",
        "supplier": "Amazon", "supplier_url": "https://example.com/supplier/desk-lamp-led",
        "supplier_price": "10.25", "shipping": "2.25",
    },
]


def _today(args) -> str:
    """The working date: --today when given (useful for repeatable
    demos and tests), otherwise the real current date."""
    if args.today:
        return validate_iso_date(args.today, "--today")
    return date.today().isoformat()


def _load_store(args) -> Store:
    return Store(args.data)


def _require_item(store: Store, sku: str) -> TrackedItem:
    item = store.get(sku)
    if item is None:
        raise StoreError(f"no tracked item with SKU {sku!r} — see `list`")
    return item


def _margin_line(item: TrackedItem, store: Store) -> str:
    margin = margin_percent(
        item.ebay_price_cents, item.supplier_price_cents,
        item.supplier_shipping_cents, store.fee_percent, store.fixed_fee_cents,
    )
    flag = "  <-- BELOW FLOOR" if margin < item.min_margin_percent else ""
    return f"margin {margin:.1f}% (floor {item.min_margin_percent:.0f}%){flag}"


# -- subcommand handlers ---------------------------------------------------

def cmd_add(args) -> int:
    store = _load_store(args)
    item = TrackedItem(
        sku=args.sku,
        title=args.title,
        ebay_item_id=args.ebay_item_id or "",
        ebay_price_cents=parse_money(args.ebay_price),
        supplier=args.supplier,
        supplier_url=args.supplier_url or "",
        supplier_price_cents=parse_money(args.supplier_price),
        supplier_shipping_cents=parse_money(args.shipping),
        stock_status=args.stock,
        min_margin_percent=args.min_margin,
        check_interval_days=args.interval,
        notes=args.notes or "",
        date_added=_today(args),
        last_checked="",
    )
    store.add(item)
    print(f"Added {item.sku} — {item.title}")
    print(f"  eBay {format_money(item.ebay_price_cents)}  "
          f"supplier {format_money(item.supplier_price_cents)} "
          f"(+ {format_money(item.supplier_shipping_cents)} shipping)")
    print(f"  {_margin_line(item, store)}")
    return 0


def cmd_list(args) -> int:
    store = _load_store(args)
    items = store.all_items()
    if args.supplier:
        items = [i for i in items if i.supplier.lower() == args.supplier.lower()]
    if args.at_risk:
        alerts = compute_alerts(
            items, _today(args), store.fee_percent, store.fixed_fee_cents
        )
        risky = {a.sku for a in alerts if a.severity in ("critical", "warning")}
        items = [i for i in items if i.sku in risky]
    if not items:
        print("No tracked items yet. Run `demo` to load sample data,")
        print("or `add --help` to track your first product.")
        return 0
    print(f"{'SKU':22} {'Title':38} {'eBay':>8}  {'Supplier':>8}  "
          f"{'Supplier':14} {'Stock':12}  Margin")
    print("-" * 122)
    for item in items:
        margin = margin_percent(
            item.ebay_price_cents, item.supplier_price_cents,
            item.supplier_shipping_cents, store.fee_percent, store.fixed_fee_cents,
        )
        flag = " !" if margin < item.min_margin_percent else ""
        print(
            f"{item.sku:22} {item.title[:38]:38} "
            f"{format_money(item.ebay_price_cents):>8}  "
            f"{format_money(item.supplier_price_cents):>8}  "
            f"{item.supplier:14} {item.stock_status:12}  "
            f"{margin:.1f}%{flag}"
        )
    print(f"\n{len(items)} item(s). '!' = margin below floor.")
    return 0


def cmd_update(args) -> int:
    store = _load_store(args)
    item = _require_item(store, args.sku)
    if args.title is not None:
        item.title = args.title
    if args.ebay_price is not None:
        item.ebay_price_cents = parse_money(args.ebay_price)
    if args.supplier is not None:
        item.supplier = args.supplier
    if args.supplier_url is not None:
        item.supplier_url = args.supplier_url
    if args.supplier_price is not None:
        item.supplier_price_cents = parse_money(args.supplier_price)
    if args.shipping is not None:
        item.supplier_shipping_cents = parse_money(args.shipping)
    if args.stock is not None:
        item.stock_status = args.stock
    if args.min_margin is not None:
        if not 0 <= args.min_margin < 100:
            raise ValueError("minimum margin % must be between 0 and 100")
        item.min_margin_percent = args.min_margin
    if args.interval is not None:
        if args.interval < 1:
            raise ValueError("check interval must be at least 1 day")
        item.check_interval_days = args.interval
    if args.notes is not None:
        item.notes = args.notes
    store.update(item)
    print(f"Updated {item.sku} — {item.title}")
    print(f"  {_margin_line(item, store)}")
    return 0


def cmd_remove(args) -> int:
    store = _load_store(args)
    item = _require_item(store, args.sku)
    if not args.yes:
        answer = input(f"Remove {item.sku} ({item.title})? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 0
    store.remove(args.sku)
    print(f"Removed {args.sku}.")
    return 0


def cmd_check(args) -> int:
    store = _load_store(args)
    item = _require_item(store, args.sku)
    price = parse_money(args.price)
    shipping = (
        parse_money(args.shipping)
        if args.shipping is not None
        else item.supplier_shipping_cents
    )
    stock = args.stock if args.stock is not None else item.stock_status
    old_price = item.supplier_price_cents
    store.record_check(args.sku, price, shipping, stock, "manual", _today(args))
    item = store.get(args.sku)
    print(f"Checked {item.sku} — {item.title}")
    print(f"  supplier price {format_money(old_price)} -> {format_money(price)}"
          + (f"  (+{((price - old_price) / old_price * 100):.1f}%)" if old_price else ""))
    print(f"  stock: {stock}   shipping: {format_money(shipping)}")
    print(f"  {_margin_line(item, store)}")
    return 0


def cmd_check_feed(args) -> int:
    store = _load_store(args)
    rows, errors = parse_feed_csv(args.file)
    applied = 0
    skipped: list[str] = []
    for row in rows:
        if store.get(row["sku"]) is None:
            skipped.append(row["sku"])
            continue
        store.record_check(
            row["sku"],
            row["supplier_price_cents"],
            row["shipping_cents"],
            row["stock_status"],
            "feed",
            _today(args),
        )
        applied += 1
    print(f"Feed applied: {applied} item(s) updated.")
    if skipped:
        print(f"Unknown SKUs skipped ({len(skipped)}): {', '.join(skipped)}")
    for error in errors:
        print(f"  skipped row: {error}")
    print("Run `alerts` to see what changed.")
    return 0


def cmd_fetch(args) -> int:
    store = _load_store(args)
    item = _require_item(store, args.sku)
    result = fetch_supplier_price(item.supplier_url)
    if not result.ok:
        note = f"[{_today(args)}] fetch failed: {result.message}"
        item.notes = (item.notes + " " + note).strip() if item.notes else note
        store.update(item)
        print(f"Fetch failed for {item.sku}: {result.message}")
        print("Recorded as a failed-check note on the item. No data changed.")
        return 0
    shipping = item.supplier_shipping_cents
    store.record_check(
        args.sku, result.price_cents, shipping, result.stock_status,
        "fetch", _today(args),
    )
    item = store.get(args.sku)
    print(f"Fetched {item.sku} — {item.title}")
    print(f"  supplier price {format_money(result.price_cents)}  "
          f"stock: {result.stock_status}")
    print(f"  {_margin_line(item, store)}")
    return 0


def cmd_alerts(args) -> int:
    store = _load_store(args)
    alerts = compute_alerts(
        store.all_items(), _today(args), store.fee_percent, store.fixed_fee_cents
    )
    if not alerts:
        print("No alerts. Everything looks healthy.")
        return 0
    counts = {"critical": 0, "warning": 0, "info": 0}
    for alert in alerts:
        counts[alert.severity] += 1
    print(f"{len(alerts)} alert(s): {counts['critical']} critical, "
          f"{counts['warning']} warning, {counts['info']} info\n")
    for alert in alerts:
        print(alert.display())
        print()
    return 0


def cmd_dashboard(args) -> int:
    store = _load_store(args)
    print(dashboard_text(
        store.all_items(), _today(args), store.fee_percent, store.fixed_fee_cents
    ))
    return 0


def cmd_history(args) -> int:
    store = _load_store(args)
    item = _require_item(store, args.sku)
    print(history_text(item, store.fee_percent, store.fixed_fee_cents))
    return 0


def cmd_suppliers(args) -> int:
    store = _load_store(args)
    rows = supplier_scorecard(store.all_items(), _today(args))
    if not rows:
        print("No suppliers yet. Run `demo` to load sample data.")
        return 0
    print(f"{'Supplier':16} {'Items':>5}  {'Checks 30d':>10}  "
          f"{'Stock-outs':>10}  {'Avg drift':>9}  Reliability")
    print("-" * 74)
    for row in rows:
        print(
            f"{row['supplier']:16} {row['items']:>5}  {row['checks_30d']:>10}  "
            f"{row['stock_outs_30d']:>10}  {row['avg_drift_percent']:>+8.1f}%  "
            f"{row['reliability']}/100"
        )
    print("\nReliability starts at 100, -10 per stock-out, -5 per price rise")
    print("over 10% in the last 30 days.")
    return 0


def cmd_export(args) -> int:
    store = _load_store(args)
    items = store.all_items()
    if args.format == "csv":
        export_csv(items, args.out, _today(args), store.fee_percent, store.fixed_fee_cents)
    elif args.format == "json":
        export_json(items, args.out, _today(args), store.fee_percent, store.fixed_fee_cents)
    else:
        export_html(items, args.out, _today(args), store.fee_percent, store.fixed_fee_cents)
    print(f"Exported {len(items)} item(s) to {args.out} ({args.format}).")
    return 0


def cmd_import_items(args) -> int:
    store = _load_store(args)
    items, errors = import_items_csv(args.file, _today(args))
    added = 0
    updated = 0
    for item in items:
        if store.get(item.sku) is not None:
            store.update(item)
            updated += 1
        else:
            store.add(item)
            added += 1
    print(f"Imported {added} new item(s), updated {updated} existing item(s).")
    for error in errors:
        print(f"  skipped row: {error}")
    return 0


def cmd_export_items(args) -> int:
    store = _load_store(args)
    count = export_items_csv(store.all_items(), args.file)
    print(f"Exported {count} item(s) to {args.file}.")
    return 0


def cmd_demo(args) -> int:
    store = _load_store(args)
    if store.items and not args.force:
        print("The store already has items. Use `demo --force` to replace them.")
        return 0
    today = date.fromisoformat(_today(args))
    seeded_on = (today - timedelta(days=10)).isoformat()
    store.items = {}
    for raw in DEMO_ITEMS:
        item = TrackedItem(
            sku=raw["sku"],
            title=raw["title"],
            ebay_item_id=raw["ebay_item_id"],
            ebay_price_cents=parse_money(raw["ebay_price"]),
            supplier=raw["supplier"],
            supplier_url=raw["supplier_url"],
            supplier_price_cents=parse_money(raw["supplier_price"]),
            supplier_shipping_cents=parse_money(raw["shipping"]),
            stock_status="in_stock",
            date_added=seeded_on,
            last_checked=seeded_on,
        )
        # Seed the opening check directly (record_check would stamp
        # last_checked with today, so build the history by hand).
        item.history.append(PriceCheck(
            date=seeded_on,
            supplier_price_cents=item.supplier_price_cents,
            shipping_cents=item.supplier_shipping_cents,
            stock_status="in_stock",
            source="demo",
        ))
        store.items[item.sku] = item
    store.save()
    print(f"Loaded {len(DEMO_ITEMS)} demo items (seeded {seeded_on}).")
    print("Try: dashboard, alerts, suppliers,")
    print("     check-feed examples/supplier_feed_week1.csv")
    return 0


def cmd_config(args) -> int:
    store = _load_store(args)
    if args.fee_percent is None and args.fixed_fee is None:
        print(f"eBay fee: {store.fee_percent}% + "
              f"{format_money(store.fixed_fee_cents)} per order")
        return 0
    fee_percent = None
    if args.fee_percent is not None:
        try:
            fee_percent = Decimal(str(args.fee_percent))
        except InvalidOperation:
            raise ValueError(f"invalid fee %: {args.fee_percent!r}") from None
    fixed_fee = parse_money(args.fixed_fee) if args.fixed_fee is not None else None
    store.set_config(fee_percent=fee_percent, fixed_fee_cents=fixed_fee)
    print(f"eBay fee is now {store.fee_percent}% + "
          f"{format_money(store.fixed_fee_cents)} per order")
    return 0


# -- argument parser -------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supplier_monitor",
        description=(
            "Watch your suppliers' prices and stock so a price rise never "
            "eats your eBay margin. Free, local, no API key."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--data", default=str(DEFAULT_DATA_PATH),
        help="path to the JSON store (default: data/monitor.json)",
    )
    parser.add_argument(
        "--today", default=None, metavar="YYYY-MM-DD",
        help="pretend today is this date (for repeatable demos and tests)",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("add", help="track a new product")
    p.add_argument("sku", help="unique product code, e.g. LED-STRIP-5M")
    p.add_argument("--title", required=True, help="product title")
    p.add_argument("--ebay-price", required=True, help="your eBay price, e.g. 24.99")
    p.add_argument("--supplier", required=True,
                   choices=["Amazon", "AliExpress", "Walmart", "eBay Wholesale", "Other"])
    p.add_argument("--supplier-price", required=True, help="supplier price, e.g. 11.50")
    p.add_argument("--shipping", default="0", help="supplier shipping cost (default 0)")
    p.add_argument("--ebay-item-id", default="", help="eBay item ID (optional)")
    p.add_argument("--supplier-url", default="", help="supplier product URL (optional)")
    p.add_argument("--stock", default="unknown",
                   choices=["in_stock", "low_stock", "out_of_stock", "unknown"])
    p.add_argument("--min-margin", type=float, default=15.0,
                   help="minimum margin %% (default 15)")
    p.add_argument("--interval", type=int, default=7,
                   help="check interval in days (default 7)")
    p.add_argument("--notes", default="", help="free-form notes")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="list tracked products")
    p.add_argument("--supplier", help="only show one supplier")
    p.add_argument("--at-risk", action="store_true", help="only show items with alerts")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("update", help="change a tracked product")
    p.add_argument("sku", help="SKU to update")
    p.add_argument("--title", help="new title")
    p.add_argument("--ebay-price", help="new eBay price")
    p.add_argument("--supplier", choices=["Amazon", "AliExpress", "Walmart", "eBay Wholesale", "Other"])
    p.add_argument("--supplier-url", help="new supplier URL")
    p.add_argument("--supplier-price", help="new supplier price")
    p.add_argument("--shipping", help="new supplier shipping cost")
    p.add_argument("--stock", choices=["in_stock", "low_stock", "out_of_stock", "unknown"])
    p.add_argument("--min-margin", type=float, help="new minimum margin %")
    p.add_argument("--interval", type=int, help="new check interval in days")
    p.add_argument("--notes", help="replace the notes")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("remove", help="stop tracking a product")
    p.add_argument("sku", help="SKU to remove")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("check", help="record a manual supplier check")
    p.add_argument("sku", help="SKU to check")
    p.add_argument("--price", required=True, help="supplier price you observed")
    p.add_argument("--shipping", help="supplier shipping cost (keeps the old one if omitted)")
    p.add_argument("--stock", choices=["in_stock", "low_stock", "out_of_stock", "unknown"],
                   help="stock status you observed")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("check-feed", help="apply a supplier price-feed CSV")
    p.add_argument("file", help="CSV with sku, supplier_price, stock_status, shipping columns")
    p.set_defaults(func=cmd_check_feed)

    p = sub.add_parser("fetch", help="fetch the supplier price from the product page")
    p.add_argument("sku", help="SKU to fetch (uses its supplier URL)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("alerts", help="show all current alerts")
    p.set_defaults(func=cmd_alerts)

    p = sub.add_parser("dashboard", help="show the overview dashboard")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("history", help="show one product's price history")
    p.add_argument("sku", help="SKU to show")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("suppliers", help="show the supplier scorecard")
    p.set_defaults(func=cmd_suppliers)

    p = sub.add_parser("export", help="export a report (csv, json or html)")
    p.add_argument("--format", required=True, choices=["csv", "json", "html"])
    p.add_argument("--out", required=True, help="output file path")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import-items", help="import tracked items from a CSV file")
    p.add_argument("file", help="CSV with sku, title, ebay_price, supplier, ... columns")
    p.set_defaults(func=cmd_import_items)

    p = sub.add_parser("export-items", help="export tracked items to a CSV file")
    p.add_argument("file", help="output CSV path")
    p.set_defaults(func=cmd_export_items)

    p = sub.add_parser("demo", help="load 10 realistic demo products")
    p.add_argument("--force", action="store_true", help="replace existing items")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("config", help="show or change the eBay fee settings")
    p.add_argument("--fee-percent", help="eBay final-value fee %%, e.g. 13.25")
    p.add_argument("--fixed-fee", help="fixed fee per order, e.g. 0.30")
    p.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (ValueError, StoreError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"Error: file not found — {exc.filename}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
