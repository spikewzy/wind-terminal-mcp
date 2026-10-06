"""Offline regressions from the 2025-04-10 manual review; no Wind calls."""
import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.service import Service
from wind_bridge.storage import Store


class ManualContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []
        self.raw = {}
        self.backend = Backend(self.store, self.runner)
        self.service = Service(self.store, self.backend)

    def runner(self, method, args):
        self.calls.append((method, args))
        return {"ok": True, "raw": json.loads(json.dumps(self.raw))}

    def test_wses_multi_field_is_rejected_before_native_request(self):
        for codes in ("a001010100", "a001010100,a001010200"):
            with self.subTest(codes=codes), self.assertRaises(Problem) as caught:
                self.backend.request("wses", {"codes": codes, "fields": "sec_close_avg,sec_pe_avg",
                    "beginTime": "2026-09-21", "endTime": "2026-09-29"})
            self.assertEqual(caught.exception.code, "INVALID_PARAMS")
        self.assertEqual(self.calls, [])

    def test_wses_single_field_multiple_sectors_keep_values_and_options(self):
        self.raw = {"ErrorCode": 0, "Codes": ["a001010100", "a001010200"],
                    "Fields": ["SEC_CLOSE_AVG"], "Times": ["2026-09-28", "2026-09-29"],
                    "Data": [[1, None], [2, 3]]}
        result = self.service.query("wses", {"codes": self.raw["Codes"], "fields": "sec_close_avg",
            "beginTime": "2026-09-28", "endTime": "2026-09-29", "options": "DynamicTime=0;Fill=Blank"})
        self.assertEqual(result["raw"], self.raw)
        self.assertEqual(self.calls[0][1]["options"], "DynamicTime=0;Fill=Blank")

    def test_conflicting_wsee_documentation_does_not_remove_multi_field_route(self):
        self.raw = {"ErrorCode": 0, "Codes": ["a001010100", "a001010200"],
                    "Fields": ["SEC_CLOSE_AVG", "SEC_PE_AVG"], "Times": ["2026-09-29"],
                    "Data": [[1, 2], [3, None]]}
        result = self.service.query("wsee", {"codes": self.raw["Codes"],
            "fields": "sec_close_avg,sec_pe_avg", "options": "tradeDate=20260929"})
        self.assertEqual(result["raw"], self.raw)
        self.assertEqual(len(self.calls), 1)

    def test_invalid_intraday_windows_stop_before_native_request(self):
        windows = [
            ("2026-09-29 15:00:00", "2026-09-29 09:00:00"),
            ("2026-09-29 25:00:00", "2026-09-29 15:00:00"),
            ("2026-09-29 09:61:00", "2026-09-29 15:00:00"),
            ("2026-09-29 09:00:00+08:00", "2026-09-29 15:00:00+08:00"),
            ("2026-09-29junk", "2026-09-29 15:00:00"),
        ]
        for method in ("wsi", "wst"):
            for begin, end in windows:
                with self.subTest(method=method, begin=begin), self.assertRaises(Problem) as caught:
                    self.backend.request(method, {"codes": "AL00.SHF", "fields": "close",
                        "beginTime": begin, "endTime": end})
                self.assertEqual(caught.exception.code, "INVALID_PARAMS")
        self.assertEqual(self.calls, [])

    def test_valid_overnight_intraday_request_is_not_truncated(self):
        self.raw = {"ErrorCode": 0, "Codes": ["AL00.SHF"], "Fields": ["CLOSE"],
                    "Times": ["2026-09-29T21:00:00", "2026-09-30T02:30:00"], "Data": [[1, 2]]}
        for method in ("wsi", "wst"):
            args = {"codes": "AL00.SHF", "fields": "close", "beginTime": "2026-09-29 20:55:00",
                    "endTime": "2026-09-30T02:31:00", "options": ""}
            result = self.service.query(method, args)
            self.assertEqual(self.calls[-1][1], args)
            self.assertEqual(result["raw"], self.raw)

    def test_daily_wsd_single_code_multiple_fields_stays_supported(self):
        self.raw = {"ErrorCode": 0, "Codes": ["AL00.SHF"], "Fields": ["OPEN", "CLOSE"],
                    "Times": ["2026-09-29"], "Data": [[1], [2]]}
        result = self.service.query("wsd", {"codes": "AL00.SHF", "fields": "open,close",
            "beginTime": "2026-09-29", "endTime": "2026-09-29"})
        self.assertEqual(result["raw"], self.raw)
