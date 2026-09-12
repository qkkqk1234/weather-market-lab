import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from latelock.core import Rule, bucket, exit_tick, fee, fee_rate, rounded_temperature, stats, sweep, weather_state
from latelock.history import evaluate, first_signal, load_days
from latelock.paper import Paper
from latelock.public import event_day, matching_city, source_station
from latelock.scanner import observed_state

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


def cool_day():
    return {h: (25 if 10 <= h < 14 else 22 if h >= 14 else 18) for h in range(24)}


@pytest.mark.parametrize("label,expected", [("22°C", (22,22,"C")), ("68-69°F", (68,69,"F")),
    ("-4--3°C", (-4,-3,"C")), ("-2°C or below", (-float("inf"),-2,"C")),
    ("28°C or higher", (28,float("inf"),"C"))])
def test_bucket_ranges(label, expected):
    assert bucket(label) == expected


@pytest.mark.parametrize("label", ["rain 22", "22°C tomorrow", "Other", "72-70°F"])
def test_unknown_bucket_rejected(label):
    with pytest.raises(ValueError):
        bucket(label)


def test_no_future_hour_leakage():
    hours = cool_day()
    assert weather_state(hours, 17, Rule())["ok"]
    hours[17] = 40
    assert weather_state(hours, 17, Rule())["ok"]
    assert not weather_state(hours, 18, Rule())["ok"]
    assert evaluate({"2026-06-01": hours}, Rule(), "C")["wins"] == 0


def test_missing_hour_not_a_lock():
    hours = cool_day(); del hours[11]
    assert weather_state(hours, 17, Rule())["reason"] == "missing_hour"


def test_plateau_is_not_cooling():
    assert not weather_state({h:22 for h in range(24)}, 21, Rule())["ok"]


def test_late_rebound_breaks_rule():
    hours = cool_day(); hours[16] = 24
    assert not weather_state(hours, 17, Rule())["ok"]


def test_confidence_never_one_for_finite_sample():
    assert .969 < stats([True]*86)["lower95"] < .97
    assert stats([])["lower95"] == 0


def test_fahrenheit_cooling_uses_correct_scale():
    c = weather_state(cool_day(), 17, Rule(), "C")
    f = weather_state({h:v*1.8+32 for h,v in cool_day().items()}, 17, Rule(), "F")
    assert f["ok"] and f["cooling_c"] == pytest.approx(c["cooling_c"])


@pytest.mark.parametrize("v", [22.5, -1.5, 70.46, float("nan")])
def test_ambiguous_rounding(v):
    with pytest.raises(ValueError):
        rounded_temperature(v)


def test_real_fee_formula_and_unknown_fee_fail_closed():
    assert fee(100, .93, .05) == pytest.approx(.3255)
    assert fee_rate({"feesEnabled": False}) == 0
    with pytest.raises(ValueError):
        fee_rate({})
    with pytest.raises(ValueError):
        fee_rate({"feesEnabled": True, "feeSchedule": {"exponent": 2, "rate": .05}})


def test_dynamic_tick_no_pretend_998_on_cent_market():
    assert exit_tick(.998, .01) is None
    assert exit_tick(.998, .001) == .998
    assert exit_tick(.9981, .001) == .999


def book(ask=.93, bid=.92, size=100, tick=.001):
    return {"asks": [{"price":str(ask),"size":str(size)}],
            "bids": [{"price":str(bid),"size":str(size)}],
            "tick_size": str(tick), "min_order_size": "5"}


def test_depth_partial_insufficient_and_bid_exit():
    assert sweep(book(size=4), "BUY", 5, .95, .05) is None
    assert sweep(book(ask=.998,bid=.95), "SELL", 5, .998, .05) is None
    b=book(size=3); b["asks"].append({"price":".95","size":"4"})
    f=sweep(b, "BUY", 5, .95, .05)
    assert f["cash"] == pytest.approx(3*.93+2*.95)
    assert f["net"] > f["cash"]


def test_event_day_from_contract_not_enddate():
    assert event_day({"slug":"highest-temperature-in-warsaw-on-september-4-2026", "endDate":"2026-09-05T12:00:00Z"}) == "2026-09-04"


def test_station_source_change():
    valid={"description":"highest reading https://www.weather.gov/wrh/timeseries?site=epwa whole degrees"}
    assert source_station(valid) == "EPWA"
    assert source_station({"description":"Weather Underground EPWA"}) is None
    assert matching_city({"series":[{"id":11342}]}, {"warsaw":{"series":"11342"}}) == "warsaw"


