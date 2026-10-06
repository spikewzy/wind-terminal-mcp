import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.entities import recognize, text_span_context
from wind_bridge.service import Service
from wind_bridge.storage import Store


class EntityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []
        self.payload = {"status": "0", "message": "OK", "body": {
            "status_code": 200, "succeed": True, "data": [], "message": ""}}
        self.service = Service(self.store, Backend(self.store, self.fake))

    def fake(self, method, arguments):
        self.calls.append((method, arguments))
        return {"ok": True, "raw": {"ErrorCode": 0, "Codes": ["fer"], "Fields": ["details"],
                                    "Times": ["2026-09-29"], "Data": [[json.dumps(self.payload)]]}}

    def test_same_name_different_code_is_not_registered_or_fetched(self):
        name = "中国:沿海:库存量:豆粕"
        self.service.register([{"code": "V0103973", "name": name, "unit": "万吨"}], "test_fixture", "Unit test")
        self.payload["body"]["data"] = [[{"entity": name, "fullName": name, "id": "S6999828", "type": "edbIndex"}]]
        before = self.store.catalog()
        result = recognize(self.service, name)
        self.assertEqual([c[0] for c in self.calls], ["wai"])
        self.assertEqual(result["edb_candidates"][0]["code"], "S6999828")
        self.assertEqual(result["warnings"][0]["local_codes"], ["V0103973"])
        self.assertTrue(result["selection_required"])
        self.assertFalse(result["full_catalog_search"])
        self.assertIsNone(result["edb_candidates"][0]["local_metadata"])
        self.assertEqual(self.store.catalog(), before)

    def test_numeric_financial_ids_and_alternatives_are_not_field_names(self):
        entity = {"entity": "营业收入", "id": "84952", "type": "stockBondIndex", "param": "600519.SH",
                  "candidateEntities": [{"entity": "营业收入", "id": "255842", "type": "stockBondIndex"}]}
        self.payload["body"]["data"] = [[entity]]
        result = recognize(self.service, "贵州茅台营业收入")
        self.assertEqual(result["edb_candidates"], [])
        self.assertEqual(result["entities"][0]["raw_entity"], entity)
        self.assertIsNone(result["entities"][0]["windpy_field"])

    def test_optional_bundle_failure_keeps_raw_recognition_and_no_extra_native_queries(self):
        entity = {"entity": "营业收入", "id": "84952", "type": "stockBondIndex",
                  "candidateEntities": [{"entity": "营业收入", "id": "255842", "type": "stockIndex"}]}
        self.payload["body"]["data"] = [[entity]]
        with patch("wind_bridge.bundle_metadata._read_index", side_effect=OSError("missing metadata")):
            result = recognize(self.service, "贵州茅台营业收入")
        self.assertEqual([call[0] for call in self.calls], ["wai"])
        self.assertEqual(result["entities"][0]["raw_entity"], entity)
        self.assertFalse(result["bundle_metadata"]["available"])
        self.assertEqual([item["status"] for item in result["entities"][0]["financial_candidate_context"]],
                         ["metadata_unavailable", "metadata_unavailable"])

    def test_errorcode_zero_does_not_hide_application_failure_and_raw_survives(self):
        self.payload["body"].update(status_code=503, succeed=False, message="service unavailable")
        with self.assertRaises(Problem) as raised:
            recognize(self.service, "中国CPI")
        self.assertEqual(raised.exception.code, "WAI_APPLICATION_ERROR")
        receipt = self.store.read(raised.exception.details["receipt_id"])
        self.assertEqual(receipt["response"]["raw"]["ErrorCode"], 0)
        self.assertIn("503", receipt["response"]["raw"]["Data"][0][0])
        with self.store.connect() as con:
            row = con.execute("SELECT status FROM validations WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone()
        self.assertEqual(row[0], "failed")

    def test_malformed_and_false_status_cannot_be_success(self):
        for payload, code in [
            ({"status": False, "body": {}}, "WAI_APPLICATION_ERROR"),
            ({"status": "0", "body": {"statusCode": 200, "succeed": True, "data": {}}}, "WAI_PAYLOAD_INVALID"),
            ({"status": "0", "body": {"statusCode": 200, "status_code": 503, "succeed": True, "data": []}}, "WAI_APPLICATION_ERROR"),
        ]:
            with self.subTest(payload=payload):
                self.payload = payload
                with self.assertRaises(Problem) as raised:
                    recognize(self.service, "中国CPI")
                self.assertEqual(raised.exception.code, code)

    def test_empty_recognition_and_documented_status_spelling(self):
        self.payload["body"]["statusCode"] = self.payload["body"].pop("status_code")
        result = recognize(self.service, "unrecognized input")
        self.assertTrue(result["ok"])
        self.assertEqual(result["entities"], [])
        self.assertFalse(result["full_catalog_search"])
        self.assertFalse(result["unit_frequency_source_returned"])

    def test_generic_inventory_preserves_each_paragraph_and_unmatched_product(self):
        texts = ["中国:库存:碳酸锂", "中国:库存:聚丙烯"]
        entity = {"entity": "库存", "fullName": "库存", "id": "S0029672", "type": "edbIndex",
                  "startIndex": 3, "endIndex": 4,
                  "candidateEntities": [{"entity": "库存", "id": "S0029672", "type": "edbIndex"}]}
        self.payload["body"]["data"] = [[entity], [entity]]
        before = self.store.catalog()
        result = recognize(self.service, "\n".join(texts))
        self.assertEqual([call[0] for call in self.calls], ["wai"])
        self.assertEqual(len(result["edb_candidates"]), 1)
        candidate = result["edb_candidates"][0]
        self.assertEqual([(o["paragraph_index"], o["origin"]) for o in candidate["occurrences"]],
                         [(0, "recognized_entity"), (0, "alternative_entity"),
                          (1, "recognized_entity"), (1, "alternative_entity")])
        for index, occurrence in enumerate(candidate["occurrences"]):
            self.assertEqual(occurrence["text_match_scope"], "partial_paragraph")
            self.assertEqual(occurrence["matched_text"], "库存")
            self.assertEqual(occurrence["unmatched_text"],
                             {"before": "中国:", "after": texts[index // 2][5:]})
        self.assertEqual([r["status"] for r in result["edb_text_coverage"]["paragraphs"]],
                         ["partial_text_candidates_only"] * 2)
        self.assertEqual([w["paragraph_index"] for w in result["warnings"]
                          if w["code"] == "EDB_PARTIAL_TEXT_MATCH"], [0, 1])
        self.assertFalse(candidate["selection_verified"])
        self.assertFalse(candidate["entitlement_verified"])
        self.assertEqual(self.store.catalog(), before)

    def test_full_text_still_preserves_alternative_codes_and_uncertainty(self):
        text = "波罗的海干散货指数(BDI)"
        entity = {"entity": text.lower(), "id": "S0031550", "type": "edbIndex",
                  "startIndex": 0, "endIndex": len(text) - 1,
                  "candidateEntities": [{"entity": text, "id": "M123456", "type": "edbIndex"}]}
        self.payload["body"]["data"] = [[entity]]
        result = recognize(self.service, text)
        row = result["edb_text_coverage"]["paragraphs"][0]
        self.assertEqual(row["status"], "full_text_candidates")
        self.assertEqual(row["full_text_candidate_codes"], ["S0031550", "M123456"])
        self.assertFalse(row["selection_verified"])
        self.assertTrue(result["selection_required"])
        self.assertTrue(all(not c["selection_verified"] for c in result["edb_candidates"]))
        self.assertEqual(result["entities"][0]["raw_entity"], entity)

    def test_invalid_or_missing_offsets_never_claim_matching_qualifiers(self):
        entity = {"entity": "库存", "id": "S0029672", "type": "edbIndex"}
        for offsets in [{}, {"startIndex": True, "endIndex": 4},
                        {"startIndex": 3, "endIndex": "4"},
                        {"startIndex": -1, "endIndex": 4},
                        {"startIndex": 3, "endIndex": 99},
                        {"startIndex": 4, "endIndex": 3},
                        {"startIndex": 0, "endIndex": 1},
                        {"startIndex": 3, "endIndex": 5}]:
            with self.subTest(offsets=offsets):
                context = text_span_context("中国:库存:碳酸锂", {**entity, **offsets})
                self.assertFalse(context["span_verified"])
                self.assertEqual(context["text_match_scope"], "unverified")
                self.assertIsNone(context["unmatched_text"])
        self.payload["body"]["data"] = [[entity]]
        result = recognize(self.service, "中国:库存:碳酸锂")
        self.assertEqual(result["edb_text_coverage"]["paragraphs"][0]["status"], "unverified_text_spans")

    def test_changed_paragraph_segmentation_does_not_assign_original_inputs(self):
        self.payload["body"]["data"] = [[{
            "entity": "库存", "id": "S0029672", "type": "edbIndex", "startIndex": 0, "endIndex": 1}]]
        result = recognize(self.service, "库存\n碳酸锂库存")
        coverage = result["edb_text_coverage"]
        self.assertEqual((coverage["input_paragraph_count"], coverage["returned_paragraph_count"]), (2, 1))
        self.assertFalse(coverage["paragraph_counts_match"])
        self.assertIsNone(coverage["paragraphs"][0]["input_text"])
        self.assertEqual(coverage["paragraphs"][0]["status"], "unaligned_paragraphs")
        self.assertFalse(result["edb_candidates"][0]["occurrences"][0]["span_verified"])

    def test_commodity_param_ids_are_not_promoted_to_indicator_candidates(self):
        entity = {"entity": "碳酸锂", "id": "10000001", "type": "commodity",
                  "startIndex": 6, "endIndex": 8,
                  "param": json.dumps([{"id": "S5470447", "type": "edbIndex"}]),
                  "candidateEntities": [{"entity": "碳酸锂", "id": "S5470447", "type": "commodity"}]}
        self.payload["body"]["data"] = [[entity]]
        result = recognize(self.service, "中国:库存:碳酸锂")
        self.assertEqual(result["edb_candidates"], [])
        self.assertEqual(result["entities"][0]["raw_entity"], entity)
        self.assertEqual(result["edb_text_coverage"]["paragraphs"][0]["status"], "no_edb_candidates")
        self.assertEqual([call[0] for call in self.calls], ["wai"])

    def test_invalid_input_does_not_start_native_query(self):
        for text in [None, "", " \n ", "x" * 5001]:
            with self.subTest(text_type=type(text)):
                with self.assertRaises(Problem):
                    recognize(self.service, text)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
