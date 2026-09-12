import json
import math
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .core import Rule, bucket, finite, fee_rate, levels, rounded_temperature, weather_state
from .public import UTC, event_day, iso, matching_city, source_station


def observed_state(records, city, day, now, rule):
    tz = ZoneInfo(city["timezone"])
    local = now.astimezone(tz)
    if local.date().isoformat() != day:
        raise ValueError("not_local_today")
    cutoff = (local - timedelta(minutes=rule.publication_delay_minutes)).hour
    if cutoff < rule.start_hour:
        raise ValueError("before_city_window")
    packets = {}
    for r in records:
        if r.get("icaoId") != city["station"] or r.get("temp") is None:
            continue
        t, receipt = iso(r["obsTime"]), iso(r["receiptTime"])
        if t > now or receipt > now:
            continue
        if t.astimezone(tz).date().isoformat() != day:
            continue
        previous = packets.get(t)
        if previous is None or receipt > previous[0]:
            value = finite(r["temp"])
            if city["unit"] == "F":
                value = value*1.8+32
            packets[t] = (receipt, value)
    times = sorted(packets)
    if not times:
        raise ValueError("no_observations")
    age = (now-times[-1]).total_seconds()/60
    cadence = city.get("cadence_minutes", 60)
    if age > cadence+15:
        raise ValueError("stale_observations")
    start = datetime.fromisoformat(day).replace(tzinfo=tz).astimezone(UTC)
    gaps = [(b-a).total_seconds()/60 for a,b in zip([start]+times, times)]
    if max(gaps) > cadence+20:
        raise ValueError("observation_gap")
    hours = {}
    for t in times:
        h = t.astimezone(tz).hour
        value = packets[t][1]
        hours[h] = max(hours.get(h, -math.inf), value)
    state = weather_state(hours, cutoff, rule, city["unit"])
    if not state["ok"]:
        raise ValueError(state["reason"])
    # Check data from the incomplete current hour too, but do not use it in
    # historical features; this only vetoes a fresh contradictory observation.
    current_max = max(v[1] for v in packets.values())
    if current_max > state["peak"] + 1e-6:
        raise ValueError("new_high_after_cutoff")
    state["settlement_temperature"] = rounded_temperature(state["peak"])
    state["fast_age_minutes"] = age
    state["observations"] = len(times)
    state["local_time"] = local.isoformat()
    return state


def evaluate_event(api, event, key, city, profile, now, config):
    row = {"slug": event["slug"], "city": key, "day": event_day(event), "status": "skip"}
    try:
        if not city["history_usable"]:
            raise ValueError("incompatible_historical_station_or_unit")
        if "rule" not in profile:
            raise ValueError("no_city_profile")
        if profile["before"] > row["day"]:
            raise ValueError("profile_contains_future_data")
        # Profiles older than 60 days are research evidence only.
        age = (datetime.fromisoformat(row["day"])-datetime.fromisoformat(profile["before"])).days
        if age > 60:
            raise ValueError("profile_needs_refresh")
        markets = event["markets"]
        if any(source_station(m) != city["station"] for m in markets):
            raise ValueError("settlement_source_or_station_changed")
        rule = Rule(**profile["rule"])
        local = now.astimezone(ZoneInfo(city["timezone"]))
        if local.date().isoformat() != row["day"]:
            raise ValueError("not_local_today")
        if (local-timedelta(minutes=rule.publication_delay_minutes)).hour < rule.start_hour:
            raise ValueError("before_city_window")
        state = observed_state(api.metar(city["station"]), city, row["day"], datetime.now(UTC), rule)
        row["weather"] = state
        matches = []
        for m in markets:
            lo, hi, unit = bucket(m["groupItemTitle"])
            if unit != city["unit"]:
                raise ValueError("unit_changed")
            if lo <= state["settlement_temperature"] <= hi:
                matches.append(m)
        if len(matches) != 1:
            raise ValueError("no_unique_bucket")
        m = matches[0]
        if m.get("closed") or m.get("acceptingOrders") is not True:
            raise ValueError("market_not_accepting_orders")
        outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
        tokens = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
        token = tokens[outcomes.index("Yes")]
        rate = fee_rate(m)
        book = api.book(token)
        asks, bids = levels(book, "BUY"), levels(book, "SELL")
        if not asks or not bids:
            raise ValueError("empty_book_side")
        ask, bid = asks[0][0], bids[0][0]
        if bid >= ask:
            raise ValueError("crossed_book")
        row.update({"token": token, "label": m["groupItemTitle"], "ask": ask, "bid": bid,
                    "fee_rate": rate, "tick": finite(book["tick_size"]),
                    "min_size": finite(book["min_order_size"]), "book": book,
                    "weather_lower95": profile["weather_lower95"]})
        if not config["entry_min"] <= ask <= config["entry_max"]:
            raise ValueError("entry_price_outside_band")
        if ask-bid > config["max_spread"]:
            raise ValueError("spread_too_wide")
        # This is merely a paper gate; cheap-price conditional calibration and
        # portfolio return must be verified before any live implementation.
        if profile["status"] != "weather_paper_candidate":
            raise ValueError("insufficient_out_of_time_weather_evidence")
        q = profile["weather_lower95"]
        conservative_edge = q*(config["target_exit"]-rate*config["target_exit"]*(1-config["target_exit"])) - ask-rate*ask*(1-ask)
        row["proxy_edge"] = conservative_edge
        if conservative_edge < config["min_proxy_edge"]:
            raise ValueError("insufficient_proxy_edge")
        row.update(status="paper_candidate", reason="weather_and_book_pass")
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        row["reason"] = str(exc)
    except Exception as exc:
        row["reason"] = f"data_error:{type(exc).__name__}:{exc}"
    return row


def scan(api, registry, profiles, config, cities=None):
    now = datetime.now(UTC)
    events = api.discover(now)
    rows, coverage = [], {}
    for event in events:
        key = matching_city(event, registry)
        label = event["title"].split(" on ")[0]
        coverage[label] = {"registered_city": key, "station": source_station(event["markets"][0]) if event.get("markets") else None}
        if not key:
            rows.append({"slug": event["slug"], "status": "skip", "reason": "unregistered_city_needs_station_timezone_history"})
        elif cities is None or key in cities:
            rows.append(evaluate_event(api, event, key, registry[key], profiles.get(key, {}), now, config))
    summary = {"timestamp": now.isoformat(), "events": len(events), "discovered_cities": len(coverage),
               "registered_discovered_cities": sum(v["registered_city"] is not None for v in coverage.values()),
               "reasons": dict(Counter(r.get("reason") for r in rows)), "coverage": coverage}
    return rows, summary
