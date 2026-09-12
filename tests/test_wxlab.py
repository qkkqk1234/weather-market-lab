"""Tests that protect the two things a backtest can silently get wrong:
leakage, and the settlement rule.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_events, load_metar, load_quotes
from wxlab.backtest import max_drawdown, pnl_curve, run, summarize
from wxlab.data import Bucket, Metar, Obs
from wxlab.gate import check, required_multiple
from wxlab.model import DeltaModel

SWITCH = "2026-08-24"


@pytest.fixture(scope="module")
def metar():
    return load_metar()


@pytest.fixture(scope="module")
def events():
    return load_events()


# --------------------------------------------------------------- data shape


def test_snapshot_loads(metar, events):
    assert len(metar) > 100_000
    assert len(events) > 150
    assert sum(1 for e in events.values() if e.settled) > 150


def test_running_max_is_monotone(metar):
    for day in metar.days[-30:]:
        seen = [metar.running_max(day, h) for h in range(6, 20)]
        seen = [v for v in seen if v is not None]
        assert seen == sorted(seen), f"{day} running max went down"


def test_daily_max_skips_partial_days():
    sparse = Metar({("2026-01-01", h): Obs(20.0, 15.0, "CLR") for h in range(5)})
    assert sparse.daily_max() == {}


def test_bucket_tails():
    assert Bucket("25C or below", -999, 25).contains(19)
    assert Bucket("33C or higher", 33, 999).contains(41)
    assert not Bucket("30C", 30, 30).contains(31)


# ------------------------------------------------------ the settlement rule


def test_metar_daily_max_explains_every_settlement_after_the_switch(metar, events):
    """Since the source change, settlement is exactly floor(max hourly METAR).

    If this ever fails, the resolution source moved again and every number in
    reports/ is about a market that no longer exists.
    """
    daily = metar.daily_max()
    checked = 0
    for day, event in events.items():
        if not event.settled or day < SWITCH or day not in daily:
            continue
        winner = next(b for b in event.buckets if b.label == event.winner)
        assert winner.contains(int(daily[day])), f"{day}: METAR {daily[day]} vs {event.winner}"
        checked += 1
    assert checked >= 15


# --------------------------------------------------------------- no leakage


def test_model_cannot_see_the_day_it_prices(metar):
    """The cutoff is exclusive: a day's own observations never enter its table."""
    model = DeltaModel().fit(metar, before="2026-06-01")
    counted = {d for d in metar.days if d < "2026-06-01" and 4 <= int(d[5:7]) <= 10}
    assert model.n_train_days <= len(counted)
    assert model.n_train_days > 1000
    later = DeltaModel().fit(metar, before="2026-09-01")
    assert later.n_train_days > model.n_train_days


def test_pmf_sums_to_one_and_respects_the_floor(metar, events):
    day = "2026-09-08"
    model = DeltaModel().fit(metar, before=day)
    pmf = model.temperature_pmf(metar, day, 11)
    assert pmf is not None
    assert abs(sum(pmf.values()) - 1.0) < 1e-9
    floor = int(metar.running_max(day, 11))
    assert min(pmf) >= floor, "mass placed below the running max"

    buckets = model.bucket_pmf(metar, day, 11, events[day].buckets)
    assert abs(sum(buckets.values()) - 1.0) < 1e-9


# ---------------------------------------------------------------- the gate


def test_tiers_are_strict_at_the_cheap_end():
    assert required_multiple(0.01) == 10.0
    assert required_multiple(0.05) == 4.0
    assert required_multiple(0.20) == 2.0
    assert required_multiple(0.40) is None


@pytest.mark.parametrize("hour,model_p,price,used,expected", [
    (13, 0.90, 0.05, 0, False),   # after the noon cutoff
    (11, 0.90, 0.05, 2, False),   # daily ticket cap
    (11, 0.90, 0.40, 0, False),   # above the limit cap
    (11, 0.10, 0.05, 0, False),   # 2x where 4x is required
    (11, 0.30, 0.05, 0, True),    # 6x at a 4x tier
])
def test_gate_rules(hour, model_p, price, used, expected):
    assert check(hour=hour, model_p=model_p, price=price, tickets_used=used).passed is expected


# ------------------------------------------------------------- backtesting


def test_backtest_is_reproducible_and_honest(metar, events):
    quotes = load_quotes()
    trades = run(metar, events, quotes, start=SWITCH, end="2026-09-11")
    assert trades, "no trades in the recent regime"
    assert all(t.hour <= 12 for t in trades), "gate let through an afternoon entry"
    assert all(t.fill <= 0.25 for t in trades), "gate let through a rich ticket"
    assert all(t.fill > t.mid for t in trades), "a fill did not pay to cross"

    again = run(metar, events, quotes, start=SWITCH, end="2026-09-11")
    assert [vars(t) for t in trades] == [vars(t) for t in again]

    summary = summarize(trades)
    assert summary.n_trades == len(trades)
    assert max_drawdown(pnl_curve(trades)) <= 0
