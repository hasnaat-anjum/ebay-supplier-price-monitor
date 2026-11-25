"""Best-effort supplier price fetching.

Downloads the supplier product page with urllib and looks for
schema.org JSON-LD ``<script type="application/ld+json">`` blocks,
reading ``offers.price`` and ``offers.availability``. This works on
many Amazon / Walmart / AliExpress-style product pages without any
API key — but it is deliberately best-effort: any failure (no URL,
network error, no JSON-LD, no price) returns a FetchResult marked as
failed instead of raising, and the CLI records a failed-check note.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
TIMEOUT_SECONDS = 10

# Matches a JSON-LD script block, capturing its contents.
JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# schema.org availability values -> our stock statuses.
AVAILABILITY_MAP = {
    "instock": "in_stock",
    "outofstock": "out_of_stock",
    "limitedavailability": "low_stock",
    "preorder": "low_stock",
    "discontinued": "out_of_stock",
}


@dataclass
class FetchResult:
    """Outcome of one fetch attempt."""

    ok: bool
    price_cents: int | None = None
    stock_status: str = "unknown"
    message: str = ""


def _normalise_availability(value: object) -> str:
    """Turn a schema.org availability value (URL or short name) into
    one of our stock statuses."""
    if not isinstance(value, str):
        return "unknown"
    tail = value.rsplit("/", 1)[-1].strip().lower()
    return AVAILABILITY_MAP.get(tail, "unknown")


def _parse_price(raw: object) -> int | None:
    """Parse a JSON-LD price (number or string) into integer cents."""
    if raw is None:
        return None
    try:
        amount = Decimal(str(raw).strip().lstrip("$"))
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return int((amount * 100).to_integral_value())


def _offers_from_jsonld(data: object) -> list[dict]:
    """Collect every ``offers`` object found in a parsed JSON-LD
    document (which may be a dict, a list, or a @graph)."""
    offers: list[dict] = []
    if isinstance(data, dict):
        if isinstance(data.get("offers"), dict):
            offers.append(data["offers"])
        elif isinstance(data.get("offers"), list):
            offers.extend(o for o in data["offers"] if isinstance(o, dict))
        graph = data.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                offers.extend(_offers_from_jsonld(node))
    elif isinstance(data, list):
        for node in data:
            offers.extend(_offers_from_jsonld(node))
    return offers


def parse_jsonld_prices(page_html: str) -> FetchResult:
    """Extract price + availability from the JSON-LD blocks in a page.

    Returns a failed FetchResult when no usable offer is found.
    """
    blocks = JSON_LD_RE.findall(page_html)
    if not blocks:
        return FetchResult(ok=False, message="no JSON-LD data found on the page")
    for block in blocks:
        try:
            data = json.loads(html.unescape(block.strip()))
        except json.JSONDecodeError:
            continue
        for offer in _offers_from_jsonld(data):
            price = _parse_price(offer.get("price"))
            if price is None:
                continue
            stock = _normalise_availability(offer.get("availability"))
            return FetchResult(
                ok=True,
                price_cents=price,
                stock_status=stock,
                message="price found in JSON-LD offers",
            )
    return FetchResult(ok=False, message="no price in the page's JSON-LD data")


def fetch_supplier_price(url: str, timeout: int = TIMEOUT_SECONDS) -> FetchResult:
    """GET a supplier product page and parse its JSON-LD price.

    Never raises for ordinary failures — network errors, timeouts and
    unparseable pages all come back as a failed FetchResult.
    """
    if not url:
        return FetchResult(ok=False, message="no supplier URL set for this item")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return FetchResult(ok=False, message=f"supplier page returned HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return FetchResult(ok=False, message=f"could not reach supplier page: {exc.reason}")
    except TimeoutError:
        return FetchResult(ok=False, message="supplier page timed out")
    except OSError as exc:
        return FetchResult(ok=False, message=f"network error: {exc}")
    try:
        page_html = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - defensive
        return FetchResult(ok=False, message=f"could not decode supplier page: {exc}")
    return parse_jsonld_prices(page_html)
