# Codex instructions

This repository contains one strategy: BTC 15-minute Strong Momentum.

- Paper mode is the default. Never enable live mode without an explicit request.
- Never commit `.env`, credentials, private keys, SQLite ledgers, or logs.
- Do not change entry filters or live risk limits without explaining the effect and adding tests.
- Run `./venv/bin/python -m unittest discover -s tests` after code changes.
- Distinguish simulated paper fills from actual exchange fills.
