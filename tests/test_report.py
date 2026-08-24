import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


BOT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "btc_bot.py"
BOT_SPEC = importlib.util.spec_from_file_location("btc_bot_for_report", BOT_PATH)
bot = importlib.util.module_from_spec(BOT_SPEC)
BOT_SPEC.loader.exec_module(bot)
REPORT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "report.py"
REPORT_SPEC = importlib.util.spec_from_file_location("btc_report", REPORT_PATH)
report = importlib.util.module_from_spec(REPORT_SPEC)
REPORT_SPEC.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def test_closed_at_controls_period_and_open_is_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            db = bot.connection(path)
            rows = [
                ("1", "e1", "t1", "yes", 1, .5, .02, .52, .7, .18,
                 1, 1, .0013, .0004, 8, .01, "2026-08-20T00:00:00+00:00",
                 "CLOSED", "2026-08-24T00:00:00+00:00", "yes", .48),
                ("2", "e2", "t2", "no", 1, .5, .02, .52, .7, .18,
                 1, 1, -.0013, .0004, 8, .01, "2026-08-23T00:00:00+00:00",
                 "OPEN", None, None, None),
            ]
            db.executemany("INSERT INTO paper_trades VALUES(" + ",".join("?" * 21) + ")", rows)
            db.commit()
            db.close()
            result = report.build_report(
                path, now=datetime(2026, 8, 24, 12, tzinfo=timezone.utc))
            self.assertEqual(result["last_24_hours"]["settled_trades"], 1)
            self.assertEqual(result["last_24_hours"]["wins"], 1)
            self.assertEqual(result["last_24_hours"]["open_positions"], 1)
            self.assertEqual(result["last_7_days"]["win_rate"], 100.0)


if __name__ == "__main__":
    unittest.main()
