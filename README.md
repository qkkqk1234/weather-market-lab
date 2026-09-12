# weather-market-lab

[![ci](https://github.com/qkkqk1234/weather-market-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/qkkqk1234/weather-market-lab/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A reproducible harness for studying **daily-high-temperature prediction
markets** — the ones that ask "what will the highest temperature in city X be
today?" and settle against a public weather station.

It ships the data pipeline, a calibrated baseline model, a leakage-audited
walk-forward backtester, the risk gate that ran against real money, and the
results — **including the negative ones, which are most of them.**

> **Headline finding:** over 174 settled days, this market prices these buckets
> well. The baseline model does not beat it at any decision hour, and a gated
> strategy that traded on the model's disagreement produced 16 winners where
> the market's own prices predicted 16.6 (z = −0.17). There is no edge here to
> report, and reporting one anyway is the failure mode this repo is built
> against.

Everything below regenerates from the bundled snapshot in under two minutes.

---

## Why publish a negative result

Public work on prediction markets skews badly toward strategies that worked in
the window they were fitted to. The scarce artifact is not another backtest
with a rising curve — it is a harness where the curve is allowed to go down,
with the leakage controls that make that believable.

There is also a specific incident here worth having written down. On
2026-08-24 this market silently changed which thermometer decides it, from a
Hong Kong coastal station to a Shenzhen airport 30 km away. Same title, same
buckets, no announcement. `studies/04` finds it from outcomes alone: the new
source explains **23%** of settlements before that date and **100%** after.
Write-up: [docs/silent-source-change.md](docs/silent-source-change.md).

## Results

### 1. The market is calibrated

Every quote at local 09:00 / 12:00 / 15:00 across 174 settled days, binned by
price against how often those buckets won.

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
n=191 the standard error is about 3.6 pp, so −5.8 pp is under two sigma. Worth
watching, not worth trading.

### 2. The model loses to the market at every hour that matters

Model refit before each day; market distribution is its own mids renormalised
to 1. Current settlement regime only (2026-08-24 onward).

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

By 15:00 they converge, because by 15:00 the day's high is usually already set
and neither is forecasting anything. **n is 14–16 days** — that is the whole
regime since the source change, and it is small. The direction is consistent
across every hour and matches the trading result below, but the magnitudes
should not be quoted as precise.

### 3. The gate, run over recorded prices

Walk-forward, refit before every day, fills at mid + 1 cent, hold to
resolution, flat $1 per ticket, at most 2 tickets a day, entries before noon.

| regime | days | trades | ROI | wins | expected | z |
|---|---:|---:|---:|---:|---:|---:|
| WU / Lau Fau Shan (03-23 → 08-23) | 130 | 241 | −45.5% | 15 | 14.9 | +0.04 |
| NOAA / Bao'an METAR (08-24 → 09-11) | 15 | 29 | −37.3% | 1 | 1.8 | −0.62 |
| **combined** | **145** | **270** | **−44.6%** | **16** | **16.6** | **−0.17** |

![cumulative P&L](reports/pnl_curve.png)

*Expected* is the sum of the market's own quoted probabilities for exactly the
tickets bought. Realised wins land on top of it. The strategy is not losing to
fees or to bad luck — it is reproducing the market's own distribution and then
paying to cross the spread.

Re-run with zero slippage and it is still −45.1%, so this is a model result,
not a fee result.

**On reading ROI here:** mean ticket price is $0.07, so one 2-cent winner moves
ROI by tens of points. With 270 longshot tickets the ROI estimate is mostly
noise; the win-count z-score is the statistic with power, and it says zero.

### 4. Second study: 51 cities, the other end of the day

[`latelock/`](latelock/) asks the complementary question — once the high is
physically locked in, does the winning bucket still sell at a discount? Across
378 city-days with a strict weather signal, **2** had a buyable print in the
0.90–0.95 band. The discount exists; the capacity does not.

---

## Install and reproduce

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

Everything in `reports/` is generated by those four scripts. The snapshot in
`data/` is 600 KB of plain CSV.

## How it is put together

```
wxlab/
  data.py      loaders; hourly METAR, market buckets, quotes
  fetch.py     the public endpoints (IEM archive, Polymarket Gamma + CLOB)
  model.py     backed-off empirical PMF over "how much further does it climb"
  gate.py      the hard risk rules, unchanged from the live version
  backtest.py  walk-forward runner, pessimistic fills, P&L curve
  report.py    figures
studies/       four scripts; every number in this README comes from one of them
latelock/      the 51-city late-lock study, its own README and 37 tests
docs/          the source-change post-mortem
data/          bundled snapshot, ~600 KB
```

### The model, in one paragraph

At decision hour `h` the running max is known, and the day's high can only go
up — so the running max is a hard floor and the only unknown is
`delta = daily_max − temperature_now`. `P(delta)` is an empirical frequency
table over (hour, 2-hour rise, dewpoint spread, cloud cover), backing off to
coarser cells when the fine cell has fewer than 60 observations. Trained on
Apr–Oct days from 2014 onward — about 2,700 warm-season days. It is
deliberately plain: every number traces to a countable set of past days, which
is what you want when the thing you are testing against may simply be right.

### What keeps the backtest honest

- **Refit per day.** `DeltaModel.fit(before=day)` is called for each trading
  day; the cutoff is exclusive, and a test asserts it.
- **Pay to cross.** Quotes are mids; every buy fills at mid + $0.01. The venue
  tick is $0.001, so that is ~10 ticks of adverse fill — harsh on a 3-cent
  ticket, which is the honest treatment of a book this thin.
- **No exits.** The gate forbids stop losses because they were measured filling
  at zero on this book. The backtest is not allowed an exit the live system
  cannot take.
- **Flat stake.** Position sizing is a separate question from whether a signal
  exists. Mixing them is how a flat edge starts looking like a compounding one.
- **A settlement tripwire.** `test_metar_daily_max_explains_every_settlement_after_the_switch`
  fails the moment the resolution source moves again.

### The risk gate

Not tuned parameters — each one is a specific loss, kept in one file so a
backtest cannot quietly relax one:

| rule | why |
|---|---|
| entries before 12:00 local only | afternoon entries measured −61% to −100% |
| limit price ≤ 0.25 | above that you are paying for the market's opinion |
| safety multiple by tier: <0.02 → 10×, 0.02–0.10 → 4×, 0.10–0.25 → 2× | the cheap end is where a model's tail is least trustworthy |
| $1 per ticket, ≤ 2 tickets/day | a $3 order once went in at $7.88 |
| no stop loss, accept the zero | stop losses on this book filled at $0.00 |

The last two interact with the venue: with a $1 ticket and a 5-share minimum,
the highest reachable price is $0.20, so the 0.20–0.25 slice is unreachable
live even though the backtest can express it.

## Data

| file | rows | what |
|---|---:|---|
| `zgsz_metar_hourly.csv.gz` | 111,231 | ZGSZ hourly METAR, 2014-01-01 → 2026-09-12, UTC |
| `shenzhen_events.csv` | 1,947 | bucket definitions and resolved winner, 177 market days |
| `shenzhen_quotes.csv.gz` | 17,793 | mid per bucket, sampled hourly local 08:00–19:00 |
| `settlement_candidates.csv` | 155 | four candidate stations' daily max vs the settled label |

Sources and refresh instructions: [`data/README.md`](data/README.md). All of it
comes from public, unauthenticated endpoints.

## Scope

This is a research harness. There is **no order placement path anywhere in it**
— no wallet, no signing key, no exchange client. `wxlab.fetch` issues
unauthenticated GETs and nothing else.

Nothing here is investment advice, and the results are the argument *against*
trading this market on a model of this kind.

## Open question

The part that is genuinely unresolved: **can a language model add calibration
over an empirical baseline on the mornings where the baseline is weakest?**

Hours 09–12 are where the outcome is least determined and where the baseline
loses to the market by the widest margin. Those mornings carry information the
frequency table cannot encode — the shape of a satellite cloud field, a sea
breeze front in the wind record, a forecast discussion in prose. The test is
whether an LLM shown that context produces distributions that beat the
empirical table on log loss, on held-out days, without leaking.

The harness for that evaluation is what this repo is. The evaluation itself is
what it does not yet have.

## License

MIT. See [LICENSE](LICENSE).
