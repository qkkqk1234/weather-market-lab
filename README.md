# weather-market-lab

[![ci](https://github.com/qkkqk1234/weather-market-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/qkkqk1234/weather-market-lab/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[中文](README.zh.md)

A reproducible harness for **daily-high-temperature prediction markets** — the
ones that ask "what will the highest temperature in city X be today?" and settle
against a public weather station.

Data pipeline, a calibrated baseline model, a leakage-audited walk-forward
backtester, a risk gate, and the results — including the negative ones, which
are most of them.

> **Finding.** Over 174 settled days this market prices its buckets well. The
> baseline model does not beat it at any decision hour, and a gated strategy
> trading the disagreement produced 16 winners where the market's own prices
> predicted 16.6 (z = −0.17). There is no edge here to report.

Everything below regenerates from a 600 KB bundled snapshot in under two
minutes.

## Results

### 1. The market is calibrated

Every quote at local 09:00 / 12:00 / 15:00, binned by price against how often
those buckets won.

| price band | n | mean price | realised | gap |
|---|---:|---:|---:|---:|
| 0.00–0.02 | 2679 | 0.002 | 0.000 | −0.2 pp |
| 0.02–0.05 | 329 | 0.033 | 0.030 | −0.3 pp |
| 0.05–0.10 | 287 | 0.071 | 0.077 | +0.6 pp |
| 0.10–0.20 | 338 | 0.147 | 0.163 | +1.6 pp |
| 0.20–0.30 | 302 | 0.249 | 0.258 | +1.0 pp |
| 0.30–0.40 | 263 | 0.346 | 0.350 | +0.4 pp |
| 0.40–0.60 | 191 | 0.472 | 0.414 | −5.8 pp |
| 0.60–0.80 | 70 | 0.697 | 0.671 | −2.6 pp |
| 0.80–0.95 | 49 | 0.883 | 0.898 | +1.5 pp |
| 0.95–1.00 | 50 | 0.976 | 1.000 | +2.4 pp |

![market calibration](reports/market_calibration.png)

The 0.40–0.60 band is the only one that looks interesting, and it is not: at
n=191 the standard error is ~3.6 pp, so −5.8 pp is under two sigma.

### 2. The model loses to the market at every hour that matters

Model refit before each day; market distribution is its own mids renormalised
to 1. Current settlement regime only.

| local hour | n | model log loss | market log loss | model top-1 | market top-1 |
|---:|---:|---:|---:|---:|---:|
| 09 | 16 | 1.994 | **1.329** | 18.8% | **43.8%** |
| 10 | 16 | 1.789 | **1.252** | 12.5% | **56.2%** |
| 11 | 16 | 1.511 | **1.250** | 31.2% | **62.5%** |
| 12 | 15 | 1.573 | **1.079** | 26.7% | **60.0%** |
| 13 | 14 | 1.340 | **0.986** | 50.0% | 42.9% |
| 14 | 14 | 1.058 | **0.889** | 50.0% | 57.1% |
| 15 | 14 | **0.575** | 0.586 | 78.6% | 78.6% |

![model vs market](reports/model_vs_market.png)

They converge by 15:00 because by then the day's high is usually set and
neither is forecasting anything. **n is 14–16 days** — the whole regime since
the source change. The direction is consistent across every hour and matches
the trading result below; the magnitudes are not precise.

### 3. The gate, run over recorded prices

Walk-forward, refit before every day, fills at mid + 1 cent, hold to
resolution, flat $1 per ticket, ≤ 2 tickets a day, entries before noon.

| regime | days | trades | ROI | wins | expected | z |
|---|---:|---:|---:|---:|---:|---:|
| Weather Underground (03-23 → 08-23) | 130 | 241 | −45.5% | 15 | 14.9 | +0.04 |
| NOAA / Bao'an METAR (08-24 → 09-11) | 15 | 29 | −37.3% | 1 | 1.8 | −0.62 |
| **combined** | **145** | **270** | **−44.6%** | **16** | **16.6** | **−0.17** |

![cumulative P&L](reports/pnl_curve.png)

*Expected* is the sum of the market's own quoted probabilities for exactly the
tickets bought. Realised wins land on top of it: the gate picks the tickets
where the model disagrees most with the price, and on those the price was
right.

**How to read the ROI, and how not to.** Mean ticket price is $0.07, so one
2-cent winner swings ROI by tens of points. On 270 longshot tickets the ROI
estimate is mostly noise — it is negative here because the winners happened to
land on the dearer tickets, not because of costs: at zero slippage it is
−45.1%, barely different. The win-count z-score is the statistic with power,
and it says zero.

### 4. A silent resolution-source change

On 2026-08-24 this market changed which thermometer decides it — a coastal
station in Hong Kong for an airport 30 km away — with no announcement. Same
title, same buckets. `studies/04` finds it from outcomes alone:

| candidate source | before | after |
|---|---:|---:|
| Weather Underground page (Lau Fau Shan sensor) | **82%** (n=154) | — |
| HKO Lau Fau Shan, official daily max | 67% (n=153) | — |
| HKO Wetland Park, 4 km away | 40% (n=153) | — |
| Shenzhen Bao'an METAR (ZGSZ) | **23%** (n=155) | **100%** (n=19) |

A constant offset does not patch it: `METAR − WU print` has a median of −1 but
ranges from −4 to +4 across 146 overlapping days, and the buckets are 1°C wide.

This also explains result 2. The old arrangement had a latency edge — the
settling sensor published 1-minute readings 40–60 minutes before the hourly
number the market resolved on. The new one has none: the METAR reading *is*
both the settlement and the fastest public feed of it. Full write-up:
[docs/silent-source-change.md](docs/silent-source-change.md).

### 5. Second study: 51 cities, the other end of the day

[`latelock/`](latelock/) asks the complementary question — once the high is
physically locked in, does the winning bucket still sell at a discount? Across
378 city-days with a strict weather signal, **2** had a buyable print in the
0.90–0.95 band. The discount exists; the capacity does not.

## Reproduce

```bash
git clone https://github.com/qkkqk1234/weather-market-lab
cd weather-market-lab
pip install -r requirements.txt

python -X utf8 studies/01_market_calibration.py
python -X utf8 studies/02_model_vs_market.py
python -X utf8 studies/03_gated_backtest.py     # ~90s, refits per day
python -X utf8 studies/04_settlement_source.py
python -X utf8 -m pytest tests/ -q
```

Everything in `reports/` comes from those four scripts.

## Layout

```
wxlab/       data loaders, public fetchers, model, gate, backtester, figures
studies/     four scripts; every number in this README comes from one of them
latelock/    the 51-city study, its own README and 37 tests
docs/        the source-change post-mortem
data/        bundled snapshot, ~600 KB of plain CSV
```

### The model

At decision hour `h` the running max is known and the day's high can only go
up, so the running max is a hard floor and the only unknown is
`delta = daily_max − temperature_now`. `P(delta)` is an empirical frequency
table over (hour, 2-hour rise, dewpoint spread, cloud cover), backing off to
coarser cells below 60 observations. Trained on ~2,700 warm-season days from
2014 on.

Deliberately plain: every number traces to a countable set of past days, which
is what you want when the thing you are testing against may simply be right.

### What keeps the backtest honest

- **Refit per day.** `fit(before=day)` for each trading day, cutoff exclusive,
  asserted by a test.
- **Pay to cross.** Quotes are mids; every buy fills at mid + $0.01. The venue
  tick is $0.001, so that is ~10 ticks of adverse fill.
- **No exits.** A loser is held to zero. On a book this thin there is often no
  bid to sell into, so the backtest is not allowed an exit that may not exist.
- **Flat stake.** Sizing is a separate question from whether a signal exists;
  mixing them is how a flat edge starts looking like a compounding one.
- **A settlement tripwire.** One test asserts
  `floor(max hourly METAR) == settled bucket` for every day in the current
  regime. It fails the moment the source moves again.

### The risk gate

| rule | why |
|---|---|
| entries before 12:00 local only | later, the outcome is largely determined and already priced |
| limit price ≤ 0.25 | above that you are buying the market's own opinion |
| tiered safety multiple: <0.02 → 10×, 0.02–0.10 → 4×, 0.10–0.25 → 2× | the cheap end is where a model's tail is least trustworthy |
| $1 per ticket, ≤ 2 tickets/day, no averaging down | sizing is not evidence |
| no stop loss, accept the zero | a thin book may have no bid to sell into |

The last two interact with the venue: with a $1 ticket and a 5-share minimum,
the highest reachable price is $0.20, so the 0.20–0.25 slice is unreachable in
practice.

## Data

| file | rows | what |
|---|---:|---|
| `zgsz_metar_hourly.csv.gz` | 111,231 | ZGSZ hourly METAR, 2014-01-01 → 2026-09-12, UTC |
| `shenzhen_events.csv` | 1,947 | bucket definitions and resolved winner, 177 market days |
| `shenzhen_quotes.csv.gz` | 17,793 | mid per bucket, hourly local 08:00–19:00 |
| `settlement_candidates.csv` | 155 | four candidate stations vs the settled label |

All from public unauthenticated endpoints. Sources and refresh instructions:
[`data/README.md`](data/README.md).

## Scope

A research harness. There is **no order placement path anywhere in it** — no
wallet, no signing key, no exchange client. `wxlab.fetch` issues unauthenticated
GETs and nothing else.

Nothing here is investment advice, and the results are the argument *against*
trading this market on a model of this kind.

## Open question

**Can a language model add calibration over an empirical baseline on the
mornings where the baseline is weakest?**

Hours 09–12 are where the outcome is least determined and where the baseline
loses to the market by the widest margin. Those mornings carry information a
frequency table cannot encode — the shape of a satellite cloud field, a sea
breeze front in the wind record, a forecast discussion in prose. The test is
whether an LLM shown that context produces distributions that beat the
empirical table on log loss, on held-out days, without leaking.

This repo is the harness for that evaluation. The evaluation is what it does
not yet have.

## License

MIT. See [LICENSE](LICENSE).
