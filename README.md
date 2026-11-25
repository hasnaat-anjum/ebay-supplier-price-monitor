# eBay Supplier Price Monitor

Watch your suppliers' prices and stock so a price rise never eats your eBay margin — in one free, offline tool. No API key, no monthly fee, no account.

## The problem it solves

eBay dropshippers lose money when a supplier raises a price or runs out of stock: the eBay listing keeps selling at the old price, and the seller only finds out when the profit is already gone. The tools that watch for this — Ecomli ($29/month), AutoDS ($49.90/month), PriceYak and DSM Tool — are all paid SaaS, from ~$29/month. There is no free, local, open-source eBay-focused supplier monitor.

This tool fills that gap: track your products, record supplier checks by hand, from a price-feed CSV, or straight from the supplier's product page, and get a clear alert — with the exact new eBay price that restores your margin — before the next sale.

## What it does

- **Tracked items** — SKU, title, eBay item ID, eBay price, supplier (Amazon / AliExpress / Walmart / eBay Wholesale / Other), supplier URL, supplier price + shipping, stock status, minimum margin % (default 15%), check interval (default 7 days), notes, and a full price history per item.
- **Exact margin math** — money is handled in integer cents, never floats. eBay fee is 13.25% + $0.30 per order by default and configurable with `config`. Net profit = eBay price − eBay fee − supplier price − supplier shipping.
- **Suggested eBay price** — the price that restores your minimum margin, worked out exactly. If it would take more than 3x your current price, the tool says so plainly: "margin unrecoverable — consider delisting or a new supplier".
- **Three ways to record a check** — manual entry (`check`), a supplier price-feed CSV (`check-feed`), or live fetching from the supplier's product page (`fetch`), which reads schema.org JSON-LD price and availability data. A failed fetch is recorded as a note, never a crash.
- **Alerts** — supplier price increased, margin below your floor, stock out (critical), low stock, stale checks, and a price rise that wiped out the margin. Every alert carries a concrete action: "Raise eBay price to $26.21", "Pause listing until restocked", "Find a new supplier", or "Re-check supplier".
- **Supplier scorecard** — items, 30-day price drift, stock-out events, checks, and a 0–100 reliability score (starts at 100, −10 per stock-out, −5 per price rise over 10%).
- **Dashboard** — tracked items, items at risk, alerts by severity, and monthly profit at risk (assumes 10 sales/month per item — stated in the output).
- **History + sparkline** — per-item price history table with an ASCII trend sparkline.
- **Exports** — CSV, JSON, or a self-contained HTML report (inline CSS, no JavaScript).
- **CSV import/export** — import your tracked-items list or a supplier feed, with tolerant header aliases ("Item SKU", "Supplier price", "Stock status"...). Unknown SKUs in a feed are reported and skipped, never a crash.
- **Safe storage** — atomic JSON store (`data/monitor.json`): writes go to a temp file and are swapped in with `os.replace`, the previous good copy is kept as `.bak`, and a corrupt main file is auto-restored from the backup.

## Who it helps

- **eBay dropshippers** who need to know the moment a supplier's price or stock changes — before the next order loses money.
- **Small sellers** who want supplier monitoring without paying $29+/month for a SaaS dashboard.
- **Freelancers / virtual assistants** who manage a client's eBay store and need a clean daily supplier report.

## Requirements

Python 3.10+ and nothing else — standard library only, no dependencies to install.

## Installation

```bash
git clone https://github.com/hasnaat-anjum/ebay-supplier-price-monitor.git
cd ebay-supplier-price-monitor
# nothing to install — standard library only
python -m supplier_monitor --help
```

## Usage

```bash
# load 10 realistic demo products and see it in action
python -m supplier_monitor demo
python -m supplier_monitor dashboard
python -m supplier_monitor alerts
python -m supplier_monitor suppliers

# track a product of your own
python -m supplier_monitor add LED-STRIP-5M --title "RGB LED Strip Lights 5m" \
    --ebay-price 24.99 --supplier Amazon --supplier-price 11.50 --shipping 2.00

# record a manual supplier check (price you observed + stock status)
python -m supplier_monitor check LED-STRIP-5M --price 16.50 --stock in_stock

# apply a supplier price-feed CSV (see examples/supplier_feed_week1.csv)
python -m supplier_monitor check-feed supplier-feed.csv

# fetch the price straight from the supplier's product page
python -m supplier_monitor fetch LED-STRIP-5M

# see one product's history and trend
python -m supplier_monitor history LED-STRIP-5M

# change the eBay fee settings (default 13.25% + $0.30)
python -m supplier_monitor config --fee-percent 12.5 --fixed-fee 0.30

# export a report
python -m supplier_monitor export --format html --out report.html
python -m supplier_monitor export --format csv --out report.csv
python -m supplier_monitor export --format json --out report.json

# import / export the tracked-items list
python -m supplier_monitor import-items my-products.csv
python -m supplier_monitor export-items my-products.csv

# use a different data file
python -m supplier_monitor --data ~/my-store.json dashboard
```

