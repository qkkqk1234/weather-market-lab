# Data snapshot

Four files, ~600 KB, all from public unauthenticated endpoints. Captured
2026-09-13.

| file | rows | coverage |
|---|---:|---|
| `zgsz_metar_hourly.csv.gz` | 111,231 | 2014-01-01 → 2026-09-12 |
| `shenzhen_events.csv` | 1,947 | 177 market days, 174 settled |
| `shenzhen_quotes.csv.gz` | 17,483 | 162 days with quotes |
| `settlement_candidates.csv` | 155 | 2026-03-20 → 2026-08-23 |

## Provenance

**`zgsz_metar_hourly.csv.gz`** — Iowa Environmental Mesonet ASOS archive, the
same archive the NOAA timeseries page reads. `report_type=3` (routine hourly)
only. ZGSZ publishes no SPECI: `report_type=4` returns zero rows for 2026, and
annual report counts come to ≈ 24 × 365, so the hourly set is the complete
record the market resolves against. 4,340 of 4,384 local days carry a full 24
reports.

`valid` is **UTC**. Shenzhen is UTC+8 year round; `wxlab.data` shifts on load,
and "day" everywhere in this repo means the local calendar day.

**`shenzhen_events.csv`** — Polymarket Gamma API, series 11366. One row per
bucket per day; `resolved=1` marks the bucket that won. Tail buckets use
`lo=-999` / `hi=999`.

**`shenzhen_quotes.csv.gz`** — Polymarket CLOB `prices-history`, reduced to one
mid per bucket per local hour. The `hour` column is the METAR observation hour,
but **the quote is sampled at `HH:30`**, because the hourly report valid at
`HH:00` is not published until 6–21 minutes later (measured on the aviation
weather API's `receiptTime`, n=72: min 5.4, p50 6.4, max 21.1 minutes). Pairing
an `HH:00` quote with the `HH:00` reading is a 30-minute look-ahead that
flatters any model reading it. A cell is omitted when no quote exists in the
preceding 30 minutes, rather than being carried forward.

These are **mid prices, not executable quotes.** The order book is not in the
archive, so the backtest charges a flat $0.01 to cross rather than pretending
to know depth. Treat any result that depends on filling at the mid as an upper
bound.

**`settlement_candidates.csv`** — daily maxima from four candidate stations
alongside the label the market actually settled to, used by `studies/04` to
date the source change. The Hong Kong series (HKO Lau Fau Shan, HKO Wetland
Park, and the Weather Underground page print) end 2026-08-23, the day before
the switch; only the METAR column spans both regimes.

## Refreshing

`wxlab/fetch.py` wraps all three endpoints:

```python
from wxlab.fetch import fetch_metar_csv, fetch_events, fetch_price_history
```

METAR is one call for the whole history. Events is a handful of paged calls.
Price history is one call per outcome token per day — roughly 2,000 calls for
the full window, which is why the snapshot is committed.

Gamma returns 403 without a `User-Agent`; `fetch.py` sets one. On CLOB
`prices-history`, always pass explicit `startTs`/`endTs` — `interval=max`
silently clamps fidelity to 10 minutes.

## Before extending the window

Check that the resolution source has not moved again. Read the `description`
field of a current event and confirm which station it names, then run:

```bash
python -X utf8 -m pytest tests/test_wxlab.py -k settlement -q
```

That test asserts `floor(max hourly METAR) == settled bucket` for every day
since 2026-08-24. If it fails, the market changed underneath the data and the
reports are about something else. See
[`../docs/silent-source-change.md`](../docs/silent-source-change.md).
