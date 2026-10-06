import gzip
import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.common import Problem
from wind_bridge.fields import FieldCatalog
from wind_bridge.storage import Store


class FieldEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "verification").mkdir()
        (self.root / "references").mkdir()
        self.store = Store(self.root / "runtime")
        self.arguments = {"codes": "600519.SH", "fields": "oper_rev,net_profit_is", "options": "rptDate=20251231;rptType=1;unit=1"}
        self.receipt = self.store.save("wss", self.arguments, {"ok": True, "raw": {
            "ErrorCode": 0, "Codes": ["600519.SH"], "Fields": ["OPER_REV", "NET_PROFIT_IS"],
            "Times": ["2026-09-29"], "Data": [[100], [20]]}})
        self.store.validation(self.receipt["receipt_id"], "passed", {})
        notes = {"metadata_source_id": "wind_api_ui", "observed_date": "2026-09-29",
                 "fields": [{"field": "oper_rev", "name": "营业收入", "meaning": "Fixture meaning with a fallback warning",
                             "source": "Fixture publisher", "unit": None, "category": "财务报表"}]}
        (self.root / "verification/wind-api-ui-fields.json").write_text(json.dumps(notes), encoding="utf-8")
        self.recipes = {"recipes": [{"name": "equity_financials", "method": "wss", "arguments": self.arguments,
                                    "receipt_id": self.receipt["receipt_id"], "evidence": "fixture", "note": "Fixture",
                                    "runtime_query_verified": True}],
                        "known_invalid_candidates": [{"field": "84952", "error": -40522006}]}
        self.save_recipes()
        self.catalog = FieldCatalog(self.store, self.root)

    def save_recipes(self):
        (self.root / "references/verified-query-recipes.json").write_text(json.dumps(self.recipes), encoding="utf-8")

    def test_definition_and_successful_query_do_not_certify_parameters_or_units(self):
        result = self.catalog.search("营业收入", method="wss")
        row = result["fields"][0]
        self.assertTrue(row["official_definition_observed"])
        self.assertTrue(row["successful_sample_available"])
        self.assertFalse(row["parameter_semantics_certified"])
        self.assertIsNone(row["unit"])
        self.assertEqual(row["source"], "Fixture publisher")
        self.assertEqual(row["definition"]["metadata_source_id"], "wind_api_ui")
        self.assertEqual(row["examples"][0]["arguments"], self.arguments)
        self.assertFalse(result["executed"])
        self.assertFalse(result["full_wind_catalog"])
        self.assertFalse(result["current_entitlement_checked"])

    def test_runtime_only_field_keeps_definition_unknown(self):
        row = self.catalog.search("NET_PROFIT_IS")["fields"][0]
        self.assertTrue(row["successful_sample_available"])
        self.assertFalse(row["official_definition_observed"])
        self.assertIsNone(row["name"])
        self.assertIsNone(row["source"])
        self.assertIsNone(row["definition"])

    def test_null_only_field_is_not_certified_by_nonempty_neighbor(self):
        receipt = self.store.save("wss", self.arguments, {"ok": True, "raw": {
            "ErrorCode": 0, "Codes": ["600519.SH"], "Fields": ["OPER_REV", "NET_PROFIT_IS"],
            "Times": ["2026-09-30"], "Data": [[100], [None]]}})
        self.store.validation(receipt["receipt_id"], "passed", {})
        self.recipes["recipes"][0]["receipt_id"] = receipt["receipt_id"]
        self.save_recipes()
        missing = self.catalog.search("net_profit_is")["fields"][0]
        present = self.catalog.search("oper_rev")["fields"][0]
        self.assertTrue(missing["successful_query_available"])
        self.assertFalse(missing["successful_sample_available"])
        self.assertEqual(missing["examples"][0]["value_availability"]["missing_count"], 1)
        self.assertTrue(present["successful_sample_available"])

    def test_one_field_multi_security_series_counts_values_across_codes(self):
        args = {"codes": "600519.SH,000001.SZ", "fields": "oper_rev", "beginTime": "2024-03-31",
                "endTime": "2024-06-30", "options": "Period=Q;Days=Alldays"}
        receipt = self.store.save("wsd", args, {"ok": True, "raw": {
            "ErrorCode": 0, "Codes": ["600519.SH", "000001.SZ"], "Fields": ["OPER_REV"],
            "Times": ["2024-03-31", "2024-06-30"], "Data": [[None, 0], [20, None]]}})
        self.store.validation(receipt["receipt_id"], "passed", {})
        self.recipes["recipes"][0].update(method="wsd", arguments=args, receipt_id=receipt["receipt_id"])
        self.save_recipes()
        counts = self.catalog.search("oper_rev")["fields"][0]["examples"][0]["value_availability"]
        self.assertEqual(counts, {"observation_count": 4, "populated_count": 2, "missing_count": 2, "all_values_missing": False})

    def test_blank_text_and_empty_result_do_not_prove_available_data(self):
        for values in [[], [None], [" "]]:
            receipt = self.store.save("wss", self.arguments, {"ok": True, "raw": {
                "ErrorCode": 0, "Codes": ["600519.SH"] if values else [], "Fields": ["OPER_REV", "NET_PROFIT_IS"],
                "Times": ["2026-09-30"], "Data": [values, values]}})
            self.store.validation(receipt["receipt_id"], "passed", {})
            self.recipes["recipes"][0]["receipt_id"] = receipt["receipt_id"]
            self.save_recipes()
            row = self.catalog.search("oper_rev")["fields"][0]
            self.assertTrue(row["successful_query_available"])
            self.assertFalse(row["successful_sample_available"])

    def test_recipe_claim_is_not_trusted_when_receipt_request_differs(self):
        self.recipes["recipes"][0]["arguments"] = {**self.arguments, "options": "rptType=2"}
        self.save_recipes()
        row = self.catalog.search("oper_rev")["fields"][0]
        self.assertTrue(row["official_definition_observed"])
        self.assertFalse(row["successful_sample_available"])
        self.assertEqual(row["examples"][0]["evidence_issue"], "RECIPE_RECEIPT_MISMATCH")

    def test_missing_or_failed_validation_downgrades_example(self):
        self.store.validation(self.receipt["receipt_id"], "failed", {})
        sample = self.catalog.search("oper_rev")["fields"][0]["examples"][0]
        self.assertEqual(sample["evidence_issue"], "UNVALIDATED_INPUT_RECEIPT")
        self.assertFalse(sample["runtime_query_verified"])
        (self.store.receipts / (self.receipt["receipt_id"] + ".json.gz")).unlink()
        sample = self.catalog.search("oper_rev")["fields"][0]["examples"][0]
        self.assertEqual(sample["evidence_issue"], "NO_RECEIPT")

    def test_other_source_cannot_be_used_as_wind_runtime_evidence(self):
        path = self.store.receipts / (self.receipt["receipt_id"] + ".json.gz")
        receipt = dict(self.receipt, data_source_id="alice_mcp")
        with gzip.open(path, "wt") as stream:
            json.dump(receipt, stream)
        sample = self.catalog.search("oper_rev")["fields"][0]["examples"][0]
        self.assertEqual(sample["evidence_issue"], "WRONG_RECEIPT_TYPE")
        self.assertFalse(sample["runtime_query_verified"])

    def test_invalid_entity_id_and_unknown_query_do_not_resolve_to_fields(self):
        result = self.catalog.search("84952")
        self.assertEqual(result["fields"], [])
        self.assertEqual(result["known_invalid_candidates"][0]["error"], -40522006)
        self.assertEqual(self.catalog.search("未记录指标")["total_matches"], 0)

    def test_filter_pagination_and_input_bounds(self):
        first = self.catalog.search("equity_financials", method="wss", limit=1)
        second = self.catalog.search("equity_financials", method="wss", limit=1, offset=first["next_offset"])
        self.assertEqual(first["total_matches"], 2)
        self.assertNotEqual(first["fields"][0]["field"], second["fields"][0]["field"])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(self.catalog.search("oper_rev", method="wsd")["fields"], [])
        for kwargs in [{"method": "wset"}, {"method": []}, {"limit": True}, {"limit": 51}, {"offset": -1}]:
            with self.assertRaises(Problem):
                self.catalog.search("oper_rev", **kwargs)
        for question in [" ", "？", "x" * 501]:
            with self.assertRaises(Problem):
                self.catalog.search(question)
