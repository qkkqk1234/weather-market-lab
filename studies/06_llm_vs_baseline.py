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
    python -X utf8 studies/06_llm_vs_baseline.py --provider deepseek --days 120
    python -X utf8 studies/06_llm_vs_baseline.py --provider openai-compatible         --base-url https://api.moonshot.cn/v1 --key-env MOONSHOT_API_KEY --model ...
"""

import argparse
import csv
import json
import math
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_metar  # noqa: E402
from wxlab.llm import (MAX_DELTA, PROVIDERS, Usage, build_prompt,  # noqa: E402
                       estimate_cost, predict)
from wxlab.model import DeltaModel  # noqa: E402
from wxlab.report import REPORTS  # noqa: E402

HOURS = (13, 14, 15)
WARM = range(4, 11)

# List prices, $ per million tokens (input, output), as of 2026-09. Kept here
# rather than in the library so a stale number is visible and editable -- check
# the provider's current page before quoting any of these.
PRICES = {"claude-sonnet-5": (3.0, 15.0),
          "gpt-5": (1.25, 10.0),
          "deepseek-chat": (0.27, 1.10)}


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
    ap.add_argument("--provider", choices=tuple(PROVIDERS),
                    help="anthropic/openai/deepseek bill an API key; "
                         "openai-compatible takes --base-url for anything else; "
                         "claude-code/codex drive a local agent CLI headless")
    ap.add_argument("--base-url", help="for --provider openai-compatible")
    ap.add_argument("--key-env", help="environment variable holding the API key")
    ap.add_argument("--model")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--estimate", action="store_true", help="price the run, call nothing")
    ap.add_argument("--dry-run", action="store_true", help="print one prompt and stop")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel calls; the CLI providers take seconds each")
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
        from concurrent.futures import ThreadPoolExecutor

        cls = PROVIDERS[args.provider]
        kwargs = {}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        if args.key_env:
            kwargs["key_env"] = args.key_env
        provider = cls(args.model, **kwargs) if (args.model or kwargs) else cls()
        todo = sorted(outcomes)
        print(f"\nquerying {provider.name}/{provider.model}, {len(todo)} points, "
              f"{args.workers} worker(s) ...")

        def one(key):
            day, hour = key
            try:
                return key, predict(provider, metar, day, hour,
                                    use_cache=not args.no_cache)
            except Exception as exc:  # noqa: BLE001 - one bad day must not end a run
                print(f"  {day} {hour}:00 failed: {exc}")
                return key, None

        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for key, pred in pool.map(one, todo):
                done += 1
                if pred is not None:
                    llm[key] = pred.pmf
                    usage.add(pred)
                if done % 15 == 0:
                    print(f"  {done}/{len(todo)}, {usage.cached} cached, "
                          f"{done - len(llm)} unusable")
        tokens = (f", {usage.input_tokens:,} in / {usage.output_tokens:,} out tokens"
                  if usage.input_tokens else " (provider reports no token counts)")
        print(f"  done: {len(llm)} usable of {len(todo)}, "
              f"{usage.cached} from cache{tokens}")

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
