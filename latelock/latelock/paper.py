"""Persistent, conservative paper ledger. No credentials or real execution."""
import json
import math
import sqlite3
from datetime import datetime

from .core import exit_tick, fee_rate, levels, sweep
from .public import UTC, iso


class Paper:
    def __init__(self, path, config):
        self.config = config
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY, initial REAL, cash REAL);
          CREATE TABLE IF NOT EXISTS positions(slug TEXT PRIMARY KEY, city TEXT, day TEXT,
            token TEXT, label TEXT, shares REAL, cost REAL, opened REAL,
            status TEXT, closed REAL, proceeds REAL, exit_kind TEXT);
          CREATE TABLE IF NOT EXISTS confirmations(slug TEXT PRIMARY KEY, token TEXT, ts REAL);
          CREATE TABLE IF NOT EXISTS journal(ts REAL, kind TEXT, payload TEXT);
        """)
        self.db.execute("INSERT OR IGNORE INTO account VALUES(1,?,?)", (config["starting_equity"], config["starting_equity"]))
        self.db.commit()
        initial = self.db.execute("SELECT initial FROM account WHERE id=1").fetchone()[0]
        if initial != config["starting_equity"]:
            raise ValueError("existing_paper_account_has_different_initial_equity")

    def log(self, now, kind, payload):
        self.db.execute("INSERT INTO journal VALUES(?,?,?)", (now.timestamp(), kind, json.dumps(payload, ensure_ascii=False)))

    def fresh_book(self, api, token):
        book = api.book(token)
        age = (datetime.now(UTC)-iso(book["fetched_utc"])).total_seconds()
        if not 0 <= age <= self.config["max_quote_age_seconds"]:
            raise ValueError("stale_or_future_book_fetch")
        return book

    def status(self):
        cash = self.db.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]
        opened = self.db.execute("SELECT count(*),coalesce(sum(cost),0) FROM positions WHERE status='open'").fetchone()
        realized = self.db.execute("SELECT coalesce(sum(proceeds-cost),0) FROM positions WHERE status='closed'").fetchone()[0]
        return {"cash": cash, "open_positions": opened[0], "open_cost": opened[1],
                "realized_pnl": realized, "equity_at_cost": cash+opened[1],
                "valuation": "cost basis, not liquidation NAV", "mode": "paper, estimated fills only"}

    def close(self, position, proceeds, kind, now):
        self.db.execute("UPDATE positions SET status='closed',closed=?,proceeds=?,exit_kind=? WHERE slug=? AND status='open'",
                        (now.timestamp(), proceeds, kind, position["slug"]))
        self.db.execute("UPDATE account SET cash=cash+? WHERE id=1", (proceeds,))
        self.log(now, kind, {"slug": position["slug"], "proceeds": proceeds, "pnl": proceeds-position["cost"]})

    def exits(self, api, now):
        for p in self.db.execute("SELECT * FROM positions WHERE status='open'").fetchall():
            try:
                event = api.event(p["slug"])
                selected = []
                for m in event["markets"]:
                    tokens = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
                    if p["token"] in tokens:
                        selected.append((m, tokens.index(p["token"])))
                if len(selected) != 1:
                    raise ValueError("held_market_missing_or_ambiguous")
                m, index = selected[0]
                if m.get("closed") and m.get("umaResolutionStatus") == "resolved":
                    prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
                    payoff = float(prices[index])
                    if payoff not in (0.0, 1.0):
                        raise ValueError("ambiguous_resolution")
                    self.close(p, p["shares"]*payoff, "paper_settlement", now)
                    continue
                if m.get("closed") or m.get("acceptingOrders") is not True:
                    self.log(now, "awaiting_resolution", {"slug": p["slug"]})
                    continue
                rate = fee_rate(m)
                book = self.fresh_book(api, p["token"])
                target = exit_tick(self.config["target_exit"], book["tick_size"])
                if target is None or p["shares"] < float(book["min_order_size"]):
                    self.log(now, "exit_unavailable_tick_or_size", {"slug": p["slug"]})
                    continue
                fill = sweep(book, "SELL", p["shares"], target, rate)
                if fill:
                    self.close(p, fill["net"], "paper_target_exit", now)
            except Exception as exc:
                self.log(now, "exit_data_error", {"slug": p["slug"], "error": str(exc)})

    def enter(self, api, row, now):
        cfg = self.config
        if self.db.execute("SELECT 1 FROM positions WHERE slug=?", (row["slug"],)).fetchone():
            return
        previous = self.db.execute("SELECT * FROM confirmations WHERE slug=?", (row["slug"],)).fetchone()
        self.db.execute("INSERT OR REPLACE INTO confirmations VALUES(?,?,?)", (row["slug"], row["token"], now.timestamp()))
        if not previous or previous["token"] != row["token"]:
            return
        elapsed = now.timestamp()-previous["ts"]
        if not cfg["confirmation_min_seconds"] <= elapsed <= cfg["confirmation_max_seconds"]:
            return
        # Scan across many cities can take time: reject stale weather decisions.
        if (now-iso(row["weather"]["local_time"])).total_seconds() > 180:
            self.log(now, "entry_scan_stale", {"slug": row["slug"]})
            return
        # Refetch depth after the second signal. Scanning quote is not the fill.
        book = self.fresh_book(api, row["token"])
        asks = levels(book, "BUY")
        bids = levels(book, "SELL")
        if not asks or not bids:
            return
        ask, bid = asks[0][0], bids[0][0]
        if bid >= ask or ask-bid > cfg["max_spread"] or not cfg["entry_min"] <= ask <= cfg["entry_max"]:
            return
        st = self.status()
        spent = self.db.execute("SELECT coalesce(sum(cost),0) FROM positions WHERE opened>=?", (now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp(),)).fetchone()[0]
        equity = st["equity_at_cost"]
        budget = min(equity*cfg["stake_fraction"], cfg["stake_cap"], st["cash"],
                     equity*cfg["max_open_fraction"]-st["open_cost"], equity*cfg["daily_fraction"]-spent)
        shares = math.floor(budget/(cfg["entry_max"]+row["fee_rate"]*.25))
        if shares < max(1, float(book["min_order_size"])):
            return
        fill = sweep(book, "BUY", shares, cfg["entry_max"], row["fee_rate"])
        if not fill or fill["net"] > budget:
            self.log(now, "insufficient_entry_depth", {"slug": row["slug"]})
            return
        target_net = cfg["target_exit"]-row["fee_rate"]*cfg["target_exit"]*(1-cfg["target_exit"])
        if row["weather_lower95"]*target_net - fill["net"]/shares < cfg["min_proxy_edge"]:
            return
        self.db.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,'open',NULL,NULL,NULL)",
                        (row["slug"], row["city"], row["day"], row["token"], row["label"], shares, fill["net"], now.timestamp()))
        self.db.execute("UPDATE account SET cash=cash-? WHERE id=1", (fill["net"],))
        self.log(now, "paper_buy", {"slug": row["slug"], **fill, "assumption": "full visible depth at refetch; no queue or network-latency guarantee"})

    def step(self, api, rows, now):
        with self.db:
            self.exits(api, now)
            current = {r["slug"] for r in rows if r["status"] == "paper_candidate"}
            for old in self.db.execute("SELECT slug FROM confirmations").fetchall():
                if old[0] not in current:
                    self.db.execute("DELETE FROM confirmations WHERE slug=?", (old[0],))
            for row in rows:
                if row["status"] == "paper_candidate":
                    try:
                        self.enter(api, row, now)
                    except Exception as exc:
                        self.log(now, "entry_data_error", {"slug": row["slug"], "error": str(exc)})
        return self.status()
