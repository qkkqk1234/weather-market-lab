"""Study 6 -- can a language model tell the day is already over?

Study 5 located the one place this market is still visibly wrong: on days that
are already physically decided, the winning bucket stays cheap for one to three
hours longer than it should. This script is the evaluation of the only thing
that would close that gap.

The question, at local hour h: how much higher will the day finish above the
running max already observed? Four outcomes: 0 (the day is over), 1, 2, 3+.

Three predictors are scored on identical days and identical outcomes:

  * the empirical table, refit before each day (walk-forward, no leakage)
  * a language model shown the same published hourly trace, nothing more
  * the trivial baseline: the unconditional frequency for that hour

No API key: run with --estimate to price the run, or --dry-run to print a
sample prompt. Replies are cached on disk, so a completed run is reproducible
by anyone with the cache and costs nothing to re-score.

    python -X utf8 studies/06_llm_vs_baseline.py --estimate
    python -X utf8 studies/06_llm_vs_baseline.py --dry-run
    python -X utf8 studies/06_llm_vs_baseline.py --provider anthropic --days 120
"""

import argparse
import csv
import json
import math
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_metar  # noqa: E402
from wxlab.llm import (MAX_DELTA, Usage, build_prompt, estimate_cost,  # noqa: E402
                       predict)
from wxlab.model import DeltaModel  # noqa: E402
from wxlab.report import REPORTS  # noqa: E402

HOURS = (13, 14, 15)
WARM = range(4, 11)

# Published list prices, $ per million tokens, as of 2026-09. Passed in rather
# than hidden in the library so a stale number is visible and editable.
PRICES = {"claude-sonnet-5": (3.0, 15.0), "gpt-5": (1.25, 10.0)}


def held_out_days(metar, n):
    """The most recent n complete warm-season days, newest last."""
    daily = metar.daily_max()
    days = [d for d in metar.days
            if d in daily and int(d[5:7]) in WARM
            and all(metar.get(d, h) is not None for h in HOURS)]
    return days[-n:]


def truth(metar, daily, day, hour):
    running = metar.running_max(day, hour)
    return min(max(int(round(daily[day] - running)), 0), MAX_DELTA)