def observations(day="2026-09-05", station="EPWA"):
    start=datetime.fromisoformat(day).replace(tzinfo=UTC)
    out=[]
    for h,v in cool_day().items():
        for minute in [0,30]:
            t=start+timedelta(hours=h,minutes=minute)
            out.append({"icaoId":station,"obsTime":int(t.timestamp()),"receiptTime":(t+timedelta(minutes=3)).isoformat(),"temp":v})
    return out


def test_receipt_time_and_current_hour_contradiction():
    city={"timezone":"UTC","station":"EPWA","unit":"C","cadence_minutes":30}
    now=datetime(2026,9,5,17,15,tzinfo=UTC)
    r=observations()
    assert observed_state(r,city,"2026-09-05",now,Rule())["peak"] == 25
    # Current-hour update is a veto, not a leak into features.
    r[34]["temp"]=27
    with pytest.raises(ValueError,match="new_high_after_cutoff"):
        observed_state(r,city,"2026-09-05",now,Rule())
    r[34]["receiptTime"]=(now+timedelta(hours=1)).isoformat()
    assert observed_state(r,city,"2026-09-05",now,Rule())["peak"] == 25


def test_station_cadence_gap_rejects_incomplete_day():
    city={"timezone":"UTC","station":"EPWA","unit":"C","cadence_minutes":30}
    now=datetime(2026,9,5,17,15,tzinfo=UTC)
    r=observations(); del r[20:23]
    with pytest.raises(ValueError,match="observation_gap"):
        observed_state(r,city,"2026-09-05",now,Rule())


def test_dst_local_day():
    city={"timezone":"Europe/Warsaw","station":"EPWA","unit":"C","cadence_minutes":30}
    now=datetime(2026,9,5,22,30,tzinfo=UTC)  # Sep 6 local
    with pytest.raises(ValueError,match="not_local_today"):
        observed_state(observations(),city,"2026-09-05",now,Rule())


def test_history_excludes_incomplete_days_and_cutoff(tmp_path):
    p=tmp_path/"obs.csv"
    p.write_text("t,temp,tmax\n"+"\n".join(f"2026-09-{day:02}T{h:02}:00:00+02:00,20,20" for day in [3,4,5] for h in range(23 if day==3 else 24)),encoding="utf-8")
    assert list(load_days(p,"2026-09-05")) == ["2026-09-04"]


class FakePublic:
    def __init__(self):
        self.current_book=book()
        self.market={"clobTokenIds": '["yes","no"]',"closed":False,"acceptingOrders":True,
                     "feesEnabled":True,"feeSchedule":{"exponent":1,"rate":.05},
                     "outcomePrices": '["0.5","0.5"]'}
    def book(self,token):
        result=copy.deepcopy(self.current_book)
        result["fetched_utc"]=datetime.now(UTC).isoformat()
        return result
    def event(self,slug):
        return {"markets":[self.market]}


def candidate(now):
    return {"slug":"example-day","city":"warsaw","day":"2026-09-05","token":"yes","label":"22°C",
            "status":"paper_candidate","weather_lower95":.99,"fee_rate":.05,"weather":{"local_time":now.isoformat()}}


def open_position(account,api,now):
    account.step(api,[candidate(now)],now)
    st=account.step(api,[candidate(now+timedelta(seconds=120))],now+timedelta(seconds=120))
    assert st["open_positions"] == 1
    return st


def test_paper_restart_dedupe_fees_exit_and_pnl(tmp_path):
    cfg=json.loads((ROOT/"config.json").read_text())
    api=FakePublic(); path=tmp_path/"paper.sqlite"
    account=Paper(path,cfg); now=datetime(2026,9,5,18,tzinfo=UTC)
    first=account.step(api,[candidate(now)],now)
    assert first["open_positions"] == 0  # Needs another observation
    account.db.close(); account=Paper(path,cfg)
    second=account.step(api,[candidate(now+timedelta(seconds=120))],now+timedelta(seconds=120))
    assert second["open_positions"] == 1 and second["open_cost"] <= 10
    cost=second["open_cost"]
    shares=account.db.execute("SELECT shares FROM positions").fetchone()[0]
    api.current_book=book(ask=.999,bid=.998)
    third=account.step(api,[candidate(now+timedelta(seconds=240))],now+timedelta(seconds=240))
    assert third["open_positions"] == 0
    assert third["realized_pnl"] == pytest.approx(shares*.998-fee(shares,.998,.05)-cost)
    assert account.db.execute("SELECT count(*) FROM positions").fetchone()[0] == 1


