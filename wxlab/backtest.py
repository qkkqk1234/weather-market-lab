"""Walk-forward backtest with pessimistic fills.

Design rules, in order of how much damage they prevent:

* **Refit before every trading day.** ``DeltaModel.fit(before=day)`` is called
  per day, so the table that prices a trade has never seen that day or any day
  after it. Slow, and the point.
* **Pay to cross.** Quotes in the snapshot are mids. Every buy fills at
  ``mid + SLIPPAGE``. The venue tick is 0.001, so a full cent is roughly ten
  ticks of adverse fill -- brutal on a 3-cent ticket, which is the honest way
  to treat a book that thin.
* **Hold to resolution.** No exits. On a book this thin there is often no bid
  to sell a loser into, so the backtest is not allowed to assume an exit that
  may not exist.
* **Flat stake.** Every ticket risks the same cash. Position sizing is a
  separate question from whether the signal exists, and mixing the two is how
  a flat edge starts looking like a compounding one.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict

from .data import Event, Metar
from .gate import check
from .model import DeltaModel

SLIPPAGE = 0.01  # added to the mid on every buy


@dataclass
class Trade:
    day: str
    hour: int
    label: str
    model_p: float
    mid: float
    fill: float
    multiple: float
    won: int
    pnl: float  # per unit of stake


@dataclass
class Summary:
    n_trades: int
    n_days: int
    stake: float
    returned: float
    roi: float
    wins: int
    hit_rate: float
    mean_fill: float
    expected_wins: float  # sum of market-implied probabilities of the tickets bought
    z_score: float        # (wins - expected) / sd, under "the market price is the truth"

    def as_dict(self):
        return asdict(self)


def run(
    metar: Metar,
    events: dict[str, Event],
    quotes: dict,
    *,
    start: str,
    end: str,
    hours=(9, 10, 11, 12),
    stake: float = 1.0,
    slippage: float = SLIPPAGE,
    model_kwargs: dict | None = None,
) -> list[Trade]:
    """Trade every settled day in [start, end]; return the filled tickets."""
    trades: list[Trade] = []
    for day in sorted(events):
        if not start <= day <= end:
            continue
        event = events[day]
        if not event.settled:
            continue
        model = DeltaModel(**(model_kwargs or {})).fit(metar, before=day)
        tickets = 0
        for hour in hours:
            pmf = model.bucket_pmf(metar, day, hour, event.buckets)
            if pmf is None:
                continue
            for bucket in event.buckets:
                mid = quotes.get((day, hour, bucket.label))
                if mid is None:
                    continue
                fill = min(0.99, round(mid + slippage, 4))
                verdict = check(hour=hour, model_p=pmf[bucket.label],
                                price=fill, tickets_used=tickets)
                if not verdict.passed:
                    continue
                won = int(bucket.label == event.winner)
                trades.append(Trade(
                    day=day, hour=hour, label=bucket.label,
                    model_p=round(pmf[bucket.label], 4), mid=mid, fill=fill,
                    multiple=round(verdict.multiple, 2), won=won,
                    pnl=stake * (1.0 / fill - 1.0) if won else -stake,
                ))
                tickets += 1
    return trades


def summarize(trades: list[Trade], stake: float = 1.0) -> Summary:
    """Headline numbers, plus a test of the only hypothesis that matters.

    The null is "the market price already is the probability". Under it, the
    number of winners among the tickets bought is Poisson-binomial with
    parameters equal to the quoted mids. ``z_score`` says how far the realised
    win count sits from that expectation. A strategy with an edge produces a
    large positive z; ROI alone cannot distinguish edge from a lucky longshot.
    """
    if not trades:
        return Summary(0, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0)
    spent = stake * len(trades)
    returned = sum(stake / t.fill for t in trades if t.won)
    wins = sum(t.won for t in trades)
    expected = sum(t.mid for t in trades)
    variance = sum(t.mid * (1 - t.mid) for t in trades)
    return Summary(
        n_trades=len(trades),
        n_days=len({t.day for t in trades}),
        stake=round(spent, 2),
        returned=round(returned, 2),
        roi=round((returned - spent) / spent, 4),
        wins=wins,
        hit_rate=round(wins / len(trades), 4),
        mean_fill=round(statistics.mean(t.fill for t in trades), 4),
        expected_wins=round(expected, 2),
        z_score=round((wins - expected) / variance ** 0.5, 2) if variance > 0 else 0.0,
    )


def pnl_curve(trades: list[Trade], stake: float = 1.0):
    """[(day, cumulative P&L in dollars)] with each ticket settled on its own day.

    Cumulative P&L rather than an equity multiple: the stake is flat, so an
    equity curve would only encode an arbitrary starting balance.
    """
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t.day] = by_day.get(t.day, 0.0) + (stake / t.fill - stake if t.won else -stake)
    total, curve = 0.0, []
    for day in sorted(by_day):
        total += by_day[day]
        curve.append((day, round(total, 4)))
    return curve


def max_drawdown(curve) -> float:
    """Worst peak-to-trough decline of cumulative P&L, in dollars."""
    peak, worst = float("-inf"), 0.0
    for _, total in curve:
        peak = max(peak, total)
        worst = min(worst, total - peak)
    return round(worst, 2)
