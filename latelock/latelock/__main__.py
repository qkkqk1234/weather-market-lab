import argparse
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .history import study, tape_opportunities
from .paper import Paper
from .public import Public, UTC
from .scanner import scan

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@contextmanager
def lock(path):
    with path.open("a+b") as f:
        f.seek(0)
        if f.read(1) == b"":
            f.write(b"0"); f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("Another paper process holds this data directory.")
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def main():
    p = argparse.ArgumentParser(description="Late temperature research/scanner/paper ledger. No live execution.")
    p.add_argument("command", choices=["study", "discover", "scan", "paper", "status"])
    p.add_argument("--source", type=Path, default=ROOT.parent/"poly1"/"wx"/"data")
    p.add_argument("--data", type=Path, default=ROOT/"data")
    p.add_argument("--before", default="2026-09-05", help="Exclusive history cutoff")
    p.add_argument("--split", default="2026-06-01", help="Train strictly before date; validate afterwards")
    p.add_argument("--cities", help="Optional comma-separated registered city keys")
    p.add_argument("--cycles", type=int, default=1, help="paper cycles; 0 = continuous until Ctrl+C or HALT file")
    args = p.parse_args()
    registry, cfg = read(ROOT/"cities.json"), read(ROOT/"config.json")
    if cfg.get("mode") != "paper_only":
        raise SystemExit("Only paper_only mode is implemented.")
    args.data.mkdir(parents=True, exist_ok=True)
    if args.command == "study":
        if not args.split < args.before:
            p.error("--split must precede --before")
        profiles = study(registry, args.source, ROOT/"reports", args.before, args.split)
        result = {"profiles": len(profiles), "weather_candidates": [k for k,v in profiles.items() if v["status"] == "weather_paper_candidate"],
                  "trade_opportunities": tape_opportunities(registry, args.source, ROOT/"reports")}
        (ROOT/"reports"/"study_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2)); return
    if args.command == "status":
        if not (args.data/"paper.sqlite").exists():
            print("No paper account yet."); return
        account = Paper(args.data/"paper.sqlite", cfg)
        print(json.dumps(account.status(), ensure_ascii=False, indent=2)); return
    api = Public(args.data)
    if args.command == "discover":
        events = api.discover(datetime.now(UTC))
        print(json.dumps({"events": len(events), "file": str(args.data/"discovery.json")}, ensure_ascii=False)); return
    profiles = read(ROOT/"reports"/"profiles.json")
    chosen = set(args.cities.split(",")) if args.cities else None
    if chosen and not chosen <= registry.keys():
        p.error("Unknown registered city key")
    with lock(args.data/"runner.lock"):
        account = Paper(args.data/"paper.sqlite", cfg) if args.command == "paper" else None
        cycle = 0
        while True:
            if (args.data/"HALT").exists():
                print("HALT: stopped; paper positions are retained."); break
            try:
                rows, summary = scan(api, registry, profiles, cfg, chosen)
                if account:
                    summary["account"] = account.step(api, rows, datetime.now(UTC))
                compact = [{k:v for k,v in row.items() if k != "book"} for row in rows]
                result = {"summary": summary, "rows": compact}
                (args.data/"latest_scan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                with (args.data/"signals.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False)+"\n")
                print(json.dumps({k:v for k,v in summary.items() if k != "coverage"}, ensure_ascii=False), flush=True)
            except Exception as exc:
                print(json.dumps({"cycle_error": str(exc), "time": datetime.now(UTC).isoformat()}), flush=True)
                # Discovery can fail while old positions still need monitoring.
                if account:
                    with account.db:
                        account.exits(api, datetime.now(UTC))
                if args.command == "scan":
                    raise
            cycle += 1
            if args.command == "scan" or (args.cycles and cycle >= args.cycles):
                break
            for _ in range(cfg["scan_interval_seconds"]):
                if (args.data/"HALT").exists():
                    break
                time.sleep(1)


if __name__ == "__main__":
    main()
