"""A deliberately thin baseline: a backed-off empirical distribution.

The market asks one question: which integer will the day's highest METAR
reading land on? At decision hour ``h`` the running max is already known, and
it is a hard floor. So the only unknown is how much further the day climbs:

    delta = daily_max - temperature_now

``P(delta)`` is estimated from history as a conditional frequency table over
(hour, gap below the running max, 2-hour rise, dewpoint spread, cloud cover),
backing off to coarser cells whenever the fine cell is too thin to trust.

The gap term was missing from the first version and it mattered more than the
other three put together -- see ``gap_bin``. A feature list that cannot express
"the temperature is already two degrees off today's peak" cannot answer the
question this repo is about.

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

# Conditioning fields, in backoff order: the rightmost is dropped first, so the
# leftmost survives longest into thin cells. See gap_bin for why gap leads.
FEATURES = ("gap", "rise", "spread", "sky")


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


def gap_bin(gap: float | None) -> str:
    """How far the current reading sits below the day's running max.

    This turned out to be the most informative field available and it was
    missing from the first version of this model, which is why that version
    was badly under-confident about the day being over. Measured on 2,732 warm
    season days, P(the high is already in):

        hour 13   gap 0: 0.630   gap 1: 0.875   gap 2+: 0.930
        hour 15   gap 0: 0.919   gap 1: 0.978   gap 2+: 0.993

    A model that cannot say "we are already two degrees off the peak" is
    guessing at the one question this repo cares about.
    """
    if gap is None:
        return "unknown"
    if gap <= 0:
        return "at-peak"
    if gap < 2:
        return "off1"
    return "off2+"


@dataclass
class DeltaModel:
    """Hierarchical-backoff empirical PMF over ``delta``.

    ``features`` names the conditioning fields and their backoff order, so an
    ablation is a constructor argument rather than a second model.
    """

    min_support: int = 60
    alpha: float = 0.5  # Laplace smoothing
    months: range = field(default=WARM_MONTHS)
    features: tuple = FEATURES
    use_levels: int = 0  # 0 = all levels; n = keep only the n coarsest
    _levels: list = field(default_factory=list, repr=False)
    n_train_days: int = 0

    @property
    def n_levels(self) -> int:
        return len(self.features) + 1

    def _values(self, metar: Metar, day: str, hour: int, ob) -> tuple:
        running = metar.running_max(day, hour)
        lookup = {
            "gap": lambda: gap_bin(None if running is None else running - ob.temp_c),
            "rise": lambda: rise_bin(metar.rise(day, hour)),
            "spread": lambda: spread_bin(ob.dewpoint_spread),
            "sky": lambda: ob.sky_bin,
        }
        return tuple(lookup[name]() for name in self.features)

    def _keys(self, hour: int, values: tuple):
        """Finest level first; the first level with enough support wins.

        Backoff drops features from the right, so whatever sits leftmost in
        ``features`` survives longest into the thin cells. ``gap`` leads by
        design: it is the field that actually separates the outcome.
        """
        return [(hour,) + values[:i] for i in range(len(values), -1, -1)]

    def _active(self):
        """(key index, level table) pairs actually consulted, coarsest last.

        ``use_levels=1`` leaves only ``(hour,)``, the unconditional control:
        same pipeline, same training window, conditioning switched off.
        """
        keep = self.n_levels if self.use_levels <= 0 else self.use_levels
        skip = self.n_levels - max(1, min(keep, self.n_levels))
        return [(i, self._levels[i]) for i in range(skip, self.n_levels)]

    def fit(self, metar: Metar, *, before: str, hours: range = range(8, 16)) -> "DeltaModel":
        """Train on every warm-season day strictly before ``before``.

        ``before`` is the walk-forward cutoff. Passing the day being traded is
        what keeps the backtest honest: nothing from that day or later can be in
        the table.
        """
        self._levels = [defaultdict(Counter) for _ in range(self.n_levels)]
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
                keys = self._keys(hour, self._values(metar, day, hour, ob))
                for level, key in zip(self._levels, keys):
                    level[key][delta] += 1
                days.add(day)
        self.n_train_days = len(days)
        return self

    def delta_pmf(self, metar: Metar, day: str, hour: int):
        ob = metar.get(day, hour)
        if ob is None or not self._levels:
            return None
        keys = self._keys(hour, self._values(metar, day, hour, ob))
        for index, level in self._active():
            counts = level.get(keys[index])
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

    def lock_pmf(self, metar: Metar, day: str, hour: int, cap: int = 3):
        """P(daily_max - running_max_now = k) for k in 0..cap, top bin inclusive.

        The same four-way question ``wxlab.llm`` puts to a language model, so
        the two are scored on identical outcomes. ``k = 0`` means the day is
        already over.
        """
        temps = self.temperature_pmf(metar, day, hour)
        running = metar.running_max(day, hour)
        if temps is None or running is None:
            return None
        floor = int(round(running))
        out = [0.0] * (cap + 1)
        for temp, p in temps.items():
            out[min(max(temp - floor, 0), cap)] += p
        total = sum(out)
        return [v / total for v in out] if total > 0 else None

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
