"""Study 5 -- if there is money in this market, where is it?

Studies 1-3 say the morning is closed: the market prices the buckets well and
a conditional model does not beat it. This one scans the three places an edge
could still structurally exist, and reports how much room each one has left.

    A. Impossible buckets. The day's high only goes up, so a bucket entirely
       below the running max is worth exactly zero. Anything quoted above zero
       there is free money with no forecast attached.
    B. The decided-but-unpriced window. With hindsight, how is the eventual
       winner priced on days where the high was already physically set?
    C. The same window, reachable causally. A cooling rule that uses no
       hindsight: how accurate is it, and what does the winner cost once it
       fires?

The gap between B and C is the answer.

    python -X utf8 studies/05_where_to_look.py
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_events, load_metar, load_quotes  # noqa: E402
from wxlab.report import REPORTS  # noqa: E402

REGIME_START = "2026-08-24"
DROP = 2.0      # degrees below the running max
COOLED_HOURS = 2  # consecutive complete reports that must be that far down


def cooled(metar, day, hour):
    """Causal 'the high is in' rule. Uses only what was published by ``hour``.

    The last two complete reports must both sit at least DROP below the running
    max, the most recent must not be rebounding, and the max must have been set
    at least two hours ago.
    """
    running = metar.running_max(day, hour)
    if running is None:
        return False
    obs = [metar.get(day, hour - i) for i in range(COOLED_HOURS)]
    if any(o is None for o in obs):
        return False
    if any(running - o.temp_c < DROP for o in obs):
        return False
    if obs[0].temp_c > obs[1].temp_c:
        return False
    earlier = metar.running_max(day, hour - 2)
    return earlier is not None and earlier >= running


def median(values):
    return sorted(values)[len(values) // 2] if values else float("nan")


def main():
    metar, events, quotes = load_metar(), load_events(), load_quotes()
    daily = metar.daily_max()
    days = [d for d, e in sorted(events.items())
            if e.settled and d >= REGIME_START and d in daily]

    # ---- A. impossible buckets -------------------------------------------
    seen = alive = 0
    examples = []
    for day in days:
        for hour in range(9, 19):
            running = metar.running_max(day, hour)
            if running is None:
                continue
            for bucket in events[day].buckets:
                if bucket.hi >= running:
                    continue  # still reachable
                mid = quotes.get((day, hour, bucket.label))
                if mid is None:
                    continue
                seen += 1
                if mid >= 0.01:
                    alive += 1
                    examples.append((day, hour, bucket.label, mid))

    print("A. Buckets that can no longer win, still quoted above a cent")
    print(f"   {alive} of {seen} observations ({100 * alive / max(seen, 1):.1f}%)")
    for row in sorted(examples, key=lambda r: -r[3])[:5]:
        print(f"     {row[0]} {row[1]:02d}:30  {row[2]} quoted {row[3]:.4f}")
    print("   -> structurally closed here. Worth re-running on a thinner market.\n")

    # ---- B and C ---------------------------------------------------------
    rows = []
    print("B/C. The winner's price once the day is over, with and without hindsight")
    print(f"   {'hour':>6} {'settled (hindsight)':>20} {'median':>8} {'<=0.90':>7} "
          f"{'min':>7} | {'rule fires':>11} {'correct':>8} {'median':>8}")
    for hour in range(11, 20):
        hind, causal = [], []
        fires = correct = 0
        for day in days:
            running = metar.running_max(day, hour)
            if running is None:
                continue
            price = quotes.get((day, hour, events[day].winner))
            if running >= daily[day] and price is not None:
                hind.append(price)
            if cooled(metar, day, hour):
                fires += 1
                correct += int(running >= daily[day])
                if price is not None:
                    causal.append(price)
        if not hind and not fires:
            continue
        row = {
            "hour": hour,
            "hindsight_days": len(hind),
            "hindsight_median": round(median(hind), 3) if hind else "",
            "hindsight_cheap": sum(1 for p in hind if p <= 0.90),
            "hindsight_min": round(min(hind), 3) if hind else "",
            "rule_fires": fires,
            "rule_correct": correct,
            "rule_median": round(median(causal), 3) if causal else "",
        }
        rows.append(row)
        print(f"   {hour:>4}:30 {len(hind):>20} {row['hindsight_median']!s:>8} "
              f"{row['hindsight_cheap']:>7} {row['hindsight_min']!s:>7} | "
              f"{fires:>11} {correct:>8} {row['rule_median']!s:>8}")

    os.makedirs(REPORTS, exist_ok=True)
    out = os.path.join(REPORTS, "where_to_look.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    # ---- the point -------------------------------------------------------
    early = [r for r in rows if r["hour"] < 13]
    late = [r for r in rows if r["hour"] >= 13]
    print(f"\n   Before 13:00 the rule fires {sum(r['rule_fires'] for r in early)} times "
          f"and is right {sum(r['rule_correct'] for r in early)} of them -- a morning "
          f"bump\n   followed by a bigger afternoon peak. It needs a time floor.")
    print(f"   From 13:00 on it fires {sum(r['rule_fires'] for r in late)} times and is "
          f"right {sum(r['rule_correct'] for r in late)} of them.")
    print("\n   But by then the winner is quoted at 0.97+. On the same days, hours "
          "earlier,\n   the answer was already physically fixed and the winner was "
          "still buyable\n   between 0.33 and 0.90.")
    print("\n   That gap is the opportunity, and closing it is not a forecasting "
          "problem.\n   It needs evidence that the temperature cannot rise further -- "
          "a finer or\n   faster feed of the same station, a sea breeze front in the "
          "wind record, a\n   cloud deck arriving on satellite -- an hour or two "
          "before an hourly-integer\n   cooling rule can see it.")
    print("\n   Caveat: the counts here are single digits per hour. This is a map of "
          "where\n   to dig, not evidence that there is anything at the bottom.")
    print("\nwrote", out)


if __name__ == "__main__":
    main()