### Example output

After loading the demo data and applying the week-1 supplier feed:

```text
$ python -m supplier_monitor demo
Loaded 10 demo items (seeded 2026-11-15).

$ python -m supplier_monitor check-feed examples/supplier_feed_week1.csv
Feed applied: 4 item(s) updated.
Unknown SKUs skipped (1): UNKNOWN-SKU-999
Run `alerts` to see what changed.

$ python -m supplier_monitor dashboard
eBay Supplier Price Monitor — dashboard (2026-11-25)
============================================================
Tracked items:        10
Items at risk:        9
Alerts:               3 critical, 8 warning, 1 info
Monthly profit at risk: $72.90 (assumes 10 sales/month per item)

Suppliers needing attention:
  - Amazon: reliability 95/100, 0 stock-out(s) in 30 days, avg drift +43.5%
  - Walmart: reliability 95/100, 0 stock-out(s) in 30 days, avg drift +42.8%
  - AliExpress: reliability 90/100, 1 stock-out(s) in 30 days, avg drift -7.9%

$ python -m supplier_monitor alerts
12 alert(s): 3 critical, 8 warning, 1 info

[CRITICAL] EARBUDS-PRO: Wireless Earbuds Pro with Charging Case is OUT OF STOCK at AliExpress.
           -> Pause listing until restocked

[CRITICAL] LED-STRIP-5M: Supplier price for RGB LED Strip Lights 5m with Remote rose $11.50 -> $16.50 (+43.5%) and wiped out the margin.
           -> Raise eBay price to $26.21

[CRITICAL] KITCHEN-CHOPPER: Supplier price for Manual Vegetable Chopper 500ml rose $14.00 -> $19.99 (+42.8%) and wiped out the margin.
           -> Raise eBay price to $32.46
```

The demo feed is designed to show real alerts: two items got pricier (LED strips, kitchen chopper), one went out of stock (earbuds), one got cheaper (phone case), and one unknown SKU is skipped.

### The money math, worked

eBay price $24.99, supplier $11.50, shipping $2.00:

- eBay fee = round($24.99 × 13.25%) + $0.30 = $3.61
- Profit = $24.99 − $3.61 − $11.50 − $2.00 = **$7.88**
- Margin = $7.88 / $24.99 = **31.5%**

## Demo instructions

```bash
python -m supplier_monitor demo
python -m supplier_monitor check-feed examples/supplier_feed_week1.csv
python -m supplier_monitor dashboard
python -m supplier_monitor alerts
python -m supplier_monitor suppliers
python -m supplier_monitor history LED-STRIP-5M
python -m supplier_monitor export --format html --out report.html
```

`examples/` also contains `demo_items.csv` (the 10 demo products, importable with `import-items`), `supplier_feed_week1.csv` (the feed used above), `sample_dashboard.txt` and `sample_report.html` — the actual dashboard output and HTML report produced by the demo run.

## Data files

| File | What it holds |
| --- | --- |
| `data/monitor.json` | All tracked items, price history and fee settings. Created on first run. |
| `data/monitor.json.bak` | The previous good copy, kept automatically for crash recovery. |
| `examples/demo_items.csv` | The 10 demo products as a CSV. |
| `examples/supplier_feed_week1.csv` | A sample supplier price feed. |

Your data stays on your machine — the tool makes no network calls except `fetch`, which only downloads the supplier product page you point it at.

## Project structure

```text
supplier_monitor/
  __init__.py      package version
  __main__.py      `python -m supplier_monitor` entry point
  cli.py           argparse CLI — all 16 subcommands
  fees.py          exact-cent fee, profit, margin and suggested-price math
  models.py        TrackedItem / PriceCheck / Alert dataclasses + validation
  store.py         atomic JSON store with .bak backup and auto-restore
  alerts.py        alert rules and the supplier scorecard
  fetch.py         JSON-LD price fetcher (urllib, best-effort)
  reports.py       dashboard, history + sparkline, CSV/JSON/HTML exports
  import_csv.py    CSV import/export with header aliases
tests/             91 tests (fees, alerts, store, fetch, CLI)
examples/          demo items, sample feed, sample dashboard + HTML report
run_tests.py       unittest discovery runner
```

## Tests

```bash
python run_tests.py
# Ran 91 tests — OK
```

Tests use temporary directories only and never touch the real `data/` directory. Network calls in the fetch tests are faked with `unittest.mock`.

## Technologies

Python 3.10+ standard library only: `argparse`, `json`, `csv`, `urllib`, `decimal`, `dataclasses`, `unittest`. No third-party packages.

## License

MIT — see [LICENSE](LICENSE).
