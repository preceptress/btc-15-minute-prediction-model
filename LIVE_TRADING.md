# Live trading checklist

Live mode can lose real money. Start with paper trading and collect a meaningful
sample. This software does not establish that the strategy is profitable.

## Safeguards

- Live is disabled in every fresh ledger.
- One contract maximum and `$5` maximum debit.
- Live disables after two session losses or `$5` session loss.
- Existing 15-minute BTC positions/resting orders block entry.
- An untracked executed 15-minute BTC order disables the bot.
- Positions are held to settlement.
- Enabling requires credentials plus an exact confirmation phrase.

## 1. Stop automation

```bash
systemctl --user stop btc-15m.timer
./venv/bin/python scripts/btc_bot.py disable
```

## 2. Store credentials safely

Create a directory outside this repository, put the matching RSA private key
there, and restrict it to your account:

```bash
mkdir -p "$HOME/.config/kalshi-btc-15m"
chmod 700 "$HOME/.config/kalshi-btc-15m"
chmod 600 "$HOME/.config/kalshi-btc-15m/kalshi-private-key.pem"
chmod 600 .env
```

Set only the key ID and absolute key path in `.env`:

```dotenv
KALSHI_API_KEY_ID=YOUR_KEY_ID
KALSHI_PRIVATE_KEY_PATH=/home/YOUR_USER/.config/kalshi-btc-15m/kalshi-private-key.pem
```

Never store private-key contents in `.env`; never commit or paste either secret.

## 3. Preflight

```bash
./venv/bin/python scripts/btc_bot.py status
./venv/bin/python scripts/btc_bot.py preflight
```

Confirm the intended balance, `account_clear: true`, and any candidate’s ticker,
side, price, probability, and edge. Verify the account directly as well.

## 4. Explicitly enable

```bash
./venv/bin/python scripts/btc_bot.py enable \
  --confirm 'CONFIRM LIVE 15M TRADING'
./venv/bin/python scripts/btc_bot.py status
systemctl --user start btc-15m.timer
```

Monitor the first cycles closely.

## Emergency stop

```bash
systemctl --user stop btc-15m.timer
./venv/bin/python scripts/btc_bot.py disable
```

Disabling prevents future cycles; it does not guarantee that an exchange-side
position or order has disappeared. Inspect and manage the Kalshi account.
