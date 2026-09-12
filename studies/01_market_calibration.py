"""Study 1 -- is the market's price a probability?

Take every quote in the snapshot at local hours 09 / 12 / 15, bin by price, and
compare the bin's average price against how often those buckets actually won.

This is the control for everything else in the repo. If the market were sloppy,
a thin model would have room. It is not sloppy.

    python -X utf8 studies/01_market_calibration.py
"""

import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_events, load_quotes  # noqa: E402
from wxlab.report import REPORTS, calibration_plot  # noqa: E402

HOURS = (9, 12, 15)
EDGES = [0.0, 0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.60, 0.80, 0.95, 1.0]


def main():
    events, quotes = load_events(), load_quotes()
    bins = defaultdict(lambda: {"n": 0, "wins": 0, "price": 0.0})

    for day, event in events.items():
        if not event.settled:
            continue
        for hour in HOURS:
            for bucket in event.buckets:
                mid = quotes.get((day, hour, bucket.label))
                if mid is None:
                    continue
                idx = max(0, min(len(EDGES) - 2,
                                 next(i for i in range(len(EDGES) - 1) if mid < EDGES[i + 1])
                                 if mid < EDGES[-1] else len(EDGES) - 2))
                cell = bins[idx]
                cell["n"] += 1
                cell["price"] += mid
                cell["wins"] += int(bucket.label == event.winner)

    rows, points = [], []
    for idx in sorted(bins):
        cell = bins[idx]
        mean_price = cell["price"] / cell["n"]
        realised = cell["wins"] / cell["n"]
        rows.append({
            "band": f"{EDGES[idx]:.2f}-{EDGES[idx + 1]:.2f}",
            "n": cell["n"], "mean_price": round(mean_price, 4),
            "realised": round(realised, 4), "gap_pp": round(100 * (realised - mean_price), 2),
        })
        points.append((mean_price, realised, cell["n"]))

    os.makedirs(REPORTS, exist_ok=True)
    out = os.path.join(REPORTS, "market_calibration.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    total = sum(r["n"] for r in rows)
    print(f"{total} quote-observations across {len([d for d, e in events.items() if e.settled])} settled days\n")
    print(f"{'price band':>12} {'n':>6} {'mean price':>11} {'realised':>9} {'gap (pp)':>9}")
    for r in rows:
        print(f"{r['band']:>12} {r['n']:>6} {r['mean_price']:>11.3f} "
              f"{r['realised']:>9.3f} {r['gap_pp']:>9.2f}")
    print("\nwrote", out)
    print("wrote", calibration_plot(points))


if __name__ == "__main__":
    main()
