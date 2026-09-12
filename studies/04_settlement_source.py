"""Study 4 -- which thermometer actually decided the market, and when?

On 2026-08-24 the resolution source in the event text changed from Weather
Underground to a NOAA page, with no announcement. Same market, same title, same
buckets. The station behind the number moved 30 km across the bay.

This script is the forensic check that finds that kind of change from outcomes
alone: for four candidate stations, score ``floor(daily max)`` against the
bucket the market actually resolved to, split before and after the switch.

A source that explains 83% of settlements and then 5% of them, on a date you
did not choose, is not a modelling problem. It is a different market.

    python -X utf8 studies/04_settlement_source.py
"""

import csv
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_events, load_metar  # noqa: E402
from wxlab.data import DATA_DIR  # noqa: E402
from wxlab.report import REPORTS  # noqa: E402

SWITCH = "2026-08-24"
CANDIDATES = [
    ("wu_daily_max", "Weather Underground page (Lau Fau Shan sensor)"),
    ("lfs_daily_max", "HKO Lau Fau Shan, official daily max"),
    ("wetland_park_daily_max", "HKO Wetland Park (4 km from Lau Fau Shan)"),
    ("zgsz_daily_max", "Shenzhen Bao'an METAR (ZGSZ)"),
]


def load_candidates():
    path = os.path.join(DATA_DIR, "settlement_candidates.csv")
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["day"]: r for r in csv.DictReader(fh)}


def main():
    events, metar = load_events(), load_metar()
    candidates = load_candidates()
    metar_daily = metar.daily_max()

    tally = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # source -> era -> [hits, n]

    for day, event in sorted(events.items()):
        if not event.settled:
            continue
        era = "after" if day >= SWITCH else "before"
        winner = next(b for b in event.buckets if b.label == event.winner)

        row = candidates.get(day, {})
        for key, _ in CANDIDATES:
            raw = row.get(key, "")
            if key == "zgsz_daily_max" and day in metar_daily:
                raw = metar_daily[day]  # the bundled METAR archive covers both eras
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            cell = tally[key][era]
            cell[1] += 1
            cell[0] += int(winner.contains(int(math.floor(value))))

    rows = []
    for key, name in CANDIDATES:
        before, after = tally[key]["before"], tally[key]["after"]
        rows.append({
            "source": name,
            "before_n": before[1],
            "before_hit": round(before[0] / before[1], 3) if before[1] else None,
            "after_n": after[1],
            "after_hit": round(after[0] / after[1], 3) if after[1] else None,
        })

    os.makedirs(REPORTS, exist_ok=True)
    out = os.path.join(REPORTS, "settlement_source.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Does floor(candidate daily max) land in the bucket that actually won?")
    print(f"split at {SWITCH}\n")
    print(f"{'candidate source':<46} {'before':>16} {'after':>16}")
    for r in rows:
        b = f"{r['before_hit']:.0%} (n={r['before_n']})" if r["before_hit"] is not None else "-"
        a = f"{r['after_hit']:.0%} (n={r['after_n']})" if r["after_hit"] is not None else "-"
        print(f"{r['source']:<46} {b:>16} {a:>16}")
    print("\n'-' = no coverage: the bundled Hong Kong series stop at 2026-08-23, "
          "the day\nbefore the switch. Only the METAR archive spans both eras.")
    print("\nwrote", out)
    print("\nA constant offset does not repair this: METAR minus the WU print has "
          "a median\nof -1 but ranges from -4 to +4 across 146 overlapping days. "
          "The old chain was\nnot mispriced, it was pointed at a different thermometer.")


if __name__ == "__main__":
    main()
