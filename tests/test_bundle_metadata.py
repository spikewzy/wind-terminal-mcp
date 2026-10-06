import json
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch

from wind_bridge.bundle_metadata import (FinancialCandidateContext, LANGUAGE_SHA256, attach_language_labels,
                                        digest, parse_indicator_records, parse_language_records,
                                        parse_lookup_tables, parse_sector_records, search_bundle)
from wind_bridge.common import Problem


class BundleMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "vendor-resource"
        self.source.write_bytes(b"fixture metadata")
        source = {"path": str(self.source), "size": self.source.stat().st_size, "sha256": digest(self.source.read_bytes())}
        self.path = self.root / "index.json"
        self.path.write_text(json.dumps({
            "schema_version": 3, "imported_at": "fixture",
            "inputs": {key: source for key in ("indicators", "parameters", "language_zh", "language_en", "sectors", "index_sectors")},
            "counts": {"indicators": 2},
            "sectors": self.sector_fixture()[0],
            "tables": [{"code": "90905", "name": "报表类型", "datatype": "Integer", "defaultItem": "",
                        "options": [{"kind": "item", "name": "合并报表", "value": "1"},
                                    {"kind": "item", "name": "母公司报表", "value": "2"}]},
                       {"code": "date", "name": "日期", "datatype": "String", "defaultItem": "",
                        "options": [{"kind": "Customitem", "name": "最近报告期", "value": "{S_report_date(-1,4)}"}]}],
            "records": [{"name": "营业收入", "node_id": node, "kind": "indicator", "internal_expression": "s_stm07_is",
                         "category_path": ["财务数据", sector], "unresolved_root_node_id": 9,
                         "labels": {"zh-CN": ["营业收入", "营业收入"], "en-US": ["Total Revenue", "Total Revenue"]},
                         "language_id": 30, "record_offset": 64}
                        for node, sector in [(10, "一般企业"), (20, "银行")]]
        }, ensure_ascii=False), encoding="utf-8")

    def search(self, question, **kwargs):
        return search_bundle(question, path=self.path, **kwargs)

    def sector_fixture(self):
        xml = '<a><s i="a001000000000000" n="沪深股票"><s i="1000000090000000" n="沪深300" e="CSI 300" t="1"/><s i="a001030201000000" n="沪深300" e="CSI 300" t="1"/></s></a>'
        mapping = b"000300.SH,a001030201000000\n"
        return parse_sector_records(xml.encode("utf-8-sig"), mapping)

    def test_sector_namespace_preserves_same_name_ids_and_exact_index_link(self):
        rows, pairs = self.sector_fixture()
        self.assertEqual(pairs, [{"wind_code": "000300.SH", "sector_id": "a001030201000000", "sector_node_found": True}])
        self.assertEqual(rows[0]["child_count"], 2)
        self.assertEqual(rows[1]["category_path"], ["沪深股票"])
        self.assertEqual(rows[1]["index_codes"], [])
        self.assertEqual(rows[2]["index_codes"], ["000300.SH"])
        self.assertNotEqual(rows[1]["sector_id"], rows[2]["sector_id"])
        self.assertEqual(rows[2]["raw_attributes"]["t"], "1")

    def test_sector_mapping_without_tree_node_is_preserved_without_fabricated_name(self):
        rows, pairs = parse_sector_records(b"<a/>", b"000300.SH,a001030201000000\n")
        self.assertEqual(rows, [])
        self.assertEqual(pairs, [{"wind_code": "000300.SH", "sector_id": "a001030201000000", "sector_node_found": False}])

    def test_sector_input_rejects_entities_duplicate_ids_and_invalid_mappings(self):
        duplicate = b'<a><s i="a001000000000000" n="one"/><s i="a001000000000000" n="two"/></a>'
        cases = [(b'<!DOCTYPE a [<!ENTITY e SYSTEM "file:///fixture">]><a/>', b""),
                 (duplicate, b""), (b"<a/>", b"000300.SH,000300.SH\n"),
                 (b"<a/>", b"000300.SH,a001030201000000,extra\n")]
        for xml, mapping in cases:
            with self.subTest(xml=xml):
                with self.assertRaises(Problem) as raised:
                    parse_sector_records(xml, mapping)
                self.assertEqual(raised.exception.code, "UNSUPPORTED_METADATA_FORMAT")

    def test_sector_search_retains_ambiguity_and_does_not_certify_membership(self):
        first = self.search("CSI 300", kind="sector", limit=1)
        second = self.search("CSI 300", kind="sector", limit=1, offset=first["next_offset"])
        self.assertEqual(first["total_matches"], 2)
        self.assertNotEqual(first["results"][0]["sector_id"], second["results"][0]["sector_id"])
        for question in ["000300.SH", "a001030201000000", "沪深300 沪深股票"]:
            self.assertIn("a001030201000000", [row["sector_id"] for row in self.search(question, kind="sector")["results"]])
        self.assertEqual(first["data_source_id"], "wind_terminal_api")
        self.assertFalse(first["executed"])
        self.assertEqual(set(first["source_files"]), {"sectors", "index_sectors"})
        for key in ["constituents_included", "current_entitlement_checked", "full_market_coverage_certified"]:
            self.assertFalse(first["results"][0][key])

    def test_sector_search_checks_index_map_as_well_as_xml(self):
        snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        mapping = self.root / "index-map"
        mapping.write_bytes(b"current mapping")
        snapshot["inputs"]["index_sectors"] = {"path": str(mapping), "sha256": digest(mapping.read_bytes()), "size": mapping.stat().st_size}
        self.path.write_text(json.dumps(snapshot), encoding="utf-8")
        self.assertTrue(self.search("CSI 300", kind="sector")["current_source_matches_snapshot"])
        mapping.write_bytes(b"changed mapping")
        result = self.search("CSI 300", kind="sector")
        self.assertTrue(result["source_files"]["sectors"]["current_source_matches_snapshot"])
        self.assertFalse(result["current_source_matches_snapshot"])
        mapping.unlink()
        self.assertIsNone(self.search("CSI 300", kind="sector")["current_source_matches_snapshot"])

    def test_same_name_nodes_remain_candidates_not_executable_fields(self):
        result = self.search("营业收入")
        self.assertEqual(result["total_matches"], 2)
        self.assertEqual([row["node_id"] for row in result["results"]], [10, 20])
        self.assertTrue(all(row["windpy_field"] is None and not row["windpy_mapping_verified"] for row in result["results"]))
        self.assertEqual(result["metadata_source_id"], "wind_api_bundle")
        self.assertFalse(result["executed"])
        self.assertFalse(result["full_wind_catalog"])
        self.assertTrue(result["current_source_matches_snapshot"])

    def test_option_values_and_macros_stay_unbound_text(self):
        rows = self.search("报表类型", kind="parameter")["results"]
        self.assertEqual([row["option"]["value"] for row in rows], ["1", "2"])
        self.assertTrue(all(row["api_parameter_name"] is None for row in rows))
        result = self.search("最近报告期", kind="parameter")
        self.assertEqual(result["results"][0]["option"]["value"], "{S_report_date(-1,4)}")
        self.assertFalse(result["executed"])

    def test_vendor_update_marks_snapshot_stale_and_missing_source_is_unknown(self):
        self.source.write_bytes(b"new vendor data")
        self.assertFalse(self.search("营业收入")["current_source_matches_snapshot"])
        self.source.unlink()
        self.assertIsNone(self.search("营业收入")["current_source_matches_snapshot"])

    def test_gb18030_xml_preserves_internal_values_without_translating_them(self):
        xml = '<?xml version="1.0" encoding="gb2312"?><lookupTables><lookupTable code="91015" name="币种"><item ID="1" name="人民币" value="CNY"/><Customitem name="自定义" value="{formula()}"/></lookupTable></lookupTables>'
        tables = parse_lookup_tables(xml.encode("gb18030"))
        self.assertEqual(tables[0]["name"], "币种")
        self.assertEqual(tables[0]["options"][0]["value"], "CNY")
        self.assertEqual(tables[0]["options"][1]["kind"], "Customitem")
        with self.assertRaises(Problem):
            parse_lookup_tables(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///fixture">]><lookupTables/>')

    def test_unknown_binary_build_is_not_guessed(self):
        with self.assertRaises(Problem) as raised:
            parse_indicator_records(b"changed or encrypted metadata")
        self.assertEqual(raised.exception.code, "UNRECOGNIZED_METADATA_BUILD")
        with self.assertRaises(Problem) as raised:
            parse_language_records(b"changed language metadata", "zh-CN")
        self.assertEqual(raised.exception.code, "UNRECOGNIZED_METADATA_BUILD")

    def language_fixture(self, label_ids=(30, 31)):
        labels = [("营业收入", "收入"), ("净利润", "净利润")]
        content, index = b"", b""
        for label_id, variants in zip(label_ids, labels):
            first, second = [value.encode("utf-8") for value in variants]
            index += struct.pack(">IIHH", label_id, len(content), len(first), len(second))
            content += first + second
        header = bytearray(64)
        header[:4] = b"WRES"
        struct.pack_into(">IIIII", header, 8, 1, 20260226, 2, 64, 88)
        return bytes(header) + index + content

    def parse_fixture_language(self, data):
        with patch.dict(LANGUAGE_SHA256, {"zh-CN": digest(data)}):
            return parse_language_records(data, "zh-CN")

    def test_language_reader_preserves_two_text_variants(self):
        self.assertEqual(self.parse_fixture_language(self.language_fixture())[30], ["营业收入", "收入"])

    def test_language_index_rejects_duplicate_ids_bounds_and_invalid_utf8(self):
        bad_offset = bytearray(self.language_fixture())
        struct.pack_into(">I", bad_offset, 68, 99999)
        bad_utf8 = bytearray(self.language_fixture())
        bad_utf8[-1] = 255
        for data in [self.language_fixture((30, 30)), bytes(bad_offset), bytes(bad_utf8)]:
            with self.subTest(data_size=len(data)):
                with self.assertRaises(Problem) as raised:
                    self.parse_fixture_language(data)
                self.assertEqual(raised.exception.code, "UNSUPPORTED_METADATA_FORMAT")

    def test_language_binding_detects_previous_record_id_misalignment(self):
        rows = [{"node_id": 10, "name": "营业收入", "language_id": 30}]
        languages = {"zh-CN": {30: ["营业总收入", "营业总收入"]}, "en-US": {30: ["Total Op Rev", "Total Op Rev"]}}
        with self.assertRaises(Problem) as raised:
            attach_language_labels(rows, languages)
        self.assertEqual(raised.exception.code, "METADATA_LANGUAGE_MISMATCH")

    def test_english_search_returns_original_chinese_nodes(self):
        result = self.search("Total Revenue")
        self.assertEqual([row["node_id"] for row in result["results"]], [10, 20])
        self.assertEqual({row["name"] for row in result["results"]}, {"营业收入"})
        self.assertEqual(len(result["source_files"]), 3)

    def test_candidate_join_preserves_same_name_categories_without_selecting(self):
        context = FinancialCandidateContext(self.path)
        for node, sector in [(10, "一般企业"), (20, "银行")]:
            result = context.match({"type": "stockBondIndex", "id": str(node), "entity": "营业收入"})
            self.assertEqual(result["status"], "node_id_and_name_match")
            self.assertEqual(result["bundle_record"]["category_path"][-1], sector)
            self.assertIsNone(result["windpy_field"])
            self.assertFalse(result["selection_verified"])
        self.assertEqual(context.match({"type": "stockBondIndex", "id": "84952", "entity": "营业收入"})["status"], "node_id_not_found")

    def test_candidate_id_collision_or_wrong_namespace_cannot_bind(self):
        context = FinancialCandidateContext(self.path)
        result = context.match({"type": "stockBondIndex", "id": "10", "entity": "营业总收入"})
        self.assertEqual(result["status"], "name_mismatch")
        self.assertNotIn("bundle_record", result)
        for entity_type in ["edbIndex", "enterprise", [], None]:
            self.assertIsNone(context.match({"type": entity_type, "id": "10", "entity": "营业收入"}))

    def test_candidate_context_requires_current_sources_and_available_index(self):
        self.source.write_bytes(b"updated metadata")
        context = FinancialCandidateContext(self.path)
        self.assertFalse(context.metadata["available"])
        self.assertEqual(context.match({"type": "stockIndex", "id": "10", "entity": "营业收入"})["status"], "metadata_unavailable")
        self.source.unlink()
        self.assertIsNone(FinancialCandidateContext(self.path).metadata["current_source_matches_snapshot"])
        self.path.unlink()
        self.assertFalse(FinancialCandidateContext(self.path).metadata["available"])

    def test_incomplete_context_index_does_not_fail_during_entity_matching(self):
        snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        del snapshot["records"][0]["labels"]
        self.path.write_text(json.dumps(snapshot), encoding="utf-8")
        context = FinancialCandidateContext(self.path)
        self.assertFalse(context.metadata["available"])
        self.assertEqual(context.match({"type": "stockIndex", "id": "10", "entity": "营业收入"})["status"], "metadata_unavailable")

    def test_category_filter_pagination_and_bounds(self):
        self.assertEqual(self.search("营业收入 银行")["results"][0]["node_id"], 20)
        first = self.search("营业收入", limit=1)
        second = self.search("营业收入", limit=1, offset=first["next_offset"])
        self.assertEqual(second["results"][0]["node_id"], 20)
        self.assertIsNone(second["next_offset"])
        for kwargs in [{"kind": "api"}, {"kind": []}, {"limit": True}, {"limit": 51}, {"offset": -1}]:
            with self.assertRaises(Problem):
                self.search("营业收入", **kwargs)
