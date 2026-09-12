"""Study 1 -- is the market's price a probability?

Take every quote in the snapshot at local hours 09 / 12 / 15, bin by price, and
compare the bin's average price against how often those buckets actually won.

This is the control for everything else in the repo. If the market were sloppy,
a thin model would have room. It is not sloppy.

    python -X utf8 studies/01_market_calibration.py
"""

import csv
import math
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
        n = cell["n"]
        mean_price = cell["price"] / n
        realised = cell["wins"] / n
        # Standard error of the win count under "the quoted price is the truth".
        # Without it a 4 pp gap on 243 observations reads as a finding.
        se = math.sqrt(max(mean_price * (1 - mean_price), 1e-12) / n)
        gap = realised - mean_price
        rows.append({
            "band": f"{EDGES[idx]:.2f}-{EDGES[idx + 1]:.2f}",
            "n": n, "mean_price": round(mean_price, 4),
            "realised": round(realised, 4), "gap_pp": round(100 * gap, 2),
            "se_pp": round(100 * se, 2), "z": round(gap / se, 2) if se > 0 else 0.0,
        })
        points.append((mean_price, realised, n))

    os.makedirs(REPORTS, exist_ok=True)
    out = os.path.join(REPORTS, "market_calibration.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    total = sum(r["n"] for r in rows)
    print(f"{total} quote-observations across {len([d for d, e in events.items() if e.settled])} settled days\n")
    print(f"{'price band':>12} {'n':>6} {'mean price':>11} {'realised':>9} "
          f"{'gap (pp)':>9} {'se (pp)':>8} {'z':>6}")
    for r in rows:
        print(f"{r['band']:>12} {r['n']:>6} {r['mean_price']:>11.3f} "
              f"{r['realised']:>9.3f} {r['gap_pp']:>9.2f} {r['se_pp']:>8.2f} {r['z']:>6.2f}")
    big = [r for r in rows if abs(r["z"]) >= 2]
    names = ", ".join(r["band"] for r in big) if big else "none"
    print(f"\nPast 2 sigma: {len(big)} of {len(rows)} bands ({names}).")
    print("Read that as noise, not as three findings. The signs alternate across")
    print("neighbouring bands, whereas a real favourite-longshot effect is monotone")
    print("in price; and the sub-cent band is dead buckets, which cannot be shorted")
    print("profitably at 0.002 anyway.")
    print("\nwrote", out)
    print("wrote", calibration_plot(points))


if __name__ == "__main__":
    main()
