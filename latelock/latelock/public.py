"""Only unauthenticated GET market/weather requests. No order/wallet APIs."""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

UTC = timezone.utc
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def iso(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("timestamp_without_timezone")
    return t


def event_day(event):
    m = re.search(r"-on-([a-z]+)-(\d{1,2})-(\d{4})$", event["slug"])
    if not m:
        raise ValueError("unknown_event_date")
    months = dict(zip(("january february march april may june july august september october november december").split(), range(1,13)))
    return datetime(int(m[3]), months[m[1]], int(m[2])).date().isoformat()


def matching_city(event, registry):
    series = {str(s["id"]) for s in event.get("series", [])}
    found = [key for key, c in registry.items() if c["series"] in series]
    return found[0] if len(found) == 1 else None


def source_station(market):
    text = market.get("description", "")
    stations = set(re.findall(r"https://(?:www\.)?weather\.gov/wrh/timeseries\?site=([a-z0-9]{4})\b", text, re.I))
    if len(stations) != 1 or "highest reading" not in text.lower() or "whole degrees" not in text.lower():
        return None
    return stations.pop().upper()


class Public:
    def __init__(self, data):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "poly-latelock-research/0.1 (public data, no trading)"
        self.last_weather_request = 0

    def get(self, url, params=None):
        r = self.session.get(url, params=params, timeout=(5, 20))
        r.raise_for_status()
        return [] if r.status_code == 204 else r.json()

    def discover(self, now):
        tag = self.get(GAMMA+"/tags/slug/highest-temperature")
        out, seen = [], set()
        for offset in range(0, 5000, 100):
            page = self.get(GAMMA+"/events", {
                "tag_id": tag["id"], "active": "true", "closed": "false",
                "end_date_min": (now-timedelta(days=2)).date().isoformat(),
                "end_date_max": (now+timedelta(days=5)).date().isoformat(),
                "order": "id", "ascending": "true", "limit": 100, "offset": offset})
            if not isinstance(page, list):
                raise ValueError("invalid_discovery")
            new = [e for e in page if e["id"] not in seen]
            if page and not new:
                raise ValueError("discovery_pagination_repeated")
            seen.update(e["id"] for e in new)
            out.extend(e for e in new if e.get("slug", "").startswith("highest-temperature-in-"))
            if len(page) < 100:
                break
        else:
            raise ValueError("discovery_truncated_at_5000")
        (self.data/"discovery.json").write_text(json.dumps({"fetched_utc": now.isoformat(), "events": out}, ensure_ascii=False), encoding="utf-8")
        return out

    def event(self, slug):
        r = self.get(GAMMA+"/events", {"slug": slug})
        if len(r) != 1 or r[0]["slug"] != slug:
            raise ValueError("event_missing_or_ambiguous")
        return r[0]

    def metar(self, station):
        if not re.fullmatch(r"[A-Z0-9]{4}", station):
            raise ValueError("invalid_station")
        wait = .65 - (time.monotonic()-self.last_weather_request)
        if wait > 0:
            time.sleep(wait)
        self.last_weather_request = time.monotonic()
        records = self.get("https://aviationweather.gov/api/data/metar", {"ids": station, "format": "json", "hours": 30})
        if not isinstance(records, list) or len(records) >= 400:
            raise ValueError("metar_invalid_or_truncated")
        stamp = datetime.now(UTC).isoformat()
        # Append raw packets with download time. Later revisions remain auditable.
        with (self.data/"weather.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"fetched_utc": stamp, "station": station, "records": records})+"\n")
        return records

    def book(self, token):
        raw = self.get(CLOB+"/book", {"token_id": token})
        if raw.get("asset_id") != token or not isinstance(raw.get("asks"), list) or not isinstance(raw.get("bids"), list):
            raise ValueError("invalid_book_response")
        now = datetime.now(UTC)
        # Server book timestamp may be old if nothing changed; successful fresh
        # HTTP fetch is saved separately. Reject future timestamps, not quiet books.
        stamp = float(raw["timestamp"])/1000
        if stamp > now.timestamp()+5:
            raise ValueError("future_book_timestamp")
        with (self.data/"books.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"fetched_utc": now.isoformat(), "book": raw})+"\n")
        raw["fetched_utc"] = now.isoformat()
        return raw
