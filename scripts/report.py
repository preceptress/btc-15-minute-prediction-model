#!/usr/bin/env python3
"""Report Strong Momentum paper or live results without changing the ledger."""

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "btc_15m.sqlite3"
PERIODS = (("last_24_hours", "LAST 24 HOURS", timedelta(hours=24)),
           ("last_7_days", "LAST 7 DAYS", timedelta(days=7)),
           ("all_time", "ALL TIME", None))


def build_report(path=DEFAULT_DB, live=False, now=None):
    if not path.exists():
        raise SystemExit("No ledger yet. Run: ./venv/bin/python scripts/btc_bot.py cycle")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    table = "live_trades" if live else "paper_trades"
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    state = dict(db.execute("SELECT key,value FROM state"))
    output = {"strategy": "Strong Momentum", "ledger": "live" if live else "paper",
              "generated_at": now.isoformat(), "timestamp_basis": "closed_at"}
    for key, _label, duration in PERIODS:
        clauses = ["status='CLOSED'", "closed_at IS NOT NULL"]
        params = []
        if duration:
            clauses.extend(["closed_at>=?", "closed_at<=?"])
            params.extend([(now - duration).isoformat(), now.isoformat()])
        row = db.execute(f"""SELECT COUNT(*) settled,
            COALESCE(SUM(pnl>0),0) wins,COALESCE(SUM(pnl<0),0) losses,
            ROUND(COALESCE(SUM(pnl),0),2) pnl,MAX(closed_at) latest
            FROM {table} WHERE {' AND '.join(clauses)}""", params).fetchone()
        wins, losses = int(row["wins"]), int(row["losses"])
        decided = wins + losses
        output[key] = {
            "settled_trades": int(row["settled"]), "wins": wins, "losses": losses,
            "win_rate": round(100 * wins / decided, 1) if decided else None,
            "pnl": float(row["pnl"]),
            "open_positions": db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status='OPEN'").fetchone()[0],
            "latest_settlement": row["latest"],
        }
        if not live:
            output[key]["cash"] = round(float(state["paper_bankroll"]), 2)
    db.close()
    return output


def print_report(report):
    print("BTC 15-MINUTE STRONG MOMENTUM REPORT")
    print(f"Ledger: {report['ledger']} | Performance timestamp: closed_at (UTC)")
    for key, label, _duration in PERIODS:
        row = report[key]
        rate = "-" if row["win_rate"] is None else f"{row['win_rate']:.1f}%"
        print(f"\n{label}")
        print(f"Settled: {row['settled_trades']} | W-L: {row['wins']}-{row['losses']} | "
              f"Win rate: {rate} | P/L: ${row['pnl']:.2f} | "
              f"Open: {row['open_positions']}")
        if "cash" in row:
            print(f"Current paper cash: ${row['cash']:.2f}")
        print(f"Latest settlement: {row['latest_settlement'] or '-'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report(args.db, live=args.live)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
