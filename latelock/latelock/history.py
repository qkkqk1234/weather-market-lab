"""Reproducible weather study; historical prices are not order books."""
import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import Rule, bucket, stats, weather_state


def load_days(path, before):
    days = defaultdict(dict)
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            t = datetime.fromisoformat(r["t"])
            if t.date().isoformat() >= before:
                continue
            hourly = days[t.date().isoformat()]
            value = float(r.get("tmax") or r["temp"])
            # Autumn DST can repeat a local hour: retain both hours' maximum.
            hourly[t.hour] = max(hourly.get(t.hour, -float("inf")), value)
    # Finalised hourly aggregates have no receipt history. Use only full 24h
    # days and explicitly label this as a proxy study, not an executable replay.
    return {d: h for d, h in sorted(days.items()) if set(h) == set(range(24))}


def first_signal(hours, rule, unit):
    for cutoff in range(rule.start_hour, 24):
        state = weather_state(hours, cutoff, rule, unit)
        if state["ok"]:
            return state
    return None


def evaluate(days, rule, unit):
    outcomes = []
    for hours in days.values():
        state = first_signal(hours, rule, unit)
        if state:
            # Requiring no higher temperature is conservative for 2 F buckets.
            outcomes.append(max(hours.values()) <= state["peak"])
    return stats(outcomes)


def study(registry, source, output, before="2026-09-05", split="2026-06-01"):
    output.mkdir(parents=True, exist_ok=True)
    profiles, rows, signals, comparisons = {}, [], [], []
    for key, city in registry.items():
        path = source/key/"obs.csv"
        if not path.exists() or not city["history_usable"]:
            profiles[key] = {"status": "no_compatible_history"}
            continue
        days = load_days(path, before)
        train = {d:h for d,h in days.items() if d < split}
        test = {d:h for d,h in days.items() if split <= d < before}
        baseline = {str(h): stats([max(v for k,v in x.items() if k < h) >= max(x.values())
                                  for x in days.values()]) for h in range(17, 22)}
        # Only select among this preregistered time grid on TRAIN. Cooling and
        # flat-period parameters are shared by cities, not tuned on outcomes.
        chosen, training = None, None
        for hour in range(17, 22):
            rule = replace(Rule(), start_hour=hour)
            s = evaluate(train, rule, city["unit"])
            if s["n"] >= 100 and s["lower95"] >= .97:
                chosen, training = rule, s
                break
        rule = chosen or Rule(start_hour=21)
        training = training or evaluate(train, rule, city["unit"])
        validation = evaluate(test, rule, city["unit"])
        # Separate fixed hypothesis, never used to promote a city or place even
        # a paper order: a late plateau can occur without any cooling.
        plateau = Rule(start_hour=20, cooling_c=0, flat_hours=4)
        comparisons.append({"city": key, "rule": asdict(plateau),
                            "train": evaluate(train, plateau, city["unit"]),
                            "validation": evaluate(test, plateau, city["unit"]),
                            "status": "unselected_hypothesis_only"})
        passed = chosen is not None and validation["n"] >= 60 and validation["lower95"] >= .97
        p = {"status": "weather_paper_candidate" if passed else "research_only",
             "rule": asdict(rule), "train": training, "validation": validation,
             "weather_lower95": min(training["lower95"], validation["lower95"]),
             "baseline": baseline, "complete_days": len(days),
             "first_day": min(days, default=None), "last_day": max(days, default=None),
             "split": split, "before": before,
             "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "source_note": city["history_note"],
             "limitation": "not calibrated conditional on cheap asks; no publication/revision history"}
        profiles[key] = p
        rows.append({"city": key, "name": city["name"], "complete_days": len(days),
                     **{f"baseline_{h}": baseline[str(h)]["rate"] for h in range(17,22)},
                     "start_hour": rule.start_hour, "train_n": training["n"],
                     "test_n": validation["n"], "test_win_rate": validation["rate"],
                     "test_lower95": validation["lower95"], "status": p["status"]})
        for day, hours in test.items():
            state = first_signal(hours, rule, city["unit"])
            if state:
                signals.append({"city": key, "day": day, **state,
                                "final_max": max(hours.values()),
                                "stable": max(hours.values()) <= state["peak"]})
    (output/"profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    (output/"weather_signals.json").write_text(json.dumps(signals, ensure_ascii=False), encoding="utf-8")
    (output/"plateau_comparison.json").write_text(json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output/"city_study.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    return profiles


def tape_opportunities(registry, source, output):
    """Observed trades after a causal weather state, NOT simulated fills/P&L.

    Published trade direction is verified from the existing tape writer:
    0=BUY, 1=SELL. A consumed quote is no guarantee our order could also fill.
    """
    signals = json.loads((output/"weather_signals.json").read_text(encoding="utf-8"))
    cache, rows = {}, []
    for signal in signals:
        city, day = signal["city"], signal["day"]
        # Settlement-source switch: prior contracts have different rules.
        if day < "2026-08-24":
            continue
        if city not in cache:
            try:
                cache[city] = (json.loads((source/city/"events.json").read_text(encoding="utf-8")),
                               json.loads((source/city/"tape.json").read_text(encoding="utf-8")))
            except FileNotFoundError:
                cache[city] = ({}, {})
        events, tape = cache[city]
        event = events.get(day, {})
        if not event.get("closed") or not event.get("winner"):
            continue
        matched = []
        for b in event.get("buckets", []):
            try:
                lo, hi, unit = bucket(b["label"])
            except ValueError:
                continue
            if lo <= signal["peak"] <= hi:
                matched.append(b)
        if len(matched) != 1:
            continue
        b = matched[0]
        now = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(registry[city]["timezone"]))
        begin = now + timedelta(hours=signal["cutoff"], minutes=10)
        end = now + timedelta(days=1)
        prints = sorted(tape.get("tape", {}).get(b["yes"], []))
        buys = [r for r in prints if begin.timestamp() <= r[0] < end.timestamp()
                and r[1] == 0 and .90 <= r[2] <= .95]
        first = buys[0] if buys else None
        exits = [r for r in prints if first and r[0] > first[0] and r[1] == 1 and r[2] >= .998]
        rows.append({"city": city, "day": day, "label": b["label"],
                     "signal_local": begin.isoformat(), "winner": event["winner"],
                     "won": event["winner"] == b["label"],
                     "cheap_buy_prints": len(buys), "cheap_buy_shares": sum(r[3] for r in buys),
                     "first_buy_ts": first[0] if first else None,
                     "first_buy_price": first[2] if first else None,
                     "first_target_sell_ts": exits[0][0] if exits else None,
                     "target_sell_print_shares": exits[0][3] if exits else None,
                     "hours_to_target_print": (exits[0][0]-first[0])/3600 if exits else None})
    (output/"tape_opportunities.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"eligible_city_days": len(rows), "days_with_cheap_buy_prints": sum(r["cheap_buy_prints"] > 0 for r in rows),
            "days_with_later_target_sell_print": sum(r["first_target_sell_ts"] is not None for r in rows),
            "warning": "Opportunity evidence only. No proven fills, capacity, portfolio P&L or return estimate."}
