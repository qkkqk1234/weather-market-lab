# weather-market-lab

[![ci](https://github.com/qkkqk1234/weather-market-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/qkkqk1234/weather-market-lab/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[中文](README.zh.md)

A reproducible harness for **daily-high-temperature prediction markets** — the
ones that ask "what will the highest temperature in city X be today?" and settle
against a public weather station.

Data pipeline, a calibrated baseline model, a leakage-audited walk-forward
backtester, a risk gate, and the results — including the negative ones, which
are most of them. Plus, in [§5](#5-where-to-look-instead), a map of the three
places an edge could still be, and which of them are already closed.

> **Finding.** Over 174 settled days this market prices its buckets well. The
> baseline model does not beat it at any decision hour, and a gated strategy
> trading the disagreement is within one sigma of the market's own predictions
> (20 winners against 16.8 expected, z = +0.83). There is no edge here to
> report.

Everything below regenerates from a 600 KB bundled snapshot in about two
minutes.

## Results

### 1. The market is calibrated

Every quote at local 09:30 / 12:30 / 15:30, binned by price against how often
those buckets won. `se` is the standard error under "the quoted price is the
truth".

| price band | n | mean price | realised | gap | se | z |
|---|---:|---:|---:|---:|---:|---:|
| 0.00–0.02 | 2715 | 0.002 | 0.000 | −0.19 pp | 0.09 | −2.07 |
| 0.02–0.05 | 328 | 0.033 | 0.037 | +0.38 pp | 0.98 | +0.39 |
| 0.05–0.10 | 243 | 0.071 | 0.115 | +4.43 pp | 1.65 | +2.69 |
| 0.10–0.20 | 304 | 0.146 | 0.148 | +0.15 pp | 2.03 | +0.08 |
| 0.20–0.30 | 262 | 0.248 | 0.218 | −3.09 pp | 2.67 | −1.16 |
| 0.30–0.40 | 230 | 0.346 | 0.387 | +4.13 pp | 3.14 | +1.32 |
| 0.40–0.60 | 184 | 0.469 | 0.391 | −7.77 pp | 3.68 | −2.11 |
| 0.60–0.80 | 57 | 0.682 | 0.667 | −1.51 pp | 6.17 | −0.24 |
| 0.80–0.95 | 64 | 0.888 | 0.875 | −1.26 pp | 3.95 | −0.32 |
| 0.95–1.00 | 77 | 0.981 | 1.000 | +1.95 pp | 1.57 | +1.24 |

![market calibration](reports/market_calibration.png)

Three bands pass 2σ, which is noise across ten tests, not three findings: the
signs alternate between neighbours, whereas a real favourite-longshot effect is
monotone in price. The sub-cent band is dead buckets, which cannot be shorted
profitably at 0.002 anyway.

### 2. The model loses to the market at every hour

Model refit before each day; market distribution is its own mids renormalised
to 1. Current settlement regime only.

| local hour | n | model log loss | market log loss | model top-1 | market top-1 |
|---:|---:|---:|---:|---:|---:|
| 09 | 16 | 1.994 | **1.348** | 18.8% | **37.5%** |
| 10 | 16 | 1.789 | **1.364** | 12.5% | **43.8%** |
| 11 | 15 | 1.542 | **1.071** | 33.3% | **46.7%** |
| 12 | 14 | 1.502 | **0.959** | 28.6% | **64.3%** |
| 13 | 14 | 1.340 | **0.976** | 50.0% | 50.0% |
| 14 | 14 | 1.058 | **0.958** | 50.0% | **64.3%** |
| 15 | 14 | 0.575 | **0.461** | 78.6% | 78.6% |

![model vs market](reports/model_vs_market.png)

**n is 14–16 days** — the whole regime since the source change. The direction is
consistent at every hour and matches the trading result below; the magnitudes
are not precise.

### 3. The gate, run over recorded prices

Walk-forward, refit before every day, fills at mid + 1 cent, hold to
resolution, flat $1 per ticket, ≤ 2 tickets a day, entries before noon.

| regime | days | trades | ROI | wins | expected | z |
|---|---:|---:|---:|---:|---:|---:|
| Weather Underground (03-23 → 08-23) | 134 | 254 | −34.2% | 18 | 15.1 | +0.81 |
| NOAA / Bao'an METAR (08-24 → 09-11) | 14 | 26 | +12.5% | 2 | 1.8 | +0.17 |
| **combined** | **148** | **280** | **−29.8%** | **20** | **16.8** | **+0.83** |

![cumulative P&L](reports/pnl_curve.png)

*Expected* is the sum of the market's own quoted probabilities for exactly the
tickets bought. z = +0.83 is p ≈ 0.20 one-sided — not evidence of anything.
Result 2 is the better-powered statement.

**How to read the ROI, and how not to.** Mean ticket price is $0.07, so one
2-cent winner swings ROI by tens of points. On 280 longshot tickets the ROI
estimate is mostly noise: the same gate filled at the mid, with no crossing
cost at all, comes out at −54.6% because a different handful of tickets happens
to win. Read the win-count z, not the ROI.

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
settling sensor published 1-minute readings 40–60 minutes ahead of the hourly
number the market resolved on. The new one has none: the METAR reading *is*
both the settlement and the fastest public feed of it. Full write-up:
[docs/silent-source-change.md](docs/silent-source-change.md).

### 5. Where to look instead

`studies/05` scans the three places an edge could still structurally exist.

**A. Impossible buckets.** The day's high only goes up, so a bucket entirely
below the running max is worth exactly zero — free money, no forecast needed.
Here: **1 quote out of 719** was still above a cent. Closed. Worth re-running on
a thinner market, since the scan costs nothing.

**B vs C. The decided-but-unpriced window.** This is the live one.

| local hour | already decided (hindsight) | winner's median quote | ≤ 0.90 | cheapest | cooling rule fires | correct | its median quote |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 11:30 | 3 | 0.750 | 3 | 0.450 | 1 | 0 | 0.360 |
| 12:30 | 5 | 0.770 | 3 | 0.500 | 1 | 0 | 0.363 |
| 13:30 | 6 | 0.925 | 3 | 0.420 | 0 | — | — |
| 14:30 | 7 | 0.875 | 5 | 0.480 | 1 | 1 | 0.975 |
| 15:30 | 12 | 0.915 | 6 | 0.330 | 1 | 1 | 0.995 |
| 16:30 | 14 | 0.990 | 3 | 0.665 | 1 | 1 | 0.997 |
| 17:30 | 14 | 0.995 | 1 | 0.770 | 2 | 2 | 1.000 |
| 18:30 | 14 | 0.999 | 0 | 0.960 | 4 | 4 | 1.000 |

Left half: days where the high was *in fact* already set, and what the eventual
winner was still quoted at. Right half: a cooling rule that uses no hindsight —
two consecutive reports at least 2°C below the running max, no rebound, max set
at least two hours ago.

The rule is accurate from 13:00 on (13 firings, 13 correct; before 13:00 it
fires on morning bumps and was wrong both times, so it needs a time floor). But
**by the time it fires, the winner is quoted at 0.97+**. On the same days, one
to three hours earlier, the answer was already physically fixed and the winner
was buyable between 0.33 and 0.90.

**That gap is the opportunity, and it is not a forecasting problem.** It needs
evidence that the temperature *cannot* rise further, an hour or two before an
hourly-integer cooling rule can see it — a finer or faster feed of the same
station, a sea breeze front in the wind record, a cloud deck arriving on
satellite. That is a much narrower question than "what will the high be", and
it is the one the market is still visibly wrong about.

Counts here are single digits per hour. This is a map of where to dig, not
evidence that there is anything at the bottom.

### 6. Second study: the same window across 51 cities

[`latelock/`](latelock/) chases exactly that window at scale, and finds the
other binding constraint. The weather signal is solid — 21 of 51 cities clear a
97% stability bound. But across 378 city-days with a strict signal, **2** had a
buyable print in the 0.90–0.95 band. The discount exists; the capacity does not.

So the full picture: the morning is efficiently priced, the late afternoon is
efficiently priced once the lock is obvious, and the hours in between are where
both the mispricing and the difficulty live.

## Reproduce

```bash
git clone https://github.com/qkkqk1234/weather-market-lab
cd weather-market-lab
pip install -r requirements.txt

python -X utf8 studies/01_market_calibration.py
python -X utf8 studies/02_model_vs_market.py
python -X utf8 studies/03_gated_backtest.py     # ~90s, refits per day
python -X utf8 studies/04_settlement_source.py
python -X utf8 studies/05_where_to_look.py
python -X utf8 -m pytest tests/ -q
```

Everything in `reports/` comes from those five scripts.

## Layout

```
wxlab/       data loaders, public fetchers, model, gate, backtester, figures
studies/     five scripts; every number in this README comes from one of them
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
- **Trade after the report is public.** An hourly METAR valid at `HH:00` is
  published 6–21 minutes later, so a quote sampled at `HH:00` predates the
  reading the model reads. Quotes are therefore sampled at `HH:30`. Getting
  this wrong is a 30-minute look-ahead that flatters the model.
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
| `shenzhen_quotes.csv.gz` | 17,483 | mid per bucket at `HH:30` local, 08:30–19:30 |
| `settlement_candidates.csv` | 155 | four candidate stations vs the settled label |

All from public unauthenticated endpoints. Sources and refresh instructions:
[`data/README.md`](data/README.md).

## Scope

A research harness. There is **no order placement path anywhere in it** — no
wallet, no signing key, no exchange client. `wxlab.fetch` issues unauthenticated
GETs and nothing else. Nothing here is investment advice.

## Open question

**Can a language model tell, at 14:00, that the day is already over?**

That is §5's gap stated as a research problem, and it is a better question than
"what will the high be" because it is narrower, it has a physical answer, and
the market is measurably still wrong about it for one to three hours. The
evidence a person would use — a sea breeze front in the wind record, a cloud
deck arriving on satellite, the wording of a forecast discussion — is exactly
the kind a frequency table cannot encode and a language model might.

The test: does an LLM shown that context beat the empirical table on log loss,
on held-out days, without leaking? This repo is the harness for that evaluation.
The evaluation is what it does not yet have.

## License

MIT. See [LICENSE](LICENSE).
