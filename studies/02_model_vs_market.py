"""Study 2 -- does the baseline model know anything the market does not?

For every settled day in the current settlement regime, score the model's
bucket distribution and the market's own implied distribution against the
bucket that actually won. Same days, same hours, same scoring rule.

The model is refit before every day (``fit(before=day)``), so nothing from the
day being scored is in its training table.

    python -X utf8 studies/02_model_vs_market.py
"""

import csv
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_events, load_metar, load_quotes  # noqa: E402
from wxlab.model import DeltaModel, log_loss  # noqa: E402
from wxlab.report import REPORTS, skill_plot  # noqa: E402

REGIME_START = "2026-08-24"  # the day settlement moved to Bao'an METAR
HOURS = (9, 10, 11, 12, 13, 14, 15)


def implied(quotes, day, hour, buckets):
    """Market's implied PMF: mids renormalised to sum to 1 (strips the overround)."""
    mids = [quotes.get((day, hour, b.label)) for b in buckets]
    if any(m is None for m in mids):
        return None
    total = sum(mids)
    if total <= 0:
        return None
    return {b.label: m / total for b, m in zip(buckets, mids)}


def main():
    metar, events, quotes = load_metar(), load_events(), load_quotes()
    days = [d for d, e in sorted(events.items()) if e.settled and d >= REGIME_START]
    models = {d: DeltaModel().fit(metar, before=d) for d in days}

    rows = []
    for hour in HOURS:
        model_ll, market_ll, model_hit, market_hit = [], [], [], []
        for day in days:
            event = events[day]
            model_pmf = models[day].bucket_pmf(metar, day, hour, event.buckets)
            market_pmf = implied(quotes, day, hour, event.buckets)
            if model_pmf is None or market_pmf is None:
                continue
            model_ll.append(log_loss(model_pmf[event.winner]))
            market_ll.append(log_loss(market_pmf[event.winner]))
            model_hit.append(int(max(model_pmf, key=model_pmf.get) == event.winner))
            market_hit.append(int(max(market_pmf, key=market_pmf.get) == event.winner))
        if not model_ll:
            continue
        rows.append({
            "hour": hour, "n_days": len(model_ll),
            "model_logloss": round(statistics.mean(model_ll), 3),
            "market_logloss": round(statistics.mean(market_ll), 3),
            "model_top1": round(statistics.mean(model_hit), 3),
            "market_top1": round(statistics.mean(market_hit), 3),
        })

    os.makedirs(REPORTS, exist_ok=True)
    out = os.path.join(REPORTS, "model_vs_market.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"regime from {REGIME_START}, {len(days)} settled days, "
          f"model trained on ~{models[days[-1]].n_train_days} warm-season days\n")
    print(f"{'hour':>5} {'n':>4} {'model LL':>9} {'market LL':>10} "
          f"{'model top-1':>12} {'market top-1':>13}")
    for r in rows:
        print(f"{r['hour']:>5} {r['n_days']:>4} {r['model_logloss']:>9.3f} "
              f"{r['market_logloss']:>10.3f} {r['model_top1']:>12.1%} {r['market_top1']:>13.1%}")

    print("\nwrote", out)
    print("wrote", skill_plot([r["hour"] for r in rows],
                              [r["model_logloss"] for r in rows],
                              [r["market_logloss"] for r in rows]))


if __name__ == "__main__":
    main()
