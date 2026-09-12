# The day the thermometer moved

**2026-08-24.** A market called *Highest temperature in Shenzhen on …* changed
which thermometer decides it. Same title, same buckets, same series, no
announcement. The station moved about 30 km across Deep Bay, and everything
built on top of the old one stopped being about this market.

This is written up because the failure is generic. Any market that resolves
against an external number can have that number redefined underneath you, and
the thing that catches it is not a better model.

## What changed

| | before 2026-08-24 | from 2026-08-24 |
|---|---|---|
| resolution source in the event text | Weather Underground, page `ZGSZ:9:CN` | NOAA `weather.gov/wrh/timeseries?site=zgsz` |
| the sensor actually behind it | HKO **Lau Fau Shan**, Hong Kong (`obs_id 45035`) | **Shenzhen Bao'an airport METAR** |
| reading granularity | hourly integer °C, published on a web page | hourly integer °C, routine METAR |
| distance between the two | — | ~30 km, opposite sides of a bay |

The Weather Underground page carried an ICAO-looking identifier, `ZGSZ`, which
is Bao'an airport. It served Lau Fau Shan's readings. So for months the market
looked like it settled on a Shenzhen airport and settled on a Hong Kong coastal
station instead. That is worth stating plainly, because it means the mislabel
existed *before* the change too.

## What it did to the numbers

On the switch day itself: Lau Fau Shan ran to 33.8°C and the WU page printed a
high of 33. Bao'an METAR read 31 at every hour from 10:00 to 15:00. The market
settled at 31°C, closing at 0.994.

`studies/04_settlement_source.py` scores each candidate station against the
bucket that actually resolved:

| candidate source | before | after |
|---|---:|---:|
| Weather Underground page (Lau Fau Shan sensor) | **82%** (n=154) | — |
| HKO Lau Fau Shan, official daily max | 67% (n=153) | — |
| HKO Wetland Park, 4 km away | 40% (n=153) | — |
| Shenzhen Bao'an METAR (ZGSZ) | **23%** (n=155) | **100%** (n=19) |

23% to 100% on a date nobody chose. There is no version of "the model drifted"
that produces that table.

## Why a correction factor does not save you

The obvious patch is an offset. Across 146 overlapping days, `METAR − WU print`
has a median of −1 and a mean of −0.48 — so far so tractable. But the
distribution runs from −4 to +4. On a market whose buckets are 1°C wide, a
spread that wide *is* the entire answer. Two stations with a stable mean
difference are still two different questions when the granularity is one
degree.

## What was actually lost

The old chain had a real, mechanical edge, and it was not the model:

    HKO 1-minute readings (Lau Fau Shan)   →  40-60 minutes ahead of
    the hourly integer printed by the WU page  →  which is the settlement

A faster feed on the *same sensor* is an information advantage that does not
require being smarter than anyone. The new arrangement has no such gap: METAR
from Bao'an is both the settlement number and the fastest published reading of
it. Everyone sees it at once.

So the honest summary of the migration is not "our model needs retraining". It
is: **the edge was latency, the latency is gone, and the model was never the
part that worked.** `studies/02` and `studies/03` are what that looks like when
you measure it instead of asserting it.

## What survived

The forecasting chain did not transfer. These did:

- **The risk gate** (`wxlab/gate.py`) — price caps, the noon cutoff, tiered
  safety multiples, no stop loss. Every rule in it binds on price and size, not
  on the model being right, which is why it goes on working after a model stops
  working. That is the property to design for: a gate that only holds while the
  model is correct is not a gate.
- **Leakage discipline** — walk-forward refitting, running-max monotonicity
  checks, an explicit test that the settlement rule still holds
  (`tests/test_wxlab.py::test_metar_daily_max_explains_every_settlement_after_the_switch`).
  That test is the tripwire for the *next* silent change.
- **Reading the resolution text every day before trading.** The change was
  visible in the event description on the morning of 2026-08-24. Nothing looked
  at it, because nothing had ever needed to.

## The operational lesson

Treat the resolution source as an input with a version, not as a constant.
Fingerprint it on every cycle, diff it, and halt on change. A strategy that
cannot tell you which thermometer it is trading is not a strategy with a bug;
it is a strategy with an unknown.
