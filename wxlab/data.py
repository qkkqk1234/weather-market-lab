"""Loaders for the bundled data snapshot.

Three tables, all plain text so they diff and audit cleanly:

* ``zgsz_metar_hourly.csv.gz``  -- hourly METAR from Shenzhen Bao'an (ICAO ZGSZ),
  the station the market settles on since 2026-08-24. Timestamps are UTC.
* ``shenzhen_events.csv``       -- one row per bucket per market day, with the
  resolved winner where the day has settled.
* ``shenzhen_quotes.csv.gz``    -- mid price per bucket, sampled once per local
  hour, from the CLOB price history.

Nothing here touches the network; see ``wxlab.fetch`` for that.
"""

from __future__ import annotations

import csv
import gzip
import io
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Shenzhen is UTC+8 all year; the market's "day" is the local calendar day.
LOCAL_OFFSET = timedelta(hours=8)

CLEAR_SKY = {"CLR", "SKC", "NSC", "FEW"}
BROKEN_SKY = {"BKN", "OVC", "VV"}


def _open(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return io.open(path, encoding="utf-8", newline="")


@dataclass(frozen=True)
class Obs:
    """One hourly METAR observation, already shifted to local time."""

    temp_c: float
    dewpoint_c: float | None
    sky: str | None
    wind_dir_deg: float | None = None
    wind_kt: float | None = None
    gust_kt: float | None = None
    visibility_mi: float | None = None
    cloud_base_ft: float | None = None

    @property
    def dewpoint_spread(self) -> float | None:
        if self.dewpoint_c is None:
            return None
        return self.temp_c - self.dewpoint_c

    @property
    def sky_bin(self) -> str:
        if self.sky in CLEAR_SKY:
            return "clear"
        if self.sky == "SCT":
            return "scattered"
        if self.sky in BROKEN_SKY:
            return "overcast"
        return "unknown"

    @property
    def wind_compass(self) -> str | None:
        """16-point compass label, for prompts and eyeballing."""
        if self.wind_dir_deg is None:
            return None
        points = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
        return points[int((self.wind_dir_deg % 360) / 22.5 + 0.5) % 16]

    @property
    def onshore(self) -> bool | None:
        """True when the wind has a sea-breeze component.

        Bao'an sits on the east side of the Pearl River estuary, so the water
        is roughly south through west. A veer into that arc during the
        afternoon is the classic sea breeze signature, and it is the single
        most useful non-temperature field in the report.
        """
        if self.wind_dir_deg is None:
            return None
        return 135 <= self.wind_dir_deg % 360 <= 292.5


@dataclass(frozen=True)
class Bucket:
    label: str
    lo: int  # inclusive; -999 for the "or below" tail
    hi: int  # inclusive; +999 for the "or higher" tail

    def contains(self, temp_c: int) -> bool:
        return self.lo <= temp_c <= self.hi


@dataclass(frozen=True)
class Event:
    day: str
    buckets: tuple[Bucket, ...]
    winner: str | None  # bucket label, or None if not settled yet

    @property
    def settled(self) -> bool:
        return self.winner is not None


class Metar:
    """Hourly observations keyed by (local day, local hour)."""

    def __init__(self, obs: dict[tuple[str, int], Obs]):
        self._obs = obs
        self.days = sorted({day for day, _ in obs})

    def __len__(self):
        return len(self._obs)

    def get(self, day: str, hour: int) -> Obs | None:
        return self._obs.get((day, hour))

    def hours(self, day: str) -> list[int]:
        return sorted(h for d, h in self._obs if d == day)

    def running_max(self, day: str, hour: int) -> float | None:
        """Highest temperature seen so far today, at ``hour``.

        This is the quantity the market settles on: the day's high can only ever
        go up, so the running max is a hard floor on the final answer.
        """
        vals = [self._obs[(day, h)].temp_c for h in range(hour + 1) if (day, h) in self._obs]
        return max(vals) if vals else None

    def daily_max(self, min_reports: int = 20) -> dict[str, float]:
        """Local-day high, restricted to days with a near-complete report set.

        ``min_reports`` guards against half-days at the edge of a data pull
        silently becoming low outliers in the training set.
        """
        buckets: dict[str, list[float]] = defaultdict(list)
        for (day, _), ob in self._obs.items():
            buckets[day].append(ob.temp_c)
        return {d: max(v) for d, v in buckets.items() if len(v) >= min_reports}

    def rise(self, day: str, hour: int, over: int = 2) -> float | None:
        """Temperature change over the previous ``over`` hours."""
        now, before = self.get(day, hour), self.get(day, hour - over)
        if now is None or before is None:
            return None
        return now.temp_c - before.temp_c


def _num(row, key):
    """IEM writes 'M' for missing and 'T' for trace; both become None."""
    try:
        return float(row[key])
    except (TypeError, ValueError, KeyError):
        return None


def load_metar(path: str | None = None) -> Metar:
    path = path or os.path.join(DATA_DIR, "zgsz_metar_hourly.csv.gz")
    obs: dict[tuple[str, int], Obs] = {}
    with _open(path) as fh:
        for row in csv.DictReader(fh):
            temp = _num(row, "tmpc")
            if temp is None:
                continue
            local = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M") + LOCAL_OFFSET
            obs[(local.date().isoformat(), local.hour)] = Obs(
                temp_c=temp,
                dewpoint_c=_num(row, "dwpc"),
                sky=(row.get("skyc1") or "").strip() or None,
                wind_dir_deg=_num(row, "drct"),
                wind_kt=_num(row, "sknt"),
                gust_kt=_num(row, "gust"),
                visibility_mi=_num(row, "vsby"),
                cloud_base_ft=_num(row, "skyl1"),
            )
    return Metar(obs)


def load_events(path: str | None = None) -> dict[str, Event]:
    path = path or os.path.join(DATA_DIR, "shenzhen_events.csv")
    rows: dict[str, list[tuple[Bucket, bool]]] = defaultdict(list)
    with _open(path) as fh:
        for row in csv.DictReader(fh):
            bucket = Bucket(row["label"], int(row["lo"]), int(row["hi"]))
            rows[row["day"]].append((bucket, row["resolved"] == "1"))
    out = {}
    for day, items in rows.items():
        items.sort(key=lambda it: (it[0].lo, it[0].hi))
        winners = [b.label for b, won in items if won]
        out[day] = Event(day, tuple(b for b, _ in items), winners[0] if winners else None)
    return out


def load_quotes(path: str | None = None) -> dict[tuple[str, int, str], float]:
    """(day, local hour, bucket label) -> mid price."""
    path = path or os.path.join(DATA_DIR, "shenzhen_quotes.csv.gz")
    out = {}
    with _open(path) as fh:
        for row in csv.DictReader(fh):
            out[(row["day"], int(row["hour"]), row["label"])] = float(row["mid"])
    return out
