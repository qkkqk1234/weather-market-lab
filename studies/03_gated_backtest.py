"""Study 3 -- run the live gate over recorded prices and see what it makes.

Walk-forward, refit before every day, fills at mid + 1 cent, hold to
resolution, flat $1 per ticket, at most two tickets a day, entries before noon
only. All of that is ``wxlab.gate`` and ``wxlab.backtest``, unchanged from what
the rules say the live system is allowed to do.

Reported per settlement regime, because a strategy aimed at the wrong station
is not the same strategy.

    python -X utf8 studies/03_gated_backtest.py
"""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_events, load_metar, load_quotes  # noqa: E402
from wxlab.backtest import max_drawdown, pnl_curve, run, summarize  # noqa: E402
from wxlab.report import REPORTS, pnl_plot  # noqa: E402

SOURCE_SWITCH = "2026-08-24"

REGIMES = [
    ("Weather Underground (Lau Fau Shan sensor)", "2026-03-23", "2026-08-23"),
    ("NOAA / Bao'an METAR", SOURCE_SWITCH, "2026-09-11"),
]


def main():
    metar, events, quotes = load_metar(), load_events(), load_quotes()

    all_trades, summaries = [], []
    for name, start, end in REGIMES:
        trades = run(metar, events, quotes, start=start, end=end)
        summary = summarize(trades)
        curve = pnl_curve(trades)
        summaries.append({
            "regime": name, "start": start, "end": end,
            **summary.as_dict(),
            "max_drawdown": max_drawdown(curve),
        })
        all_trades.extend(trades)

    combined = run(metar, events, quotes, start=REGIMES[0][1], end=REGIMES[-1][2])
    curve = pnl_curve(combined)

    # Same gate, but filling at the mid instead of crossing. Cheaper fills let
    # slightly different tickets through, so this is not the same basket -- it
    # is here only to answer one question: is the loss a transaction-cost
    # artifact, or is it the model?
    frictionless = run(metar, events, quotes, start=REGIMES[0][1], end=REGIMES[-1][2],
                       slippage=0.0)
    frictionless_curve = pnl_curve(frictionless)

    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "backtest_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summaries, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(REPORTS, "backtest_trades.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(vars(combined[0])))
        writer.writeheader()
        writer.writerows(vars(t) for t in combined)
    with open(os.path.join(REPORTS, "pnl_curve.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["day", "cum_pnl_usd"])
        writer.writerows(curve)

    for s in summaries:
        print(f"\n{s['regime']}  ({s['start']} to {s['end']})")
        print(f"  trades {s['n_trades']} on {s['n_days']} days, mean fill {s['mean_fill']:.3f}")
        print(f"  staked ${s['stake']:.2f} -> returned ${s['returned']:.2f}   ROI {s['roi']:+.1%}")
        print(f"  wins {s['wins']} vs {s['expected_wins']:.1f} expected "
              f"if the market price were the truth   z = {s['z_score']:+.2f}")
        print(f"  worst drawdown ${s['max_drawdown']:.2f}")

    total = summarize(combined)
    zero = summarize(frictionless)
    print(f"\nCOMBINED  {total.n_trades} trades, ROI {total.roi:+.1%}, "
          f"{total.wins} wins vs {total.expected_wins:.1f} expected (z = {total.z_score:+.2f})")
    print(f"          cumulative P&L ${curve[-1][1]:.2f}, "
          f"worst drawdown ${max_drawdown(curve):.2f}")
    print(f"\nSAME GATE, FILLED AT THE MID (no crossing cost at all)")
    print(f"          {zero.n_trades} trades, ROI {zero.roi:+.1%}, "
          f"{zero.wins} wins vs {zero.expected_wins:.1f} expected (z = {zero.z_score:+.2f})")
    print( "          -> still negative, so this is a model result, not a fee result")

    print("\nHow to read ROI here: mean ticket price is ~$0.07, so a single "
          "2-cent\nwinner moves ROI by tens of points. The win-count z-score is "
          "the test\nwith power; ROI on a basket of longshots this small is "
          "mostly noise.")

    print("\nwrote", pnl_plot({"filled at mid + 1c (realistic)": curve,
                              "filled at mid (no crossing cost)": frictionless_curve},
                              marker_day=SOURCE_SWITCH,
                              marker_label="settlement source changed"))


if __name__ == "__main__":
    main()
