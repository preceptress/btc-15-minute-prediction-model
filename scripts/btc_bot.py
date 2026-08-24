#!/usr/bin/env python3
"""One-strategy BTC 15-minute paper/live trading bot."""

import argparse
import base64
import fcntl
import json
import math
import os
import sqlite3
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "btc_15m.sqlite3"
LOCK = ROOT / "data" / "btc_15m.lock"
API_ROOT = "https://external-api.kalshi.com"
API = API_ROOT + "/trade-api/v2"
SERIES = "KXBTC15M"

# Existing Strong Momentum rules.
MIN_PRICE = 0.20
MAX_PRICE = 0.85
MAX_SPREAD = 0.05
MIN_MINUTES_LEFT = 2
MAX_MINUTES_LEFT = 12
MAX_CANDLE_AGE_SECONDS = 120
CONFIRMATION_MINUTES = 3
PROXY_BASIS_SIGMA = 0.00025
PROXY_MIN_EDGE = 0.12
MIN_MOVE = 0.0012
SIDE_AUDIT_TRADES = 10
MAX_SIDE_SHARE = 0.80

# Conservative live limits inherited from the original bot.
MAX_CONTRACTS = 1
MAX_DEBIT = 5.00
MAX_LOSSES = 2
MAX_SESSION_LOSS = 5.00

load_dotenv(ROOT / ".env", override=True)


def utcnow():
    return datetime.now(timezone.utc)


def fee(price, count=1):
    raw = 0.07 * count * price * (1.0 - price)
    return math.ceil((raw - 1e-12) * 100.0) / 100.0 if count else 0.0


def candidate_value(probability, price, count=1):
    paid_fee = fee(price, count)
    debit = price * count + paid_fee
    return {
        "fee": paid_fee,
        "debit": debit,
        "expected_profit": probability * count - debit,
        "edge_probability": probability - price - paid_fee / count,
    }


def minute_volatility(closes, minimum=0.00035):
    values = [float(value) for value in closes if float(value) > 0]
    if len(values) < 10:
        raise ValueError("at least 10 positive minute closes are required")
    returns = [math.log(current / previous)
               for previous, current in zip(values, values[1:])]
    return max(statistics.stdev(returns), minimum)


def settlement_probability(spot, reference, minutes_left, volatility,
                           basis_sigma=0.00075):
    horizon_sigma = math.sqrt(volatility ** 2 * minutes_left + basis_sigma ** 2)
    z_score = math.log(reference / spot) / horizon_sigma
    return min(1.0, max(0.0, 0.5 * math.erfc(z_score / math.sqrt(2.0))))


def connection(path=DB):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    trade_columns = """id TEXT PRIMARY KEY,event_ticker TEXT NOT NULL UNIQUE,
        ticker TEXT NOT NULL,side TEXT NOT NULL,contracts INTEGER NOT NULL,
        entry_price REAL NOT NULL,entry_fee REAL NOT NULL,entry_debit REAL NOT NULL,
        model_probability REAL NOT NULL,model_edge REAL NOT NULL,spot REAL NOT NULL,
        window_open_spot REAL NOT NULL,minute_return REAL NOT NULL,
        minute_volatility REAL NOT NULL,minutes_left REAL NOT NULL,spread REAL NOT NULL,
        opened_at TEXT NOT NULL,status TEXT NOT NULL,closed_at TEXT,result TEXT,pnl REAL"""
    db.execute(f"CREATE TABLE IF NOT EXISTS paper_trades({trade_columns})")
    db.execute(f"""CREATE TABLE IF NOT EXISTS live_trades({trade_columns},
        entry_order_id TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS scan_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,scanned_at TEXT NOT NULL,
        selected_ticker TEXT,selected_side TEXT,decision TEXT NOT NULL,
        diagnostics TEXT NOT NULL)""")
    defaults = {
        "enabled": "false", "paper_bankroll": "1000.00",
        "starting_bankroll": "1000.00", "losses": "0",
        "session_pnl": "0", "account_audit_after": "",
    }
    for key, value in defaults.items():
        db.execute("INSERT OR IGNORE INTO state VALUES(?,?)", (key, value))
    db.commit()
    return db


def get_state(db, key):
    return db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()[0]


def set_state(db, key, value):
    db.execute("UPDATE state SET value=? WHERE key=?", (str(value), key))


