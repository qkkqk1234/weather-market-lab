# latelock — the same question, asked across 51 cities

`wxlab` asks whether a morning forecast can beat the market. This module asks
the opposite-end question: **once the day's high is physically settled, does
the market still sell the winning bucket at a discount?**

If it is 6pm, the high was set at 2pm, and the last two hours have both come in
at least 2°C cooler with no rebound, the answer is known. A bucket still
quoted at 0.93 is then a 7-point return over a few hours — if you can actually
buy it, and actually get out.

## Verdict

**The discount is real and occasionally large. The capacity is not there.**

Across 378 city-days that produced a strict weather signal after the
2026-08-24 source change, **2** had any subsequent trade print in the
0.90–0.95 buy band. Two. Everything else was already bid up past the entry.

| what was checked | result |
|---|---|
| cities with a live daily-high market | 51 discovered, 49 with usable history |
| cities passing the weather-stability gate | 21 (one-sided 95% Wilson lower bound ≥ 97%) |
| Warsaw, the case that started this | train 385/385, validate 86/86 signal days with no new high |
| …yet its Wilson lower bound | 96.95%, *below* the pre-registered 97% threshold |
| city-days with signal, post-switch | 378 |
| …with a buyable print in band | **2** |

Arithmetic on a fill that does work, at the venue's weather-taker fee
(`shares × 0.05 × p × (1-p)`):

| buy → sell | net on 100 shares | return on cost | one zero cancels |
|---|---:|---:|---:|
| 0.93 → 0.998 | $6.46 | 6.93% | 14.4 winners |
| 0.95 → 0.998 | $4.55 | 4.78% | 20.9 winners |

Break-even success rates are 93.5% and 95.4%. That is the whole problem: the
edge per trade is thin enough that the tail, not the fee, decides the outcome.

## What the code does

Paper only. There is no wallet, no signing key, and no order placement path.

1. Page the public market list, and for **every** event verify the date,
   IANA timezone, settlement station, source and unit. A new city goes on a
   review list rather than straight into the universe — the source text is per
   market, not per platform (Taipei and Jinan were still on Weather Underground
   on 2026-09-05, after the "everything is NOAA now" switch).
2. Require, by default, local hour ≥ 17, both of the last two complete hours at
   least 2°C below the day's high, no rebound, and the high set at least two
   hours ago. Stale quotes, gaps, a new high in the current hour, and rounding
   boundaries all block.
3. Per-city earliest start hour chosen only on the training split (through
   2026-05-31); validation runs from 2026-06-01. Minimum 100 conditioned
   training days and 60 validation days.
4. Enter only the bucket matching the current high, at 0.90–0.95, spread ≤ 0.04,
   with the fee read from that market's own metadata — unknown fee, no trade.
5. Confirm across two scan rounds, re-pull the full order book, and simulate the
   fill only if the book has the depth inside the limit, charging each level.
6. Exit only if a real **bid** at ≥ 0.998 could absorb the whole position.
   Otherwise hold and wait for settlement. No invented exit price.

## Running it

```bash
pip install -r ../requirements.txt
python -X utf8 -m latelock study --before 2026-09-05 --split 2026-06-01
python -X utf8 -m pytest -q          # 37 tests
python -X utf8 -m latelock scan      # one read-only pass over live markets
python -X utf8 -m latelock paper --cycles 1
python -X utf8 -m latelock status
```

`study` rebuilds `reports/` from an hourly weather archive; point `--source` at
your own pull. Pick the split date once and leave it alone — re-choosing it
after seeing results is how a validation set stops being one.

Stop a run with Ctrl+C, or by creating a `HALT` file in the data directory.

## Known gaps

- The historical hourly archive has no publication or revision timestamps, so
  "complete hour + 10 minutes" is a conservative proxy for what was actually
  knowable at the time, not a record of it.
- A high all-weather stability rate does not transfer to the small subset of
  events where someone is *still* offering at 0.93. Those may be exactly the
  events with residual risk. This needs a price-conditioned sample.
- Settlement can revise until the next day's first publication, and the rules
  carry fallback branches (NOAA missing → WU → lowest bucket).

The full evidence write-up, in Chinese, is
[`reports/evaluation.zh.md`](reports/evaluation.zh.md).
