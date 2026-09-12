"""Pure rules. Weather confidence is a research proxy, never a guarantee."""
import math
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING


@dataclass(frozen=True)
class Rule:
    start_hour: int = 17
    cooling_c: float = 2.0
    flat_hours: int = 2
    publication_delay_minutes: int = 10


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("non-finite value")
    return value


def stats(outcomes):
    """One-sided 95% Wilson lower bound; days, not minute snapshots."""
    n = len(outcomes)
    if not n:
        return {"n": 0, "wins": 0, "rate": None, "lower95": 0.0}
    wins = sum(outcomes)
    p, z = wins / n, 1.6448536269514722
    low = (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1+z*z/n)
    return {"n": n, "wins": wins, "rate": p, "lower95": low}


def weather_state(hours, cutoff, rule, unit="C"):
    """hours: completed local-hour maxima. NEVER consume hour >= cutoff.

    Use at cutoff:10 or later, reserving ten minutes for publication. The live
    adapter additionally checks actual receipt times and cadence gaps.
    """
    prior = {h: finite(v) for h, v in hours.items() if 0 <= h < cutoff}
    if set(prior) != set(range(cutoff)):
        return {"ok": False, "reason": "missing_hour"}
    peak = max(prior.values())
    recent = [prior[h] for h in range(cutoff-rule.flat_hours, cutoff)]
    last_peak = max(h for h, v in prior.items() if v == peak)
    first_peak = min(h for h, v in prior.items() if v == peak)
    scale = 1.8 if unit == "F" else 1.0
    cooling = (peak - max(recent)) / scale
    ok = (cutoff >= rule.start_hour and cooling >= rule.cooling_c
          and cutoff - 1 - first_peak >= rule.flat_hours
          and recent[-1] <= recent[0])
    return {"ok": ok, "reason": "weather_pass" if ok else "not_locked",
            "peak": peak, "cooling_c": cooling, "last_peak_hour": last_peak,
            "first_peak_hour": first_peak,
            "latest_hour_max": prior[cutoff-1], "cutoff": cutoff}


def bucket(label):
    # Full match: reject unrecognised categories rather than guess a temperature.
    m = re.fullmatch(r"\s*(-?\d+)(?:\s*[-–]\s*(-?\d+))?\s*°([CF])(?:\s+or\s+(below|lower|higher|above))?\s*", label)
    if not m:
        raise ValueError("unsupported bucket: " + label)
    lo, hi, unit, tail = int(m[1]), int(m[2] or m[1]), m[3], m[4]
    if hi < lo:
        raise ValueError("reversed range")
    return (-math.inf if tail in ("below", "lower") else lo,
            math.inf if tail in ("higher", "above") else hi, unit)


def rounded_temperature(value):
    v = finite(value)
    if abs((v - math.floor(v)) - .5) <= .06:
        raise ValueError("rounding_boundary")
    return math.floor(v + .5)


def fee_rate(market):
    if market.get("feesEnabled") is False:
        return 0.0
    f = market.get("feeSchedule") or {}
    if market.get("feesEnabled") is not True or f.get("exponent") != 1:
        raise ValueError("unknown_fee_schedule")
    r = finite(f["rate"])
    if not 0 <= r <= 1:
        raise ValueError("invalid_fee_rate")
    return r


def fee(shares, price, rate):
    return round(shares * rate * price * (1-price), 5)


def exit_tick(target, tick):
    t, x = Decimal(str(tick)), Decimal(str(target))
    if not 0 < t < 1:
        raise ValueError("invalid_tick")
    out = (x / t).to_integral_value(rounding=ROUND_CEILING) * t
    return float(out) if out <= 1-t else None


def levels(book, side):
    out = []
    for row in book["asks" if side == "BUY" else "bids"]:
        p, s = finite(row["price"]), finite(row["size"])
        if not 0 < p < 1 or s < 0:
            raise ValueError("invalid_book_level")
        if s:
            out.append((p, s))
    return sorted(out, reverse=side == "SELL")


def sweep(book, side, shares, limit, rate):
    """Full depth simulation. No midpoint fills or invented counterparties."""
    remaining, cash, fees = shares, 0.0, 0.0
    for p, size in levels(book, side):
        if (side == "BUY" and p > limit) or (side == "SELL" and p < limit):
            break
        take = min(remaining, size)
        cash += take*p
        fees += fee(take, p, rate)
        remaining -= take
        if remaining <= 1e-8:
            break
    if remaining > 1e-8:
        return None
    return {"shares": shares, "cash": cash, "fee": fees,
            "net": cash+fees if side == "BUY" else cash-fees,
            "average": cash/shares}
