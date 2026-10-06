import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from build_community_fields import build, extract_module
from wind_bridge.common import Problem
from wind_bridge.field_candidates import FieldCandidates


class CandidateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "references").mkdir()
        self.records = [
            {"field": "ftdate", "labels": ["开始交易日"], "references": [
                {"file": "windget/wss.py", "line": 881, "function": "getFtDate", "method": "wss", "literal_field": "ftdate"},
                {"file": "windget/wsd.py", "line": 881, "function": "getFtDateSeries", "method": "wsd", "literal_field": "ftdate"}]},
            {"field": "ftdate_new", "labels": ["开始交易日(支持历史)"], "references": [
                {"file": "windget/wss.py", "line": 885, "function": "getFtDateNew", "method": "wss", "literal_field": "ftdate_new"}]}]
        self.save()
        self.catalog = FieldCandidates(self.root)

    def save(self):
        payload = json.dumps({"schema_version": 1, "metadata_source_id": "fixture_community", "records": self.records}).encode()
        (self.root / "references/community-fields.json").write_bytes(payload)
        (self.root / "references/community-fields-manifest.json").write_text(json.dumps({
            "metadata_source_id": "fixture_community", "catalog_sha256": hashlib.sha256(payload).hexdigest(),
            "field_count": len(self.records), "version": "fixture", "released_at": "2022-06-23"}), encoding="utf-8")

    def test_chinese_search_returns_candidates_without_certifying_metadata(self):
        result = self.catalog.search("开始交易日", method="wss")
        self.assertEqual([r["field"] for r in result["fields"]], ["ftdate", "ftdate_new"])
        self.assertEqual(result["search_scope"], "community_field_candidates")
        self.assertFalse(result["executed"])
        self.assertFalse(result["full_wind_catalog"])
        for row in result["fields"]:
            self.assertTrue(row["candidate_only"])
            for flag in ["runtime_query_verified", "official_definition_observed", "parameter_semantics_certified", "current_entitlement_checked"]:
                self.assertFalse(row[flag])
            for unknown in ["options_schema", "source", "unit", "definition"]:
                self.assertIsNone(row[unknown])

    def test_method_references_filter_and_pagination(self):
        row = self.catalog.search("ftdate", method="wsd")["fields"][0]
        self.assertEqual(row["methods"], ["wsd"])
        self.assertEqual(len(row["references"]), 1)
        first = self.catalog.search("开始交易日", limit=1)
        second = self.catalog.search("开始交易日", limit=1, offset=first["next_offset"])
        self.assertEqual(second["fields"][0]["field"], "ftdate_new")
        self.assertIsNone(second["next_offset"])
        self.assertEqual(self.catalog.search("不存在")["fields"], [])

    def test_changed_or_missing_catalog_cannot_be_used(self):
        path = self.root / "references/community-fields.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(Problem) as caught:
            self.catalog.search("ftdate")
        self.assertEqual(caught.exception.code, "CANDIDATE_CATALOG_UNAVAILABLE")
        path.unlink()
        with self.assertRaises(Problem):
            self.catalog.search("ftdate")

    def test_parameter_bounds(self):
        for kwargs in [{"method": "wset"}, {"method": []}, {"method": "wsed"}, {"limit": True}, {"limit": 51}, {"offset": -1}]:
            with self.assertRaises(Problem):
                self.catalog.search("ftdate", **kwargs)
        for text in [None, "", "？", "a" * 501]:
            with self.assertRaises(Problem):
                self.catalog.search(text)

    def test_static_extractor_preserves_literal_and_comment_without_execution(self):
        text = '''raise RuntimeError("must not run")
def getTest(security, *args, **kwargs):
    # 获取CTD(支持历史)时间序列
    return w.wsd(security, "tbf_CTD2", *args, **kwargs)
'''
        row = extract_module(text, "wsd", "windget/wsd.py")[0]
        self.assertEqual(row["field"], "tbf_ctd2")
        self.assertEqual(row["label"], "CTD(支持历史)")
        self.assertEqual(row["reference"]["literal_field"], "tbf_CTD2")
        self.assertEqual(row["reference"]["line"], 2)

    def test_extractor_rejects_dynamic_or_mislabelled_calls_and_unpinned_wheel(self):
        for call in ['w.wss(security, field)', 'w.wsd(security, "ftdate")', 'w.wss(security, "a,b")']:
            with self.assertRaises(ValueError):
                extract_module('def getTest(security):\n    # 获取字段\n    return ' + call, "wss", "fixture.py")
        with self.assertRaises(ValueError):
            extract_module('def getTest(security):\n    log()\n    # 获取字段\n    return w.wss(security,"ftdate")', "wss", "fixture.py")
        wheel = self.root / "unknown.whl"
        wheel.write_bytes(b"untrusted version")
        with self.assertRaises(ValueError):
            build(wheel, self.root)


if __name__ == "__main__":
    unittest.main()
