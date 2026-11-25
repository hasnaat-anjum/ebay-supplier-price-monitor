"""Tests for fetch.py — JSON-LD price parsing and HTTP fetching.

No real network calls: HTTP is faked with unittest.mock.
"""

import io
import unittest
import urllib.error
from unittest import mock

from supplier_monitor.fetch import fetch_supplier_price, parse_jsonld_prices

PAGE_WITH_PRICE = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product", "name": "Widget",
 "offers": {"@type": "Offer", "price": "12.34",
            "availability": "https://schema.org/InStock"}}
</script>
</head><body></body></html>
"""

PAGE_OUT_OF_STOCK = """
<script type="application/ld+json">
{"@type": "Product", "offers": {"price": 9.99,
 "availability": "https://schema.org/OutOfStock"}}
</script>
"""

PAGE_LIMITED = """
<script type="application/ld+json">
{"@type": "Product", "offers": {"price": "5.00",
 "availability": "LimitedAvailability"}}
</script>
"""

PAGE_GRAPH = """
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "WebPage", "name": "Shop"},
  {"@type": "Product", "offers": {"price": "7.50",
   "availability": "https://schema.org/InStock"}}
]}
</script>
"""

PAGE_NO_PRICE = """
<script type="application/ld+json">
{"@type": "Product", "name": "No offer here"}
</script>
"""

PAGE_NO_JSONLD = "<html><body>Nothing structured here.</body></html>"


class FakeResponse:
    def __init__(self, text):
        self._data = text.encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestParseJsonLd(unittest.TestCase):
    def test_price_and_in_stock(self):
        result = parse_jsonld_prices(PAGE_WITH_PRICE)
        self.assertTrue(result.ok)
        self.assertEqual(result.price_cents, 1234)
        self.assertEqual(result.stock_status, "in_stock")

    def test_out_of_stock(self):
        result = parse_jsonld_prices(PAGE_OUT_OF_STOCK)
        self.assertTrue(result.ok)
        self.assertEqual(result.price_cents, 999)
        self.assertEqual(result.stock_status, "out_of_stock")

    def test_limited_availability_is_low_stock(self):
        result = parse_jsonld_prices(PAGE_LIMITED)
        self.assertTrue(result.ok)
        self.assertEqual(result.stock_status, "low_stock")

    def test_graph_layout(self):
        result = parse_jsonld_prices(PAGE_GRAPH)
        self.assertTrue(result.ok)
        self.assertEqual(result.price_cents, 750)

    def test_no_price_fails(self):
        result = parse_jsonld_prices(PAGE_NO_PRICE)
        self.assertFalse(result.ok)

    def test_no_jsonld_fails(self):
        result = parse_jsonld_prices(PAGE_NO_JSONLD)
        self.assertFalse(result.ok)
        self.assertIn("no JSON-LD", result.message)

    def test_broken_jsonld_block_is_skipped(self):
        page = "<script type=\"application/ld+json\">{broken</script>" + PAGE_WITH_PRICE
        result = parse_jsonld_prices(page)
        self.assertTrue(result.ok)
        self.assertEqual(result.price_cents, 1234)


class TestFetchSupplierPrice(unittest.TestCase):
    def test_successful_fetch(self):
        with mock.patch(
            "supplier_monitor.fetch.urllib.request.urlopen",
            return_value=FakeResponse(PAGE_WITH_PRICE),
        ):
            result = fetch_supplier_price("https://example.com/product")
        self.assertTrue(result.ok)
        self.assertEqual(result.price_cents, 1234)

    def test_http_error_is_a_failed_result_not_a_crash(self):
        with mock.patch(
            "supplier_monitor.fetch.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "https://example.com", 404, "Not Found", {}, io.BytesIO(b"")
            ),
        ):
            result = fetch_supplier_price("https://example.com/product")
        self.assertFalse(result.ok)
        self.assertIn("404", result.message)

    def test_network_error_is_a_failed_result(self):
        with mock.patch(
            "supplier_monitor.fetch.urllib.request.urlopen",
            side_effect=urllib.error.URLError("no route to host"),
        ):
            result = fetch_supplier_price("https://example.com/product")
        self.assertFalse(result.ok)

    def test_missing_url_fails_cleanly(self):
        result = fetch_supplier_price("")
        self.assertFalse(result.ok)
        self.assertIn("no supplier URL", result.message)

    def test_sends_desktop_user_agent(self):
        with mock.patch(
            "supplier_monitor.fetch.urllib.request.urlopen",
            return_value=FakeResponse(PAGE_WITH_PRICE),
        ) as fake_open:
            fetch_supplier_price("https://example.com/product")
        request = fake_open.call_args[0][0]
        self.assertIn("Mozilla/5.0", request.get_header("User-agent"))


if __name__ == "__main__":
    unittest.main()
