"""Alert rules and the supplier scorecard.

Alerts are computed fresh from the current store every time they are
asked for — nothing is cached or stored. Every alert carries a concrete
suggested action so the seller knows exactly what to do next.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .fees import (
    format_money,
    is_margin_unrecoverable,
    margin_percent,
    suggested_ebay_price_cents,
)
from .models import Alert, TrackedItem

# How much of a supplier price rise (in percent) counts as a "big" rise
# for the reliability score.
BIG_RISE_PERCENT = 10.0


def _days_between(earlier: str, later: str) -> int:
    """Days from one YYYY-MM-DD date to another (negative if reversed)."""
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days


def _previous_check(item: TrackedItem) -> "object | None":
    """The check before the most recent one, or None."""
    return item.history[-2] if len(item.history) >= 2 else None


def compute_alerts(
    items: list[TrackedItem],
    today: str,
    fee_percent: Decimal,
    fixed_fee_cents: int,
) -> list[Alert]:
    """Compute every current alert for the given items, most severe
    first. ``today`` is a YYYY-MM-DD date string."""
    alerts: list[Alert] = []

    for item in items:
        margin = margin_percent(
            item.ebay_price_cents,
            item.supplier_price_cents,
            item.supplier_shipping_cents,
            fee_percent,
            fixed_fee_cents,
        )
        suggested = suggested_ebay_price_cents(
            item.supplier_price_cents,
            item.supplier_shipping_cents,
            item.min_margin_percent,
            fee_percent,
            fixed_fee_cents,
        )

        # -- stock ------------------------------------------------------
        if item.stock_status == "out_of_stock":
            alerts.append(Alert(
                sku=item.sku,
                severity="critical",
                kind="stock_out",
                message=f"{item.title} is OUT OF STOCK at {item.supplier}.",
                action="Pause listing until restocked",
            ))
        elif item.stock_status == "low_stock":
            alerts.append(Alert(
                sku=item.sku,
                severity="warning",
                kind="low_stock",
                message=f"{item.title} is LOW STOCK at {item.supplier}.",
                action="Re-check supplier",
            ))

        # -- margin ------------------------------------------------------
        if margin < item.min_margin_percent:
            if is_margin_unrecoverable(item.ebay_price_cents, suggested):
                alerts.append(Alert(
                    sku=item.sku,
                    severity="critical",
                    kind="margin_breach",
                    message=(
                        f"Margin on {item.title} is {margin:.1f}%, below your "
                        f"{item.min_margin_percent:.0f}% floor — and no price "
                        f"can restore it."
                    ),
                    action="Find a new supplier",
                ))
            else:
                alerts.append(Alert(
                    sku=item.sku,
                    severity="warning",
                    kind="margin_breach",
                    message=(
                        f"Margin on {item.title} is {margin:.1f}%, below your "
                        f"{item.min_margin_percent:.0f}% floor."
                    ),
                    action=f"Raise eBay price to {format_money(suggested)}",
                ))

        # -- price movement since the previous check --------------------
        prev = _previous_check(item)
        if prev is not None and prev.supplier_price_cents > 0:
            change = (
                (item.supplier_price_cents - prev.supplier_price_cents)
                / prev.supplier_price_cents
                * 100.0
            )
            if change > 0:
                if margin < item.min_margin_percent:
                    alerts.append(Alert(
                        sku=item.sku,
                        severity="critical",
                        kind="price_rise",
                        message=(
                            f"Supplier price for {item.title} rose "
                            f"{format_money(prev.supplier_price_cents)} -> "
                            f"{format_money(item.supplier_price_cents)} "
                            f"(+{change:.1f}%) and wiped out the margin."
                        ),
                        action=(
                            f"Raise eBay price to {format_money(suggested)}"
                            if suggested is not None
                            and not is_margin_unrecoverable(
                                item.ebay_price_cents, suggested)
                            else "Find a new supplier"
                        ),
                    ))
                else:
                    alerts.append(Alert(
                        sku=item.sku,
                        severity="warning",
                        kind="price_rise",
                        message=(
                            f"Supplier price for {item.title} rose "
                            f"{format_money(prev.supplier_price_cents)} -> "
                            f"{format_money(item.supplier_price_cents)} "
                            f"(+{change:.1f}%)."
                        ),
                        action="Re-check supplier",
                    ))
            elif change < 0:
                alerts.append(Alert(
                    sku=item.sku,
                    severity="info",
                    kind="price_drop",
                    message=(
                        f"Supplier price for {item.title} dropped "
                        f"{format_money(prev.supplier_price_cents)} -> "
                        f"{format_money(item.supplier_price_cents)} "
                        f"({change:.1f}%)."
                    ),
                    action="Re-check supplier",
                ))

        # -- stale check --------------------------------------------------
        if item.last_checked:
            stale_days = _days_between(item.last_checked, today)
            if stale_days > item.check_interval_days:
                alerts.append(Alert(
                    sku=item.sku,
                    severity="warning",
                    kind="stale_check",
                    message=(
                        f"{item.title} has not been checked for {stale_days} "
                        f"days (interval is {item.check_interval_days} days)."
                    ),
                    action="Re-check supplier",
                ))
        else:
            alerts.append(Alert(
                sku=item.sku,
                severity="warning",
                kind="stale_check",
                message=f"{item.title} has never been checked.",
                action="Re-check supplier",
            ))

    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (severity_rank[a.severity], a.sku, a.kind))
    return alerts


def supplier_scorecard(
    items: list[TrackedItem],
    today: str,
) -> list[dict]:
    """Per-supplier scorecard, sorted by reliability score (best first).

    Reliability starts at 100 and loses 10 points per stock-out event
    and 5 points per price rise of more than 10% in the last 30 days
    (floor 0). Price drift is the average percentage change in supplier
    price over the last 30 days, per item, averaged across the
    supplier's items.
    """
    window_start = date.fromisoformat(today) - timedelta(days=30)
    by_supplier: dict[str, list[TrackedItem]] = {}
    for item in items:
        by_supplier.setdefault(item.supplier, []).append(item)

    rows = []
    for supplier, supplier_items in by_supplier.items():
        checks = 0
        stock_outs = 0
        big_rises = 0
        drifts: list[float] = []
        for item in supplier_items:
            recent = [
                c for c in item.history
                if date.fromisoformat(c.date) >= window_start
            ]
            checks += len(recent)
            stock_outs += sum(
                1 for c in recent if c.stock_status == "out_of_stock"
            )
            if len(recent) >= 2 and recent[0].supplier_price_cents > 0:
                drift = (
                    (recent[-1].supplier_price_cents - recent[0].supplier_price_cents)
                    / recent[0].supplier_price_cents
                    * 100.0
                )
                drifts.append(drift)
                if drift > BIG_RISE_PERCENT:
                    big_rises += 1
        reliability = max(0, 100 - 10 * stock_outs - 5 * big_rises)
        rows.append({
            "supplier": supplier,
            "items": len(supplier_items),
            "checks_30d": checks,
            "stock_outs_30d": stock_outs,
            "avg_drift_percent": (
                sum(drifts) / len(drifts) if drifts else 0.0
            ),
            "reliability": reliability,
        })
    rows.sort(key=lambda row: (-row["reliability"], row["supplier"]))
    return rows
