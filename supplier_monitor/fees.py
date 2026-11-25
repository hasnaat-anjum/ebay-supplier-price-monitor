"""Money and margin math for eBay Supplier Price Monitor.

All money is handled in INTEGER CENTS internally — never floats — so
profit and margin figures are exact. Dollar amounts only exist at the
edges (user input and display).
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

DEFAULT_FEE_PERCENT = Decimal("13.25")
DEFAULT_FIXED_FEE_CENTS = 30  # $0.30 per order


def parse_money(value: str) -> int:
    """Parse a dollar amount such as "24.99" into integer cents.

    Accepts an optional leading "$" and surrounding whitespace.
    Raises ValueError on anything that is not a non-negative amount
    with at most two decimal places.
    """
    if value is None:
        raise ValueError("missing amount")
    text = str(value).strip().lstrip("$").strip()
    if not text:
        raise ValueError("empty amount")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"not a valid amount: {value!r}") from None
    if amount < 0:
        raise ValueError(f"amount must not be negative: {value!r}")
    if -amount.as_tuple().exponent > 2:
        raise ValueError(f"amount has more than two decimal places: {value!r}")
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def format_money(cents: int) -> str:
    """Format integer cents as a dollar string, e.g. 2499 -> "$24.99"."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100}.{cents % 100:02d}"


def ebay_fee_cents(
    ebay_price_cents: int,
    fee_percent: Decimal = DEFAULT_FEE_PERCENT,
    fixed_fee_cents: int = DEFAULT_FIXED_FEE_CENTS,
) -> int:
    """eBay final-value fee for one order: fee% of the sale price
    (rounded half-up to the nearest cent) plus the fixed per-order fee."""
    percent_part = (
        Decimal(ebay_price_cents) * fee_percent / Decimal(100)
    ).to_integral_value(rounding=ROUND_HALF_UP)
    return int(percent_part) + int(fixed_fee_cents)


def net_profit_cents(
    ebay_price_cents: int,
    supplier_price_cents: int,
    shipping_cents: int,
    fee_percent: Decimal = DEFAULT_FEE_PERCENT,
    fixed_fee_cents: int = DEFAULT_FIXED_FEE_CENTS,
) -> int:
    """Net profit per unit sold: eBay price minus eBay fee, supplier
    price and supplier shipping."""
    fee = ebay_fee_cents(ebay_price_cents, fee_percent, fixed_fee_cents)
    return ebay_price_cents - fee - supplier_price_cents - shipping_cents


def margin_percent(
    ebay_price_cents: int,
    supplier_price_cents: int,
    shipping_cents: int,
    fee_percent: Decimal = DEFAULT_FEE_PERCENT,
    fixed_fee_cents: int = DEFAULT_FIXED_FEE_CENTS,
) -> float:
    """Net margin as a percentage of the eBay price.

    Returns 0.0 for a zero/negative eBay price instead of dividing by zero.
    """
    if ebay_price_cents <= 0:
        return 0.0
    profit = net_profit_cents(
        ebay_price_cents, supplier_price_cents, shipping_cents,
        fee_percent, fixed_fee_cents,
    )
    return profit / ebay_price_cents * 100.0


def suggested_ebay_price_cents(
    supplier_price_cents: int,
    shipping_cents: int,
    min_margin_percent: float,
    fee_percent: Decimal = DEFAULT_FEE_PERCENT,
    fixed_fee_cents: int = DEFAULT_FIXED_FEE_CENTS,
) -> int | None:
    """The eBay price that restores the minimum margin.

    Solved from: price - fee%(price) - fixed - supplier - shipping
    = min_margin%(price), i.e. price = (supplier + shipping + fixed)
    / (1 - fee% - min_margin%).

    Returns None when no price can restore the margin (the fee and the
    required margin already add up to 100% or more of the price).
    The result is rounded UP to the next cent so the restored margin
    is never below the floor because of rounding.
    """
    numerator = Decimal(supplier_price_cents + shipping_cents + fixed_fee_cents)
    denominator = (
        Decimal(1)
        - fee_percent / Decimal(100)
        - Decimal(str(min_margin_percent)) / Decimal(100)
    )
    if denominator <= 0:
        return None
    price = (numerator / denominator).to_integral_value(rounding=ROUND_CEILING)
    return int(price)


def is_margin_unrecoverable(
    current_ebay_price_cents: int,
    suggested_price_cents: int | None,
) -> bool:
    """True when the price needed to restore the margin is more than
    three times the current eBay price (or no price can restore it)."""
    if suggested_price_cents is None:
        return True
    if current_ebay_price_cents <= 0:
        return True
    return suggested_price_cents > 3 * current_ebay_price_cents
