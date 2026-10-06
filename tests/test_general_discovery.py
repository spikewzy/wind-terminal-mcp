import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.service import Service
from wind_bridge.storage import Store


class GeneralDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []
        self.entity = {"entity": "测试铜库存", "fullName": "测试铜库存", "id": "M123456",
                       "type": "edbIndex", "candidateEntities": [
                           {"entity": "测试铜库存", "fullName": "测试铜库存", "id": "M123457", "type": "edbIndex"}]}
        self.service = Service(self.store, Backend(self.store, self.fake))

    def fake(self, method, arguments):
        self.calls.append((method, arguments))
        if method == "wai":
            payload = {"status": "0", "body": {"status_code": 200, "succeed": True,
                                                 "data": [[self.entity]]}}
            raw = {"ErrorCode": 0, "Codes": ["fer"], "Fields": ["details"],
                   "Times": ["2026-09-29"], "Data": [[json.dumps(payload)]]}
        elif method == "edb":
            raw = {"ErrorCode": 0, "Codes": [arguments["codes"]], "Fields": ["CLOSE"],
                   "Times": ["2026-09-01", "2026-09-02"], "Data": [[1, None]]}
        else:
            raise AssertionError(method)
        return {"ok": True, "raw": raw}

    def test_fresh_installation_has_no_implicit_agricultural_catalog(self):
        self.assertEqual(self.store.catalog(), [])
        self.service.register([{"code": "M123458", "name": "用户已有指标"}], "test_fixture", "test")
        Service(self.store, self.service.backend)
        self.assertEqual([x["code"] for x in self.store.catalog()], ["M123458"])

    def test_terminal_discovery_keeps_ambiguity_without_fetching_values(self):
        self.entity.update(startIndex=0, endIndex=4)
        result = self.service.discover_economic("测试铜库存", scope="terminal", limit=1)
        self.assertEqual([x[0] for x in self.calls], ["wai"])
        self.assertEqual(result["total_matches"], 2)
        self.assertEqual(result["next_offset"], 1)
        self.assertEqual(result["metrics"][0]["code"], "M123456")
        self.assertIsNone(result["metrics"][0]["unit"])
        self.assertFalse(result["metrics"][0]["selection_verified"])
        self.assertEqual(result["terminal_recognition"]["entities"][0]["raw_entity"], self.entity)
        self.assertFalse(result["full_wind_catalog"])
        self.assertEqual(self.store.catalog(), [])
        coverage = result["edb_text_coverage"]
        self.assertEqual(coverage, result["terminal_recognition"]["edb_text_coverage"])
        self.assertEqual(coverage["paragraphs"][0]["full_text_candidate_codes"], ["M123456", "M123457"])
        self.assertEqual(result["metrics"][0]["candidate"]["occurrences"][0]["matched_text"], "测试铜库存")

    def test_local_search_is_offline_and_does_not_claim_global_absence(self):
        result = self.service.discover_economic("铜库存")
        self.assertEqual(result["metrics"], [])
        self.assertEqual(result["terminal_discovery"]["scope"], "terminal")
        self.assertFalse(result["catalog_is_query_whitelist"])
        self.assertEqual(self.calls, [])

    def test_unsupported_filters_and_invalid_pages_do_not_call_wind(self):
        for kwargs in [{"scope": "unknown"}, {"scope": "terminal", "filters": {"unit": "吨"}},
                       {"scope": "terminal", "limit": 0}, {"scope": "terminal", "offset": -1}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(Problem):
                self.service.discover_economic("铜库存", **kwargs)
        self.assertEqual(self.calls, [])

    def test_explicit_code_query_works_without_catalog_and_keeps_unknown_metadata(self):
        result = self.service.economic("M123456", "2026-09-01", "2026-09-02")
        self.assertEqual([x[0] for x in self.calls], ["edb"])
        self.assertEqual(result["metrics"][0]["value"], [1, None])
        self.assertIsNone(result["metrics"][0]["meta"]["unit"])
        self.assertFalse(result["point_in_time_safe"])

    def test_explicit_seed_import_preserves_provenance(self):
        seed = Path(self.temp.name) / "user-seed.json"
        seed.write_text(json.dumps({"indicators": [{"meta": {"code": "M123458", "name": "用户指标"},
                                                    "provenance": {"metadata_source_id": "test_fixture", "evidence": "test"}}]}), encoding="utf-8")
        Service(self.store, self.service.backend, seed_path=seed)
        self.assertEqual(self.store.indicator("M123458")["metadata_provenance"]["metadata_source_id"], "test_fixture")

    def test_unknown_frequency_reports_a_structured_requirement_without_fetching(self):
        with self.assertRaises(Problem) as raised:
            self.service.economic("M123456", observation="10")
        self.assertEqual(raised.exception.code, "FREQUENCY_REQUIRED")
        self.assertEqual(raised.exception.details["indicator_code"], "M123456")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
