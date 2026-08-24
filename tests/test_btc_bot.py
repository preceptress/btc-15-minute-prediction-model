import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "btc_bot.py"
SPEC = importlib.util.spec_from_file_location("btc_bot", MODULE)
bot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bot)


def estimate(**changes):
    values = {
        "minute_return": 0.0013, "confirmation_return": 0.0002,
        "candle_age_seconds": 30, "reference_source": "kalshi",
        "volatility": 0.0004, "spot": 65000, "open_spot": 64915,
        "minutes_left": 8,
    }
    values.update(changes)
    return values


class BtcBotTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "ledger.sqlite3"
        self.db = bot.connection(self.path)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_new_ledger_is_paper_only_with_1000_cash(self):
        self.assertEqual(bot.get_state(self.db, "enabled"), "false")
        self.assertEqual(bot.get_state(self.db, "paper_bankroll"), "1000.00")

    def test_strong_momentum_rules(self):
        self.assertTrue(bot.entry_eligible("yes", 0.55, 0.08, estimate()))
        self.assertFalse(bot.entry_eligible(
            "yes", 0.55, 0.08, estimate(minute_return=0.0011)))
        self.assertFalse(bot.entry_eligible(
            "yes", 0.55, 0.08, estimate(confirmation_return=-0.0002)))
        self.assertFalse(bot.entry_eligible(
            "yes", 0.55, 0.08, estimate(candle_age_seconds=121)))
        self.assertFalse(bot.entry_eligible(
            "yes", 0.55, 0.08, estimate(reference_source="coinbase_proxy")))
        self.assertTrue(bot.entry_eligible(
            "yes", 0.55, 0.12, estimate(reference_source="coinbase_proxy")))

    def test_side_alarm_blocks_eighth_same_side_in_last_ten(self):
        for index, side in enumerate(["yes"] * 8 + ["no"] * 2):
            self.db.execute("""INSERT INTO paper_trades VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                str(index), f"event-{index}", f"ticker-{index}", side, 1,
                .5, .02, .52, .7, .18, 65000, 64900, .0013, .0004, 8, .01,
                f"2026-08-23T00:{index:02}:00+00:00", "CLOSED",
                f"2026-08-23T00:{index:02}:30+00:00", side, .48))
        self.db.commit()
        self.assertFalse(bot.side_allowed(self.db, "paper_trades", "yes"))
        self.assertTrue(bot.side_allowed(self.db, "paper_trades", "no"))

    def test_paper_cycle_records_one_contract_and_debits_cash(self):
        market = {"ticker": "KXBTC15M-TEST", "event_ticker": "EVENT"}
        value = bot.candidate_value(.75, .55)
        candidate = (value["expected_profit"], market, "yes", .55,
                     .75, .01, value, estimate())
        with patch.object(bot, "settle_table", return_value=[]), \
                patch.object(bot, "discover_candidate", return_value=candidate):
            message = bot.paper_cycle(self.db)
        row = self.db.execute("SELECT * FROM paper_trades").fetchone()
        self.assertEqual(row["contracts"], 1)
        self.assertEqual(row["status"], "OPEN")
        self.assertIn("paper entry", message)
        self.assertEqual(float(bot.get_state(self.db, "paper_bankroll")),
                         round(1000 - value["debit"], 2))

    def test_live_enable_requires_exact_confirmation(self):
        with self.assertRaises(SystemExit):
            with patch("sys.argv", ["btc_bot.py", "enable"]), \
                    patch.object(bot, "connection", return_value=self.db):
                bot.main()

    def test_fee_matches_existing_formula(self):
        self.assertEqual(bot.fee(.70, 1), .02)

    def test_no_fill_converts_yes_book_price(self):
        order = {"fill_count_fp": "1.00", "taker_fill_cost_dollars": "0.30",
                 "taker_fees_dollars": "0.02"}
        self.assertEqual(bot.actual_fill(order, "no"), (1, .70, .02))


if __name__ == "__main__":
    unittest.main()
