"""Data models: tracked items, price-check history and alerts.

Items are plain dataclasses with validation in ``__post_init__`` so a
bad record is rejected at construction time, wherever it comes from
(CLI input, CSV import or the JSON store).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .fees import format_money

# Supplier names the tool knows about; anything else is stored as "Other".
KNOWN_SUPPLIERS = ("Amazon", "AliExpress", "Walmart", "eBay Wholesale", "Other")

STOCK_STATUSES = ("in_stock", "low_stock", "out_of_stock", "unknown")

SEVERITIES = ("critical", "warning", "info")


def validate_iso_date(value: str, field_name: str = "date") -> str:
    """Return the value unchanged if it is a valid YYYY-MM-DD date,
    otherwise raise ValueError."""
    try:
        date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(
            f"{field_name} must be a date in YYYY-MM-DD format, got {value!r}"
        ) from None
    return value


@dataclass
class PriceCheck:
    """One recorded supplier check: the supplier price, shipping and
    stock status observed on a given date, plus where it came from."""

    date: str  # YYYY-MM-DD
    supplier_price_cents: int
    shipping_cents: int = 0
    stock_status: str = "unknown"
    source: str = "manual"  # manual | feed | fetch | demo

    def __post_init__(self) -> None:
        validate_iso_date(self.date, "check date")
        if self.supplier_price_cents < 0:
            raise ValueError("supplier price must not be negative")
        if self.shipping_cents < 0:
            raise ValueError("shipping cost must not be negative")
        if self.stock_status not in STOCK_STATUSES:
            raise ValueError(
                f"stock status must be one of {', '.join(STOCK_STATUSES)}, "
                f"got {self.stock_status!r}"
            )

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "supplier_price_cents": self.supplier_price_cents,
            "shipping_cents": self.shipping_cents,
            "stock_status": self.stock_status,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PriceCheck":
        return cls(
            date=data["date"],
            supplier_price_cents=int(data["supplier_price_cents"]),
            shipping_cents=int(data.get("shipping_cents", 0)),
            stock_status=data.get("stock_status", "unknown"),
            source=data.get("source", "manual"),
        )


@dataclass
class TrackedItem:
    """One product whose supplier price and stock are being watched."""

    sku: str
    title: str
    ebay_price_cents: int
    supplier: str
    supplier_price_cents: int
    supplier_shipping_cents: int = 0
    stock_status: str = "unknown"
    ebay_item_id: str = ""
    supplier_url: str = ""
    min_margin_percent: float = 15.0
    check_interval_days: int = 7
    notes: str = ""
    date_added: str = ""
    last_checked: str = ""
    history: list = field(default_factory=list)  # list[PriceCheck]

    def __post_init__(self) -> None:
        self.sku = self.sku.strip()
        if not self.sku:
            raise ValueError("SKU must not be empty")
        if not self.title.strip():
            raise ValueError("title must not be empty")
        if self.ebay_price_cents <= 0:
            raise ValueError("eBay price must be greater than zero")
        if self.supplier_price_cents < 0:
            raise ValueError("supplier price must not be negative")
        if self.supplier_shipping_cents < 0:
            raise ValueError("supplier shipping cost must not be negative")
        if self.supplier not in KNOWN_SUPPLIERS:
            self.supplier = "Other"
        if self.stock_status not in STOCK_STATUSES:
            raise ValueError(
                f"stock status must be one of {', '.join(STOCK_STATUSES)}, "
                f"got {self.stock_status!r}"
            )
        if not 0 <= self.min_margin_percent < 100:
            raise ValueError("minimum margin % must be between 0 and 100")
        if self.check_interval_days < 1:
            raise ValueError("check interval must be at least 1 day")
        if self.date_added:
            validate_iso_date(self.date_added, "date added")
        if self.last_checked:
            validate_iso_date(self.last_checked, "last checked date")
        # Normalise history entries given as plain dicts (from JSON).
        normalised = []
        for entry in self.history:
            normalised.append(
                entry if isinstance(entry, PriceCheck) else PriceCheck.from_dict(entry)
            )
        self.history = normalised

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "title": self.title,
            "ebay_item_id": self.ebay_item_id,
            "ebay_price_cents": self.ebay_price_cents,
            "supplier": self.supplier,
            "supplier_url": self.supplier_url,
            "supplier_price_cents": self.supplier_price_cents,
            "supplier_shipping_cents": self.supplier_shipping_cents,
            "stock_status": self.stock_status,
            "min_margin_percent": self.min_margin_percent,
            "check_interval_days": self.check_interval_days,
            "notes": self.notes,
            "date_added": self.date_added,
            "last_checked": self.last_checked,
            "history": [entry.to_dict() for entry in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackedItem":
        return cls(
            sku=data["sku"],
            title=data["title"],
            ebay_item_id=data.get("ebay_item_id", ""),
            ebay_price_cents=int(data["ebay_price_cents"]),
            supplier=data.get("supplier", "Other"),
            supplier_url=data.get("supplier_url", ""),
            supplier_price_cents=int(data["supplier_price_cents"]),
            supplier_shipping_cents=int(data.get("supplier_shipping_cents", 0)),
            stock_status=data.get("stock_status", "unknown"),
            min_margin_percent=float(data.get("min_margin_percent", 15.0)),
            check_interval_days=int(data.get("check_interval_days", 7)),
            notes=data.get("notes", ""),
            date_added=data.get("date_added", ""),
            last_checked=data.get("last_checked", ""),
            history=data.get("history", []),
        )

    def display(self) -> str:
        """One-line summary for terminal listings."""
        return (
            f"{self.sku:22} {self.title[:38]:38} "
            f"eBay {format_money(self.ebay_price_cents):>8}  "
            f"supplier {format_money(self.supplier_price_cents):>8}  "
            f"{self.supplier:14} {self.stock_status}"
        )


@dataclass
class Alert:
    """One computed alert for a tracked item. Alerts are always
    computed fresh from the current data — they are never stored."""

    sku: str
    severity: str  # critical | warning | info
    kind: str  # price_rise | margin_breach | stock_out | low_stock | stale_check | price_drop
    message: str
    action: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")

    def display(self) -> str:
        marker = {"critical": "[CRITICAL]", "warning": "[WARNING] ", "info": "[INFO]    "}[self.severity]
        return f"{marker} {self.sku}: {self.message}\n           -> {self.action}"
