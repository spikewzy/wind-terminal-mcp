import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.common import Problem
from wind_bridge.fields import FieldCatalog
from wind_bridge.query_recipes import QueryRecipes
from wind_bridge.storage import Store


class QueryRecipeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "references").mkdir()
        self.store = Store(self.root / "runtime")
        self.arguments = {"tablename": "conversionfactor", "options": "windcode=T2612.CFE"}
        self.receipt = self.store.save("wset", self.arguments, {"ok": True, "raw": {
            "ErrorCode": 0, "Codes": ["1"], "Fields": ["wind_code", "cf"],
            "Times": ["2026-09-30"], "Data": [["TEST.IB"], [0.95]]}})
        self.store.validation(self.receipt["receipt_id"], "passed", {})
        self.rows = [{"name": "treasury_bonds", "title": "国债期货可交割券及转换因子", "tags": ["交割", "期货"],
                      "method": "wset", "arguments": self.arguments, "receipt_id": self.receipt["receipt_id"],
                      "runtime_query_verified": True, "note": "Fixture date-specific request"}]
        self.save()
        self.catalog = QueryRecipes(self.store, self.root)

    def save(self):
        (self.root / "references/verified-query-recipes.json").write_text(json.dumps({"recipes": self.rows}, ensure_ascii=False), encoding="utf-8")

    def test_table_and_chinese_search_preserve_request_without_execution(self):
        for question in ["conversionfactor", "可交割券", "windcode", "期货 转换因子"]:
            result = self.catalog.search(question, method="wset")
            sample = result["recipes"][0]
            self.assertEqual(sample["arguments"], self.arguments)
            self.assertTrue(sample["runtime_query_verified"])
            self.assertTrue(sample["successful_sample_available"])
            self.assertEqual(sample["value_availability"]["cf"]["populated_count"], 1)
            self.assertFalse(sample["parameter_semantics_certified"])
            self.assertFalse(sample["example_is_default"])
            self.assertFalse(result["executed"])
            self.assertFalse(result["current_entitlement_checked"])
            self.assertFalse(result["full_wind_catalog"])

    def test_missing_receipt_downgrades_distributed_historical_example(self):
        sample = QueryRecipes(Store(self.root / "fresh"), self.root).search("conversionfactor")["recipes"][0]
        self.assertFalse(sample["runtime_query_verified"])
        self.assertFalse(sample["successful_sample_available"])
        self.assertEqual(sample["evidence_issue"], "NO_RECEIPT")
        self.assertEqual(sample["arguments"], self.arguments)

    def test_changed_request_is_not_certified_by_original_receipt(self):
        self.rows[0]["arguments"] = {**self.arguments, "options": "windcode=OTHER.CFE"}
        self.save()
        sample = self.catalog.search("conversionfactor")["recipes"][0]
        self.assertFalse(sample["runtime_query_verified"])
        self.assertEqual(sample["evidence_issue"], "RECIPE_RECEIPT_MISMATCH")

    def test_null_or_blank_table_does_not_prove_data_available(self):
        for value in [None, " "]:
            receipt = self.store.save("wset", self.arguments, {"ok": True, "raw": {
                "ErrorCode": 0, "Codes": ["1"], "Fields": ["wind_code", "cf"],
                "Times": ["2026-09-30"], "Data": [[value], [value]]}})
            self.store.validation(receipt["receipt_id"], "passed", {})
            self.rows[0]["receipt_id"] = receipt["receipt_id"]
            self.save()
            sample = self.catalog.search("conversionfactor")["recipes"][0]
            self.assertTrue(sample["runtime_query_verified"])
            self.assertFalse(sample["successful_sample_available"])

    def test_paging_method_filter_and_invalid_input(self):
        self.rows.append({**self.rows[0], "name": "other_date"})
        self.save()
        first = self.catalog.search("期货", limit=1)
        second = self.catalog.search("期货", limit=1, offset=first["next_offset"])
        self.assertEqual(first["total_matches"], 2)
        self.assertNotEqual(first["recipes"][0]["name"], second["recipes"][0]["name"])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(self.catalog.search("期货", method="wsd")["recipes"], [])
        for kwargs in [{"method": "wupf"}, {"method": []}, {"limit": True}, {"limit": 31}, {"offset": -1}]:
            with self.assertRaises(Problem):
                self.catalog.search("期货", **kwargs)
        for question in ["", "？", "x" * 501, None]:
            with self.assertRaises(Problem):
                self.catalog.search(question)

    def test_field_label_supports_search_without_claiming_official_definition(self):
        (self.root / "verification").mkdir()
        (self.root / "verification/wind-api-ui-fields.json").write_text('{"fields": []}', encoding="utf-8")
        arguments = {"codes": "CU2611.SHF", "fields": "st_stock", "options": "tradeDate=20260929"}
        receipt = self.store.save("wss", arguments, {"ok": True, "raw": {
            "ErrorCode": 0, "Codes": ["CU2611.SHF"], "Fields": ["ST_STOCK"],
            "Times": ["2026-09-30"], "Data": [[0]]}})
        self.store.validation(receipt["receipt_id"], "passed", {})
        self.rows.append({"name": "warehouse", "method": "wss", "arguments": arguments,
                          "field_labels": {"st_stock": "注册仓单数量"}, "receipt_id": receipt["receipt_id"]})
        self.save()
        row = FieldCatalog(self.store, self.root).search("仓单")["fields"][0]
        self.assertEqual(row["field"], "st_stock")
        self.assertTrue(row["successful_sample_available"])
        self.assertFalse(row["official_definition_observed"])
        self.assertIsNone(row["unit"])
