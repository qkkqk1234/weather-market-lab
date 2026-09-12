"""A deliberately thin baseline: a backed-off empirical distribution.

The market asks one question: which integer will the day's highest METAR
reading land on? At decision hour ``h`` the running max is already known, and
it is a hard floor. So the only unknown is how much further the day climbs:

    delta = daily_max - temperature_now

``P(delta)`` is estimated from history as a conditional frequency table over
(hour, 2-hour rise, dewpoint spread, cloud cover), backing off to coarser cells
whenever the fine cell is too thin to trust.

Why an empirical table and not a gradient booster: every number in it traces
back to a countable set of past days, which is what you want when the thing you
are calibrating against is a market that may simply be right. Beating this
baseline is the interesting result; the baseline being fancy is not.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .data import Metar

MAX_DELTA = 10  # degrees; the tail beyond this is pooled into the last bin
WARM_MONTHS = range(4, 11)  # Apr-Oct, the season this market is liquid


def rise_bin(rise: float | None) -> str:
    if rise is None:
        return "unknown"
    if rise >= 3:
        return "rise3+"
    if rise >= 2:
        return "rise2"
    if rise >= 1:
        return "rise1"
    return "flat"


def spread_bin(spread: float | None) -> str:
    if spread is None:
        return "unknown"
    if spread >= 6:
        return "dry"
    if spread >= 3:
        return "mid"
    return "humid"


@dataclass
class DeltaModel:
    """Hierarchical-backoff empirical PMF over ``delta``."""

    min_support: int = 60
    alpha: float = 0.5  # Laplace smoothing
    months: range = field(default=WARM_MONTHS)
    _levels: list = field(default_factory=list, repr=False)
    n_train_days: int = 0

    # Finest level first; the first level with enough support wins.
    @staticmethod
    def _keys(hour: int, rise: str, spread: str, sky: str):
        return [(hour, rise, spread, sky), (hour, rise, spread), (hour, rise), (hour,)]

    def fit(self, metar: Metar, *, before: str, hours: range = range(8, 16)) -> "DeltaModel":
        """Train on every warm-season day strictly before ``before``.

        ``before`` is the walk-forward cutoff. Passing the day being traded is
        what keeps the backtest honest: nothing from that day or later can be in
        the table.
        """
        self._levels = [defaultdict(Counter) for _ in range(4)]
        daily = metar.daily_max()
        days = set()
        for day in metar.days:
            if day >= before or day not in daily:
                continue
            if int(day[5:7]) not in self.months:
                continue
            for hour in hours:
                ob = metar.get(day, hour)
                if ob is None:
                    continue
                delta = max(0, min(int(round(daily[day] - ob.temp_c)), MAX_DELTA))
                keys = self._keys(hour, rise_bin(metar.rise(day, hour)),
                                  spread_bin(ob.dewpoint_spread), ob.sky_bin)
                for level, key in zip(self._levels, keys):
                    level[key][delta] += 1
                days.add(day)
        self.n_train_days = len(days)
        return self

    def delta_pmf(self, metar: Metar, day: str, hour: int):
        ob = metar.get(day, hour)
        if ob is None or not self._levels:
            return None
        keys = self._keys(hour, rise_bin(metar.rise(day, hour)),
                          spread_bin(ob.dewpoint_spread), ob.sky_bin)
        for level, key in zip(self._levels, keys):
            counts = level.get(key)
            if counts and sum(counts.values()) >= self.min_support:
                total = sum(counts.values()) + self.alpha * (MAX_DELTA + 1)
                return [(counts.get(d, 0) + self.alpha) / total for d in range(MAX_DELTA + 1)]
        return None

    def temperature_pmf(self, metar: Metar, day: str, hour: int):
        """PMF over the integer the day will settle on.

        Mass that would land below the running max is folded onto the running
        max itself: the day's high cannot go down.
        """
        pmf = self.delta_pmf(metar, day, hour)
        ob, floor = metar.get(day, hour), metar.running_max(day, hour)
        if pmf is None or ob is None or floor is None:
            return None
        out = defaultdict(float)
        for delta, p in enumerate(pmf):
            out[max(int(round(ob.temp_c + delta)), int(round(floor)))] += p
        total = sum(out.values())
        return {k: v / total for k, v in out.items()}

    def bucket_pmf(self, metar: Metar, day: str, hour: int, buckets):
        """PMF over market buckets, normalised to sum to 1."""
        temps = self.temperature_pmf(metar, day, hour)
        if temps is None:
            return None
        out = {b.label: sum(p for t, p in temps.items() if b.contains(t)) for b in buckets}
        total = sum(out.values())
        if total <= 0:
            return None
        return {k: v / total for k, v in out.items()}


def log_loss(prob: float) -> float:
    return -math.log(max(prob, 1e-6))


def brier(pmf: dict, winner) -> float:
    return sum((p - (1.0 if k == winner else 0.0)) ** 2 for k, p in pmf.items())