def test_paper_exit_requires_size_and_correct_tick(tmp_path):
    cfg=json.loads((ROOT/"config.json").read_text())
    account=Paper(tmp_path/"p.sqlite",cfg); api=FakePublic(); now=datetime(2026,9,5,18,tzinfo=UTC)
    open_position(account,api,now)
    api.current_book=book(ask=.999,bid=.998,size=1)
    assert account.step(api,[],now+timedelta(seconds=240))["open_positions"] == 1
    api.current_book=book(ask=.999,bid=.998,tick=.01)
    assert account.step(api,[],now+timedelta(seconds=360))["open_positions"] == 1


def test_losing_settlement_and_deduplication(tmp_path):
    cfg=json.loads((ROOT/"config.json").read_text())
    account=Paper(tmp_path/"p.sqlite",cfg); api=FakePublic(); now=datetime(2026,9,5,18,tzinfo=UTC)
    st=open_position(account,api,now)
    api.market.update(closed=True,umaResolutionStatus="resolved",outcomePrices='["0","1"]')
    end=account.step(api,[],now+timedelta(days=1))
    assert end["realized_pnl"] == pytest.approx(-st["open_cost"])
    again=account.step(api,[],now+timedelta(days=2))
    assert again["cash"] == end["cash"]


def test_price_one_is_not_resolution(tmp_path):
    cfg=json.loads((ROOT/"config.json").read_text())
    account=Paper(tmp_path/"p.sqlite",cfg); api=FakePublic(); now=datetime(2026,9,5,18,tzinfo=UTC)
    open_position(account,api,now)
    api.market.update(closed=True,outcomePrices='["1","0"]')
    assert account.step(api,[],now+timedelta(days=1))["open_positions"] == 1


def test_missing_signal_resets_confirmation(tmp_path):
    cfg=json.loads((ROOT/"config.json").read_text())
    account=Paper(tmp_path/"p.sqlite",cfg); api=FakePublic(); now=datetime(2026,9,5,18,tzinfo=UTC)
    account.step(api,[candidate(now)],now)
    account.step(api,[],now+timedelta(seconds=60))
    st=account.step(api,[candidate(now+timedelta(seconds=120))],now+timedelta(seconds=120))
    assert st["open_positions"] == 0


def test_thin_book_never_fabricates_fill(tmp_path):
    cfg=json.loads((ROOT/"config.json").read_text())
    account=Paper(tmp_path/"p.sqlite",cfg); api=FakePublic(); now=datetime(2026,9,5,18,tzinfo=UTC)
    api.current_book=book(size=4)
    account.step(api,[candidate(now)],now)
    assert account.step(api,[candidate(now+timedelta(seconds=120))],now+timedelta(seconds=120))["open_positions"] == 0


def test_late_plateau_is_only_separate_hypothesis():
    hours=cool_day()
    for h in range(10,24): hours[h]=25
    assert not first_signal(hours,Rule(),"C")
    assert first_signal(hours,Rule(start_hour=20,cooling_c=0,flat_hours=4),"C")["cutoff"] == 20


def test_stale_quote_rejected(tmp_path):
    cfg=json.loads((ROOT/"config.json").read_text())
    account=Paper(tmp_path/"p.sqlite",cfg); api=FakePublic()
    api.book=lambda token:{**book(),"fetched_utc":(datetime.now(UTC)-timedelta(seconds=90)).isoformat()}
    with pytest.raises(ValueError,match="stale_or_future_book_fetch"):
        account.fresh_book(api,"yes")


def test_fall_dst_repeated_hour_retains_higher_observation(tmp_path):
    p=tmp_path/"obs.csv"
    rows=[f"2025-10-26T{h:02}:00:00+01:00,20,20" for h in range(24)]
    rows.insert(2,"2025-10-26T02:00:00+02:00,27,27")
    p.write_text("t,temp,tmax\n"+"\n".join(rows),encoding="utf-8")
    assert load_days(p,"2026-01-01")["2025-10-26"][2] == 27
