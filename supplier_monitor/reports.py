"""Reports: dashboard, price history with sparkline, and exports.

The dashboard's "monthly profit at risk" assumes 10 sales per month
per item — a documented, conservative estimate for a small eBay store —
and says so in the output, so the number is never mistaken for a
measured figure.
"""

from __future__ import annotations

import csv
import html as html_lib
import json
from datetime import date
from decimal import Decimal

from .alerts import compute_alerts, supplier_scorecard
from .fees import format_money, margin_percent, net_profit_cents
from .models import Alert, TrackedItem

# Assumed sales per item per month, used only for the "profit at risk"
# estimate on the dashboard. Stated in the output wherever it is used.
ASSUMED_MONTHLY_SALES = 10

# Unicode blocks for the ASCII sparkline, lowest to highest.
SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int]) -> str:
    """ASCII sparkline of a list of integer-cent prices.

    A single value (or an empty list) renders as a flat line.
    """
    if not values:
        return ""
    low, high = min(values), max(values)
    if low == high:
        return SPARK_CHARS[3] * len(values)
    span = high - low
    return "".join(
        SPARK_CHARS[min(7, (value - low) * 7 // span)] for value in values
    )


def at_risk_skus(alerts: list[Alert]) -> set[str]:
    """SKUs carrying a critical or warning alert."""
    return {a.sku for a in alerts if a.severity in ("critical", "warning")}


def monthly_profit_at_risk_cents(
    items: list[TrackedItem],
    at_risk: set[str],
    fee_percent: Decimal,
    fixed_fee_cents: int,
) -> int:
    """Estimated profit at risk per month for at-risk items.

    - Items losing money per sale: the loss per unit x assumed sales.
    - Items out of stock: the profit that would have been made per
      unit x assumed sales (sales that cannot happen).
    """
    total = 0
    for item in items:
        if item.sku not in at_risk:
            continue
        profit = net_profit_cents(
            item.ebay_price_cents,
            item.supplier_price_cents,
            item.supplier_shipping_cents,
            fee_percent,
            fixed_fee_cents,
        )
        if profit < 0:
            total += -profit * ASSUMED_MONTHLY_SALES
        elif item.stock_status == "out_of_stock":
            total += profit * ASSUMED_MONTHLY_SALES
    return total


def dashboard_text(
    items: list[TrackedItem],
    today: str,
    fee_percent: Decimal,
    fixed_fee_cents: int,
) -> str:
    """The full dashboard as human-readable terminal text."""
    alerts = compute_alerts(items, today, fee_percent, fixed_fee_cents)
    at_risk = at_risk_skus(alerts)
    counts = {"critical": 0, "warning": 0, "info": 0}
    for alert in alerts:
        counts[alert.severity] += 1
    risk_cents = monthly_profit_at_risk_cents(
        items, at_risk, fee_percent, fixed_fee_cents
    )
    scorecard = supplier_scorecard(items, today)
    attention = [row for row in scorecard if row["reliability"] < 100]

    lines = [
        f"eBay Supplier Price Monitor — dashboard ({today})",
        "=" * 60,
        f"Tracked items:        {len(items)}",
        f"Items at risk:        {len(at_risk)}",
        f"Alerts:               {counts['critical']} critical, "
        f"{counts['warning']} warning, {counts['info']} info",
        f"Monthly profit at risk: {format_money(risk_cents)} "
        f"(assumes {ASSUMED_MONTHLY_SALES} sales/month per item)",
        "",
        "Suppliers needing attention:",
    ]
    if attention:
        for row in attention:
            lines.append(
                f"  - {row['supplier']}: reliability {row['reliability']}/100, "
                f"{row['stock_outs_30d']} stock-out(s) in 30 days, "
                f"avg drift {row['avg_drift_percent']:+.1f}%"
            )
    else:
        lines.append("  None — all suppliers at 100/100 reliability.")
    lines.append("")
    lines.append("Top alerts:")
    if alerts:
        for alert in alerts[:5]:
            lines.append(f"  {alert.display().replace(chr(10), chr(10) + '  ')}")
    else:
        lines.append("  No alerts. Everything looks healthy.")
    return "\n".join(lines)


def history_text(item: TrackedItem, fee_percent: Decimal, fixed_fee_cents: int) -> str:
    """Price-history table plus an ASCII sparkline for one item."""
    lines = [
        f"Price history — {item.sku} ({item.title})",
        f"eBay price: {format_money(item.ebay_price_cents)}  "
        f"supplier: {item.supplier}  "
        f"current margin: "
        f"{margin_percent(item.ebay_price_cents, item.supplier_price_cents, item.supplier_shipping_cents, fee_percent, fixed_fee_cents):.1f}%",
        "",
        f"{'Date':12} {'Supplier price':>14} {'Shipping':>10}  {'Stock':12} Source",
        "-" * 62,
    ]
    for entry in item.history:
        lines.append(
            f"{entry.date:12} {format_money(entry.supplier_price_cents):>14} "
            f"{format_money(entry.shipping_cents):>10}  {entry.stock_status:12} {entry.source}"
        )
    if not item.history:
        lines.append("(no checks recorded yet)")
    else:
        prices = [entry.supplier_price_cents for entry in item.history]
        lines.append("")
        lines.append(f"Supplier price trend: {sparkline(prices)}")
        lines.append(
            f"  low {format_money(min(prices))}  "
            f"high {format_money(max(prices))}  "
            f"latest {format_money(prices[-1])}"
        )
    return "\n".join(lines)


def export_csv(items: list[TrackedItem], path: str, today: str,
               fee_percent: Decimal, fixed_fee_cents: int) -> None:
    """Export the tracked-items list with live margin figures to CSV."""
    fieldnames = [
        "sku", "title", "supplier", "ebay_price", "supplier_price",
        "supplier_shipping", "stock_status", "margin_percent",
        "profit_per_unit", "min_margin_percent", "last_checked",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            profit = net_profit_cents(
                item.ebay_price_cents, item.supplier_price_cents,
                item.supplier_shipping_cents, fee_percent, fixed_fee_cents,
            )
            margin = margin_percent(
                item.ebay_price_cents, item.supplier_price_cents,
                item.supplier_shipping_cents, fee_percent, fixed_fee_cents,
            )
            writer.writerow({
                "sku": item.sku,
                "title": item.title,
                "supplier": item.supplier,
                "ebay_price": f"{Decimal(item.ebay_price_cents) / 100:.2f}",
                "supplier_price": f"{Decimal(item.supplier_price_cents) / 100:.2f}",
                "supplier_shipping": f"{Decimal(item.supplier_shipping_cents) / 100:.2f}",
                "stock_status": item.stock_status,
                "margin_percent": f"{margin:.1f}",
                "profit_per_unit": f"{Decimal(profit) / 100:.2f}",
                "min_margin_percent": item.min_margin_percent,
                "last_checked": item.last_checked,
            })


def export_json(items: list[TrackedItem], path: str, today: str,
                fee_percent: Decimal, fixed_fee_cents: int) -> None:
    """Export the tracked-items list with live margin figures to JSON."""
    payload = {
        "generated": today,
        "items": [],
    }
    for item in items:
        profit = net_profit_cents(
            item.ebay_price_cents, item.supplier_price_cents,
            item.supplier_shipping_cents, fee_percent, fixed_fee_cents,
        )
        margin = margin_percent(
            item.ebay_price_cents, item.supplier_price_cents,
            item.supplier_shipping_cents, fee_percent, fixed_fee_cents,
        )
        record = item.to_dict()
        record["profit_per_unit_cents"] = profit
        record["margin_percent"] = round(margin, 1)
        payload["items"].append(record)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def export_html(items: list[TrackedItem], path: str, today: str,
                fee_percent: Decimal, fixed_fee_cents: int) -> None:
    """Export a self-contained HTML report (inline CSS, no JavaScript)."""
    alerts = compute_alerts(items, today, fee_percent, fixed_fee_cents)
    at_risk = at_risk_skus(alerts)
    scorecard = supplier_scorecard(items, today)

    def esc(value: object) -> str:
        return html_lib.escape(str(value))

    item_rows = []
    for item in items:
        profit = net_profit_cents(
            item.ebay_price_cents, item.supplier_price_cents,
            item.supplier_shipping_cents, fee_percent, fixed_fee_cents,
        )
        margin = margin_percent(
            item.ebay_price_cents, item.supplier_price_cents,
            item.supplier_shipping_cents, fee_percent, fixed_fee_cents,
        )
        row_class = "risk" if item.sku in at_risk else ""
        item_rows.append(
            "<tr class=\"{cls}\"><td>{sku}</td><td>{title}</td>"
            "<td>{supplier}</td><td>{ebay}</td><td>{supp}</td>"
            "<td>{ship}</td><td>{stock}</td><td>{margin:.1f}%</td>"
            "<td>{profit}</td><td>{checked}</td></tr>".format(
                cls=row_class, sku=esc(item.sku), title=esc(item.title),
                supplier=esc(item.supplier),
                ebay=esc(format_money(item.ebay_price_cents)),
                supp=esc(format_money(item.supplier_price_cents)),
                ship=esc(format_money(item.supplier_shipping_cents)),
                stock=esc(item.stock_status), margin=margin,
                profit=esc(format_money(profit)),
                checked=esc(item.last_checked or "never"),
            )
        )

    alert_items = "".join(
        "<li class=\"{sev}\"><strong>[{sev}] {sku}</strong> — {msg}<br>"
        "<em>Action: {action}</em></li>".format(
            sev=esc(a.severity), sku=esc(a.sku),
            msg=esc(a.message), action=esc(a.action),
        )
        for a in alerts
    ) or "<li>No alerts.</li>"

    supplier_rows = "".join(
        "<tr><td>{supplier}</td><td>{items}</td><td>{checks}</td>"
        "<td>{outs}</td><td>{drift:+.1f}%</td><td>{rel}/100</td></tr>".format(
            supplier=esc(row["supplier"]), items=row["items"],
            checks=row["checks_30d"], outs=row["stock_outs_30d"],
            drift=row["avg_drift_percent"], rel=row["reliability"],
        )
        for row in scorecard
    )

    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>eBay Supplier Price Monitor — report {esc(today)}</title>
<style>
body {{ font-family: Arial, Helvetica, sans-serif; margin: 2em; color: #222; }}
h1 {{ color: #0b3d91; }} h2 {{ color: #0b3d91; margin-top: 1.5em; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; font-size: 14px; }}
th {{ background: #0b3d91; color: #fff; }}
tr.risk td {{ background: #fdecea; }}
li.critical {{ color: #b00020; }} li.warning {{ color: #8a6d00; }} li.info {{ color: #444; }}
footer {{ margin-top: 2em; color: #666; font-size: 12px; }}
</style>
</head>
<body>
<h1>eBay Supplier Price Monitor</h1>
<p>Report date: {esc(today)} &middot; Tracked items: {len(items)} &middot;
Items at risk: {len(at_risk)} &middot; Alerts: {len(alerts)}</p>

<h2>Tracked items</h2>
<table>
<tr><th>SKU</th><th>Title</th><th>Supplier</th><th>eBay price</th>
<th>Supplier price</th><th>Shipping</th><th>Stock</th><th>Margin</th>
<th>Profit/unit</th><th>Last checked</th></tr>
{''.join(item_rows)}
</table>

<h2>Alerts</h2>
<ul>
{alert_items}
</ul>

<h2>Supplier scorecard</h2>
<table>
<tr><th>Supplier</th><th>Items</th><th>Checks (30d)</th>
<th>Stock-outs (30d)</th><th>Avg drift</th><th>Reliability</th></tr>
{supplier_rows}
</table>

<footer>Generated by eBay Supplier Price Monitor. Profit-at-risk assumes
10 sales per month per item.</footer>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(document)