def public_get(path, params=None):
    response = requests.get(API + path, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def minute_closes():
    response = requests.get(
        "https://api.exchange.coinbase.com/products/BTC-USD/candles",
        params={"granularity": 60}, timeout=20)
    response.raise_for_status()
    return sorted(response.json(), key=lambda row: row[0])[-61:]


def reference_price(market):
    for field in ("reference_price_dollars", "reference_price",
                  "opening_reference_price_dollars", "opening_reference_price"):
        try:
            value = float(market.get(field) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value, field
    return None, "coinbase_proxy"


def probability_estimate(rows, open_time, close_time, now=None, reference=None):
    now = now or utcnow()
    open_ts = open_time.timestamp()
    eligible = [row for row in rows if float(row[0]) <= max(open_ts, now.timestamp())]
    if not eligible:
        return None
    opening = min(eligible, key=lambda row: abs(float(row[0]) - open_ts))
    recent = sorted(eligible, key=lambda row: float(row[0]))
    proxy_open = float(opening[3])
    open_spot = float(reference or proxy_open)
    spot = float(recent[-1][4])
    volatility = minute_volatility([row[4] for row in rows])
    minutes_left = max((close_time - now).total_seconds() / 60, 1e-6)
    displacement = math.log(spot / open_spot)
    basis_sigma = 0.0 if reference else PROXY_BASIS_SIGMA
    probability = settlement_probability(
        spot, open_spot, minutes_left, volatility, basis_sigma)
    start = recent[max(0, len(recent) - CONFIRMATION_MINUTES - 1)]
    confirmation = math.log(spot / float(start[4]))
    candle_age = max(0.0, now.timestamp() - float(recent[-1][0]))
    return {
        "probability": min(max(probability, 0.001), 0.999), "spot": spot,
        "open_spot": open_spot, "minute_return": displacement,
        "confirmation_return": confirmation, "candle_age_seconds": candle_age,
        "volatility": volatility, "minutes_left": minutes_left,
        "reference_source": "kalshi" if reference else "coinbase_proxy",
    }


def entry_eligible(side, price, edge, estimate):
    direction = 1 if side == "yes" else -1
    confirmed = (direction * estimate["minute_return"] > 0
                 and direction * estimate["confirmation_return"] > 0)
    proxy_edge_ok = (estimate["reference_source"] != "coinbase_proxy"
                     or edge >= PROXY_MIN_EDGE)
    return (MIN_PRICE <= price <= MAX_PRICE
            and estimate["candle_age_seconds"] <= MAX_CANDLE_AGE_SECONDS
            and proxy_edge_ok and confirmed
            and abs(estimate["minute_return"]) >= MIN_MOVE
            and edge >= 0.03)


def discover_candidate(now=None, diagnostics=None):
    now = now or utcnow()
    payload = public_get("/markets", {
        "series_ticker": SERIES, "min_close_ts": int(now.timestamp()),
        "max_close_ts": int(now.timestamp() + MAX_MINUTES_LEFT * 60 + 60),
        "limit": 100,
    })
    rows = minute_closes()
    choices = []
    for market in payload.get("markets", []):
        if str(market.get("status") or "").lower() != "active":
            continue
        opened = datetime.fromisoformat(market["open_time"].replace("Z", "+00:00"))
        closes = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
        reference, source = reference_price(market)
        estimate = probability_estimate(rows, opened, closes, now, reference)
        if not estimate or not MIN_MINUTES_LEFT <= estimate["minutes_left"] <= MAX_MINUTES_LEFT:
            continue
        estimate["reference_source"] = source
        for side in ("yes", "no"):
            price = float(market.get(f"{side}_ask_dollars") or 0)
            bid = float(market.get(f"{side}_bid_dollars") or 0)
            size = float(market.get(f"{side}_ask_size_fp") or 0)
            probability = estimate["probability"] if side == "yes" else 1 - estimate["probability"]
            value = candidate_value(probability, price)
            spread = price - bid
            liquid = size >= 1 and bid > 0 and 0 <= spread <= MAX_SPREAD
            eligible = entry_eligible(side, price, value["edge_probability"], estimate)
            if diagnostics is not None:
                diagnostics.append({
                    "ticker": market.get("ticker"), "side": side, "ask": price,
                    "bid": bid, "spread": round(spread, 6),
                    "probability": round(probability, 6),
                    "edge": round(value["edge_probability"], 6),
                    "move": round(estimate["minute_return"], 8),
                    "confirmation": round(estimate["confirmation_return"], 8),
                    "eligible": liquid and eligible,
                })
            if liquid and eligible:
                choices.append((value["expected_profit"], market, side, price,
                                probability, spread, value, estimate))
    return max(choices, default=None, key=lambda row: row[0])


def side_allowed(db, table, side):
    rows = db.execute(
        f"SELECT side FROM {table} GROUP BY event_ticker "
        "ORDER BY MAX(opened_at) DESC LIMIT ?", (SIDE_AUDIT_TRADES,)).fetchall()
    if len(rows) < SIDE_AUDIT_TRADES:
        return True
    share = sum(row[0] == side for row in rows) / len(rows)
    return share < MAX_SIDE_SHARE


def record_scan(db, candidate, diagnostics, decision):
    db.execute("INSERT INTO scan_log(scanned_at,selected_ticker,selected_side,decision,diagnostics) VALUES(?,?,?,?,?)",
               (utcnow().isoformat(), candidate[1].get("ticker") if candidate else None,
                candidate[2] if candidate else None, decision,
                json.dumps(diagnostics, separators=(",", ":"))))


def settle_table(db, table):
    messages = []
    for trade in db.execute(f"SELECT * FROM {table} WHERE status='OPEN'").fetchall():
        market = public_get(f"/markets/{trade['ticker']}").get("market", {})
        result = str(market.get("result") or "").lower()
        if result not in ("yes", "no"):
            continue
        pnl = (trade["contracts"] if result == trade["side"] else 0) - trade["entry_debit"]
        db.execute(f"UPDATE {table} SET status='CLOSED',closed_at=?,result=?,pnl=? WHERE id=?",
                   (utcnow().isoformat(), result, pnl, trade["id"]))
        if table == "paper_trades":
            bankroll = float(get_state(db, "paper_bankroll"))
            payout = trade["contracts"] if result == trade["side"] else 0
            set_state(db, "paper_bankroll", round(bankroll + payout, 2))
        else:
            total = float(get_state(db, "session_pnl")) + pnl
            losses = int(get_state(db, "losses")) + int(pnl < 0)
            set_state(db, "session_pnl", total)
            set_state(db, "losses", losses)
            if losses >= MAX_LOSSES or total <= -MAX_SESSION_LOSS:
                set_state(db, "enabled", "false")
        messages.append(f"settled {result}; pnl=${pnl:.2f}")
    db.commit()
    return messages


def paper_cycle(db):
    messages = settle_table(db, "paper_trades")
    if db.execute("SELECT 1 FROM paper_trades WHERE status='OPEN'").fetchone():
        return "; ".join(messages + ["position still open"])
    diagnostics = []
    candidate = discover_candidate(diagnostics=diagnostics)
    if not candidate:
        record_scan(db, None, diagnostics, "NO_OPPORTUNITY")
        db.commit()
        return "; ".join(messages + ["no qualifying opportunity"])
    _, market, side, price, probability, spread, value, estimate = candidate
    event = market.get("event_ticker") or market["ticker"]
    if db.execute("SELECT 1 FROM paper_trades WHERE event_ticker=?", (event,)).fetchone():
        record_scan(db, candidate, diagnostics, "DUPLICATE_EVENT")
        db.commit()
        return "; ".join(messages + ["window already evaluated"])
    if not side_allowed(db, "paper_trades", side):
        record_scan(db, candidate, diagnostics, "SIDE_ALARM")
        db.commit()
        return "; ".join(messages + [f"{side} blocked by side-balance alarm"])
    bankroll = float(get_state(db, "paper_bankroll"))
    if value["debit"] > bankroll:
        return "; ".join(messages + ["insufficient paper cash"])
    values = (str(uuid.uuid4()), event, market["ticker"], side, 1, price,
              value["fee"], value["debit"], probability,
              value["edge_probability"], estimate["spot"], estimate["open_spot"],
              estimate["minute_return"], estimate["volatility"],
              estimate["minutes_left"], spread, utcnow().isoformat(), "OPEN",
              None, None, None)
    db.execute("INSERT INTO paper_trades VALUES(" + ",".join("?" * 21) + ")", values)
    set_state(db, "paper_bankroll", round(bankroll - value["debit"], 2))
    record_scan(db, candidate, diagnostics, "ENTERED")
    db.commit()
    return "; ".join(messages + [
        f"paper entry {side} at {price:.2f}; P={probability:.1%}; "
        f"edge={value['edge_probability']:.1%}; move={estimate['minute_return']:.3%}"])


class Kalshi:
    def __init__(self):
        self.key_id = os.environ.get("KALSHI_API_KEY_ID")
        key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        if not self.key_id or not key_path:
            raise RuntimeError("Kalshi credentials are missing")
        with open(key_path, "rb") as handle:
            self.private_key = serialization.load_pem_private_key(handle.read(), None)

    def headers(self, method, path):
        stamp = str(int(time.time() * 1000))
        message = f"{stamp}{method.upper()}{path}".encode()
        signature = self.private_key.sign(
            message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                 salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id,
                "KALSHI-ACCESS-TIMESTAMP": stamp,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
                "Content-Type": "application/json"}

    def private_get(self, path, params=None):
        response = requests.get(API_ROOT + path, params=params,
                                headers=self.headers("GET", path), timeout=20)
        response.raise_for_status()
        return response.json()

    def order(self, ticker, side, price, count):
        path = "/trade-api/v2/portfolio/events/orders"
        action = "bid" if side == "yes" else "ask"
        yes_price = price if side == "yes" else 1 - price
        body = {"ticker": ticker, "client_order_id": str(uuid.uuid4()),
                "side": action, "count": f"{count:.2f}", "price": f"{yes_price:.4f}",
                "time_in_force": "fill_or_kill", "post_only": False,
                "cancel_order_on_pause": True,
                "self_trade_prevention_type": "taker_at_cross"}
        response = requests.post(API_ROOT + path, headers=self.headers("POST", path),
                                 json=body, timeout=20)
        response.raise_for_status()
        return response.json().get("order", response.json())


def actual_fill(order, outcome_side):
    count = int(float(order.get("fill_count_fp") or order.get("fill_count") or 0))
    if count <= 0:
        return 0, 0.0, 0.0
    cost = float(order.get("taker_fill_cost_dollars") or 0) + float(order.get("maker_fill_cost_dollars") or 0)
    paid_fee = float(order.get("taker_fees_dollars") or 0) + float(order.get("maker_fees_dollars") or 0)
    yes_price = cost / count
    contract_price = yes_price if outcome_side == "yes" else 1 - yes_price
    return count, contract_price, paid_fee


def account_clear(client):
    positions = client.private_get("/trade-api/v2/portfolio/positions", {"limit": 1000})
    btc = [row for row in positions.get("market_positions", [])
           if str(row.get("ticker", "")).startswith(SERIES)
           and float(row.get("position_fp") or row.get("position") or 0)]
    orders = client.private_get("/trade-api/v2/portfolio/orders", {"status": "resting", "limit": 1000})
    resting = [row for row in orders.get("orders", [])
               if str(row.get("ticker", "")).startswith(SERIES)]
    return not btc and not resting


def untracked_orders(db, client):
    audit_after = get_state(db, "account_audit_after")
    if not audit_after:
        return []
    tracked = {row[0] for row in db.execute(
        "SELECT entry_order_id FROM live_trades") if row[0]}
    payload = client.private_get("/trade-api/v2/portfolio/orders", {"limit": 100})
    return [order for order in payload.get("orders", [])
            if str(order.get("ticker", "")).startswith(SERIES)
            and order.get("status") == "executed"
            and str(order.get("created_time") or "") > audit_after
            and order.get("order_id") not in tracked]


def live_cycle(db):
    messages = settle_table(db, "live_trades")
    if get_state(db, "enabled") != "true":
        return "; ".join(messages + ["live disabled"])
    if db.execute("SELECT 1 FROM live_trades WHERE status='OPEN'").fetchone():
        return "; ".join(messages + ["live position still open"])
    if int(get_state(db, "losses")) >= MAX_LOSSES or float(get_state(db, "session_pnl")) <= -MAX_SESSION_LOSS:
        set_state(db, "enabled", "false")
        db.commit()
        return "stop-loss reached; live disabled"
    client = Kalshi()
    unknown = untracked_orders(db, client)
    if unknown:
        set_state(db, "enabled", "false")
        db.commit()
        return "untracked account order detected; live disabled"
    if not account_clear(client):
        return "no entry: existing 15-minute BTC position or resting order"
    candidate = discover_candidate()
    if not candidate:
        return "no qualifying opportunity"
    _, market, side, price, probability, spread, value, estimate = candidate
    event = market.get("event_ticker") or market["ticker"]
    if db.execute("SELECT 1 FROM live_trades WHERE event_ticker=?", (event,)).fetchone():
        return "window already evaluated"
    if not side_allowed(db, "live_trades", side):
        return f"{side} blocked by side-balance alarm"
    balance = float(client.private_get("/trade-api/v2/portfolio/balance").get("balance_dollars") or 0)
    if value["debit"] > min(MAX_DEBIT, balance):
        return "insufficient balance within live cap"
    order = client.order(market["ticker"], side, price, MAX_CONTRACTS)
    filled, fill_price, paid_fee = actual_fill(order, side)
    if filled <= 0:
        return "entry fill-or-kill order did not fill"
    debit = fill_price * filled + paid_fee
    values = (str(uuid.uuid4()), event, market["ticker"], side, filled, fill_price,
              paid_fee, debit, probability, value["edge_probability"],
              estimate["spot"], estimate["open_spot"], estimate["minute_return"],
              estimate["volatility"], estimate["minutes_left"], spread,
              utcnow().isoformat(), "OPEN", None, None, None, order["order_id"])
    db.execute("INSERT INTO live_trades VALUES(" + ",".join("?" * 22) + ")", values)
    db.commit()
    return f"LIVE entry {market['ticker']} {side} x{filled} at {fill_price:.2f}"


def run_cycle():
    db = connection()
    try:
        if get_state(db, "enabled") == "true":
            print(live_cycle(db))
        else:
            print("PAPER MODE: " + paper_cycle(db))
    finally:
        db.close()


def status(db):
    state = dict(db.execute("SELECT key,value FROM state"))
    print(json.dumps({
        "mode": "live" if state["enabled"] == "true" else "paper",
        "strategy": "Strong Momentum", "series": SERIES,
        "paper_bankroll": float(state["paper_bankroll"]),
        "live_session_pnl": float(state["session_pnl"]),
        "live_session_losses": int(state["losses"]),
        "paper_open": db.execute("SELECT COUNT(*) FROM paper_trades WHERE status='OPEN'").fetchone()[0],
        "live_open": db.execute("SELECT COUNT(*) FROM live_trades WHERE status='OPEN'").fetchone()[0],
        "limits": {"max_contracts": MAX_CONTRACTS, "max_debit": MAX_DEBIT,
                   "max_losses": MAX_LOSSES, "max_session_loss": MAX_SESSION_LOSS},
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("cycle", "status", "preflight", "enable", "disable", "reset-session"))
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.command == "cycle":
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCK, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run_cycle()
        return
    db = connection()
    try:
        if args.command == "status":
            status(db)
        elif args.command == "disable":
            set_state(db, "enabled", "false")
            db.commit()
            print("Paper mode enabled; live trading disabled.")
        elif args.command == "enable":
            if args.confirm != "CONFIRM LIVE 15M TRADING":
                raise SystemExit("Exact confirmation required; see LIVE_TRADING.md")
            Kalshi()
            set_state(db, "enabled", "true")
            set_state(db, "account_audit_after", utcnow().isoformat())
            db.commit()
            print("LIVE MODE ENABLED")
        elif args.command == "reset-session":
            if args.confirm != "RESET LIVE SESSION":
                raise SystemExit("Exact confirmation required")
            if db.execute("SELECT 1 FROM live_trades WHERE status='OPEN'").fetchone():
                raise SystemExit("Cannot reset while a live position is open")
            set_state(db, "enabled", "false")
            set_state(db, "losses", "0")
            set_state(db, "session_pnl", "0")
            db.commit()
            print("Live session reset; paper mode active.")
        elif args.command == "preflight":
            client = Kalshi()
            candidate = discover_candidate()
            print(json.dumps({
                "balance": client.private_get("/trade-api/v2/portfolio/balance"),
                "account_clear": account_clear(client),
                "candidate": None if not candidate else {
                    "ticker": candidate[1]["ticker"], "side": candidate[2],
                    "price": candidate[3], "probability": candidate[4],
                    "edge": candidate[6]["edge_probability"]},
            }, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
