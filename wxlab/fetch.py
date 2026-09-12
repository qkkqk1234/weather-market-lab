"""Pull the three raw sources from their public endpoints.

Everything here is read-only and unauthenticated. No API key, no wallet, no
order placement -- this package does not contain an execution path at all.

* METAR      : Iowa State ASOS archive (IEM), the same archive NOAA's own
               timeseries page reads from.
* Events     : Polymarket Gamma API, series 11366 = "Highest temperature in
               Shenzhen on <date>".
* Price hist : Polymarket CLOB ``prices-history``.

Refreshing the full bundled snapshot is roughly 2,000 CLOB calls, so the repo
ships a snapshot and you only need this to extend it. See ``data/README.md``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "wxlab/0.1 (open-source research; https://github.com/qkkqk1234/weather-market-lab)"
IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SHENZHEN_SERIES = 11366


def _get(url: str, params: dict, *, tries: int = 4, timeout: int = 90):
    """GET with a browser-ish UA (Gamma 403s without one) and linear backoff."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - network
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET failed after {tries} tries: {full}") from last


def fetch_metar_csv(station: str = "ZGSZ", start: str = "2014-01-01", end: str = "2026-12-31") -> str:
    """Raw IEM CSV: station,valid,tmpc,dwpc,skyc1,skyl1 with ``valid`` in UTC.

    ``report_type=3`` keeps routine hourly METAR only. ZGSZ publishes no SPECI
    (verified: report_type=4 returns zero rows), so the hourly set is the whole
    record the market reads.
    """
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return _get(IEM, {
        "station": station, "data": ["tmpc", "dwpc", "skyc1", "skyl1"],
        "year1": s.year, "month1": s.month, "day1": s.day,
        "year2": e.year, "month2": e.month, "day2": e.day,
        "tz": "UTC", "format": "onlycomma", "latlon": "no",
        "missing": "M", "trace": "T", "direct": "no", "report_type": 3,
    })


def fetch_events(series_id: int = SHENZHEN_SERIES, limit: int = 2000) -> list[dict]:
    """All events in a series. Gamma caps a page at 100, so this pages."""
    out: list[dict] = []
    offset, page = 0, 100
    while offset < limit:
        chunk = json.loads(_get(f"{GAMMA}/events", {
            "series_id": series_id, "limit": page, "offset": offset,
            "order": "endDate", "ascending": "false",
        }))
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
    return out


def fetch_price_history(token_id: str, start: datetime, end: datetime, fidelity: int = 60):
    """[(utc datetime, mid price)] for one outcome token.

    Gotcha worth keeping: ``interval=max`` silently clamps fidelity to 10
    minutes. Always pass explicit ``startTs``/``endTs``.
    """
    raw = json.loads(_get(f"{CLOB}/prices-history", {
        "market": token_id,
        "startTs": int(start.timestamp()), "endTs": int(end.timestamp()),
        "fidelity": fidelity,
    }))
    return [(datetime.fromtimestamp(p["t"], timezone.utc), float(p["p"]))
            for p in raw.get("history", [])]
