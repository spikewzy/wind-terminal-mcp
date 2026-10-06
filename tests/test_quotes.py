from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.compat import execute
from wind_bridge.service import Service
from wind_bridge.storage import Store


class QuoteDateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []
        self.trade_date = 20260925.0
        self.times = ["2026-09-25T14:59:00", "2026-09-25T15:00:00"]
        self.service = Service(self.store, Backend(self.store, self.fake))

    def fake(self, method, arguments):
        self.calls.append((method, arguments))
        raw = {"ErrorCode": 0, "Codes": ["600519.SH"]}
        if method == "wsq":
            raw.update(Fields=["RT_DATE"], Times=["2026-09-27T12:00:00"], Data=[[self.trade_date]])
        else:
            raw.update(Fields=[f.upper() for f in arguments["fields"].split(",")], Times=self.times,
                       Data=[[1.0] * len(self.times) for _ in range(6)])
        return {"ok": True, "raw": raw}

    def query(self, **params):
        return execute(self.service, "stock_data", "get_stock_quote", {"windcode": "600519.SH", **params})

    def test_default_uses_reported_date_and_count_stays_in_that_session(self):
        result = self.query(count=-1)
        self.assertEqual([c[0] for c in self.calls], ["wsq", "wsi"])
        self.assertEqual(self.calls[1][1]["beginTime"], "2026-09-25 00:00:00")
        self.assertEqual(self.calls[1][1]["endTime"], "2026-09-25 23:59:59")
        self.assertEqual(result["raw"]["Times"], ["2026-09-25T15:00:00"])
        snapshot = self.store.read(result["date_selection"]["snapshot_receipt_id"])
        self.assertEqual(snapshot["response"]["raw"]["Data"], [[20260925.0]])
        self.assertEqual(len(self.store.read(result["receipt_id"])["response"]["raw"]["Times"]), 2)

    def test_begin_only_extends_to_reported_latest_date(self):
        result = self.query(begin="2026-09-01")
        self.assertEqual(self.calls[1][1]["beginTime"], "2026-09-01 00:00:00")
        self.assertEqual(result["date_selection"]["effective_end"], "2026-09-25")

    def test_explicit_dates_do_not_query_snapshot_and_allow_empty_nontrading_day(self):
        self.times = []
        result = self.query(begin="2026-09-27", end="2026-09-27")
        self.assertEqual([c[0] for c in self.calls], ["wsi"])
        self.assertEqual(result["raw"]["Times"], [])
        self.assertEqual(result["date_selection"]["mode"], "explicit_dates")

    def test_end_only_and_missing_snapshot_date_do_not_guess_today(self):
        with self.assertRaises(Problem) as raised:
            self.query(end="2026-09-25")
        self.assertEqual(raised.exception.code, "INVALID_PARAMS")
        self.assertEqual(self.calls, [])
        self.trade_date = None
        with self.assertRaises(Problem) as raised:
            self.query()
        self.assertEqual(raised.exception.code, "LATEST_TRADING_DATE_UNAVAILABLE")
        self.assertEqual([c[0] for c in self.calls], ["wsq"])


if __name__ == "__main__":
    unittest.main()