def log_loss(pmf, outcome):
    return -math.log(max(pmf[outcome], 1e-6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("anthropic", "openai"))
    ap.add_argument("--model")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--estimate", action="store_true", help="price the run, call nothing")
    ap.add_argument("--dry-run", action="store_true", help="print one prompt and stop")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    metar = load_metar()
    daily = metar.daily_max()
    days = held_out_days(metar, args.days)
    print(f"{len(days)} held-out warm-season days, {days[0]} to {days[-1]}, "
          f"hours {HOURS}\n")

    if args.dry_run:
        print(build_prompt(metar, days[-1], HOURS[1]))
        return

    if args.estimate or not args.provider:
        est = estimate_cost(metar, days, HOURS, PRICES)
        print("Run size and cost, before spending anything:")
        print(f"  calls           {est['calls']}")
        print(f"  input tokens    ~{est['input_tokens']:,}")
        print(f"  output tokens   ~{est['output_tokens']:,}")
        for label in PRICES:
            print(f"  {label:<15} ~${est['usd_' + label]:.2f}")
        if not args.provider:
            print("\nNo --provider given, so nothing was called. Add one plus the "
                  "matching\nAPI key in the environment to run the evaluation.")
        if args.estimate:
            return

    # ---- the two predictors that need no key ----------------------------
    # Both are refit before every scored day. The unconditional control runs
    # through the identical pipeline with the conditioning features switched
    # off, so the comparison isolates the features and nothing else -- and it
    # is not fitted on the days it is scored against.
    outcomes, base, uncond = {}, {}, {}
    for day in days:
        conditional = DeltaModel().fit(metar, before=day)
        hour_only = DeltaModel(use_levels=1).fit(metar, before=day)
        for hour in HOURS:
            pmf = conditional.lock_pmf(metar, day, hour, cap=MAX_DELTA)
            flat = hour_only.lock_pmf(metar, day, hour, cap=MAX_DELTA)
            if pmf is None or flat is None:
                continue
            base[(day, hour)] = pmf
            uncond[(day, hour)] = flat
            outcomes[(day, hour)] = truth(metar, daily, day, hour)

    # ---- the language model ---------------------------------------------
    llm, usage = {}, Usage()
    if args.provider:
        from wxlab.llm import Anthropic, OpenAI
        cls = Anthropic if args.provider == "anthropic" else OpenAI
        provider = cls(args.model) if args.model else cls()
        print(f"\nquerying {provider.name}/{provider.model} ...")
        for i, day in enumerate(days, 1):
            for hour in HOURS:
                if (day, hour) not in outcomes:
                    continue
                pred = predict(provider, metar, day, hour, use_cache=not args.no_cache)
                if pred is None:
                    continue
                llm[(day, hour)] = pred.pmf
                usage.add(pred)
            if i % 20 == 0:
                print(f"  {i}/{len(days)} days, {usage.cached} cached")
        print(f"  done: {usage.calls} predictions, {usage.cached} from cache, "
              f"{usage.input_tokens:,} in / {usage.output_tokens:,} out tokens")

    # ---- score ------------------------------------------------------------
    rows = []
    print(f"\n{'hour':>5} {'n':>5} {'unconditional':>14} {'empirical table':>16} "
          f"{'language model':>15}")
    for hour in HOURS:
        keys = [k for k in outcomes if k[1] == hour]
        if not keys:
            continue
        u = sum(log_loss(uncond[k], outcomes[k]) for k in keys) / len(keys)
        b = sum(log_loss(base[k], outcomes[k]) for k in keys) / len(keys)
        shared = [k for k in keys if k in llm]
        m = (sum(log_loss(llm[k], outcomes[k]) for k in shared) / len(shared)
             if shared else None)
        rows.append({"hour": hour, "n": len(keys), "unconditional": round(u, 4),
                     "empirical": round(b, 4), "n_llm": len(shared),
                     "llm": round(m, 4) if m is not None else ""})
        print(f"{hour:>5} {len(keys):>5} {u:>14.4f} {b:>16.4f} "
              f"{(f'{m:.4f} (n={len(shared)})' if m is not None else '-'):>15}")

    if rows:
        os.makedirs(REPORTS, exist_ok=True)
        out = os.path.join(REPORTS, "llm_vs_baseline.csv")
        with open(out, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        with open(os.path.join(REPORTS, "llm_usage.json"), "w", encoding="utf-8") as fh:
            json.dump({"provider": args.provider, "model": args.model,
                       "days": len(days), "hours": list(HOURS),
                       "calls": usage.calls, "cached": usage.cached,
                       "input_tokens": usage.input_tokens,
                       "output_tokens": usage.output_tokens}, fh, indent=2)
        print("\nwrote", out)

    print("\nLower is better.")
    beaten = [r for r in rows if r["empirical"] > r["unconditional"]]
    if len(beaten) == len(rows):
        print("\nThe conditioning features are a net negative at every hour here: the\n"
              "empirical table loses to its own unconditional control. Rise, dewpoint\n"
              "spread and cloud cover carry morning information, and by the afternoon\n"
              "they are fitting noise instead. So the bar for anything new is the\n"
              "UNCONDITIONAL column, and the empirical approach has already failed to\n"
              "clear it. That is what makes this window worth asking a model about.")
    elif beaten:
        print(f"\nThe empirical table loses to its unconditional control at "
              f"{len(beaten)} of {len(rows)} hours.")
    if not any(r["llm"] for r in rows):
        print("\nThe language model column is empty: no provider was queried. The\n"
              "evaluation is built and priced; running it needs an API key.")


if __name__ == "__main__":
    main()
