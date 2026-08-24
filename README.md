# BTC 15-Minute Strong Momentum

A deliberately small command-line bot for one Kalshi prediction-market strategy:
**BTC 15-minute Strong Momentum**. It can paper trade or, after an explicit
safety procedure, place live trades. Paper mode is always the default.

> Research software, not financial advice. Paper fills are simulations. The
> original experiment had only ten settled Strong Momentum trades, so its early
> 90% result is not enough to establish a durable edge.

## What the model does

Once per minute the bot reads public Coinbase one-minute candles and active
Kalshi `KXBTC15M` markets. It considers a contract only when:

- 2–12 minutes remain;
- its ask is 20–85 cents with a spread no wider than 5 cents;
- Bitcoin has moved at least 0.12% from the window reference;
- the window move and recent three-minute move agree with the trade direction;
- the latest candle is no older than 120 seconds;
- estimated edge is at least 3%, or 12% when Coinbase must proxy the settlement reference;
- the last ten signals are not already 80% or more on that side.

It chooses the qualifying side with the greatest estimated profit, takes at
most one contract, and holds it through settlement. Paper and live modes use
the same signal function and separate ledger tables.

## Ten-minute setup

### Minute 0–2: prepare the computer

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

### Minute 2–3: clone

```bash
git clone https://github.com/preceptress/btc-15-minute-prediction-model.git
cd btc-15-minute-prediction-model
```

### Minute 3–6: install safely

```bash
chmod +x setup.sh scripts/install_timer.sh
./setup.sh
```

This creates `venv/`, installs three Python packages, creates a blank local
`.env`, runs tests, initializes a fresh SQLite ledger, and confirms paper mode.
It does not submit an order or install the timer automatically.

### Minute 6–7: run the first paper scan

```bash
./venv/bin/python scripts/btc_bot.py cycle
```

Zero trades is a valid result when no market passes every filter.

### Minute 7–8: see status and results

```bash
./venv/bin/python scripts/btc_bot.py status
./venv/bin/python scripts/report.py
./venv/bin/python scripts/report.py --json
```

### Minute 8–10: automate the scan (Linux with systemd)

```bash
./scripts/install_timer.sh
systemctl --user status btc-15m.timer
journalctl --user -u btc-15m.service -n 30
```

The timer runs one cycle per minute. Stop it with:

```bash
systemctl --user disable --now btc-15m.timer
```

## Paper or live

There are exactly two modes:

- **Paper:** default, no credentials, simulated fills, `$1,000` fresh bankroll.
- **Live:** real orders and real loss risk; requires Kalshi credentials and the
  separate checklist in [LIVE_TRADING.md](LIVE_TRADING.md).

Adding keys to `.env` does not enable live mode. The database gate also requires
the exact activation phrase.

## Work on it with Codex

The official OpenAI Codex CLI installer for macOS/Linux is:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Then open this local repository and start Codex:

```bash
cd btc-15-minute-prediction-model
codex
```

On first launch, choose **Sign in with ChatGPT** or another available sign-in
method. Codex operates on files in the directory where you launch it. See the
[official Codex CLI guide](https://learn.chatgpt.com/docs/codex/cli).

Try plain-English requests such as:

```text
Explain the Strong Momentum model without changing files.
```

```text
Run the tests and tell me whether paper mode is active.
```

```text
Show the last 24-hour and 7-day paper results. Do not estimate missing trades.
```

```text
Inspect recent scan rejection reasons. Do not alter the filters.
```

```text
Review my proposed change, add tests, but do not enable live mode.
```

Never paste `.env`, a private key, API credentials, or private ledger data into
a conversation.

## Repository map

```text
scripts/btc_bot.py       model, paper ledger, and gated live execution
scripts/report.py        read-only 24h, 7d, and all-time report
scripts/install_timer.sh optional user-level systemd installation
tests/                    focused offline tests
LIVE_TRADING.md           mandatory live-mode procedure
AGENTS.md                 safety instructions for Codex
```
