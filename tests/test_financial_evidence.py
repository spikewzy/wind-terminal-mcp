import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wind_bridge.common import ROOT
from wind_bridge.fields import FieldCatalog
from wind_bridge.financial_evidence import REFERENCE, StatementEvidence, statement_reference_checks
from wind_bridge.service import Service
from wind_bridge.storage import Store


class FinancialEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference = json.loads((ROOT / REFERENCE).read_text(encoding="utf-8"))
        self.pdf = self.root / self.reference["documents"][0]["path"]
        self.pdf.parent.mkdir(parents=True)
        self.pdf.write_bytes(b"fixture document; not a real filing")
        self.reference["documents"][0]["sha256"] = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.store = Store(self.root / "runtime")
        self.receipts = []
        recipes = []
        for sample in self.reference["native_samples"]:
            rows = [row for row in self.reference["rows"] if row["report_date"] == sample["report_date"]
                    and row["rpt_type"] == sample["rpt_type"]]
            by_field = {row["field"]: row for row in rows}
            fields = sample["arguments"]["fields"].split(",")
            raw = {"ErrorCode": 0, "Codes": ["600519.SH"], "Fields": [field.upper() for field in fields],
                   "Times": ["2026-09-30"], "Data": [[float(by_field[field]["value_decimal"])] for field in fields]}
            receipt = self.store.save("wss", sample["arguments"], {"ok": True, "raw": raw})
            self.store.validation(receipt["receipt_id"], "passed", {})
            sample.update(receipt_id=receipt["receipt_id"], native_sha256=receipt["sha256"])
            self.receipts.append(receipt)
            recipes.append({"name": "financial_sample", "method": "wss", "arguments": sample["arguments"],
                            "receipt_id": receipt["receipt_id"]})
        self.save_reference()
        (self.root / "verification").mkdir()
        (self.root / "verification/wind-api-ui-fields.json").write_text(json.dumps({"fields": []}), encoding="utf-8")
        (self.root / "references/verified-query-recipes.json").write_text(json.dumps({"recipes": recipes}), encoding="utf-8")
        self.arguments = copy.deepcopy(self.receipts[0]["arguments"])
        self.raw = copy.deepcopy(self.receipts[0]["response"]["raw"])

    def save_reference(self):
        (self.root / REFERENCE).write_text(json.dumps(self.reference), encoding="utf-8")

    def compare(self, arguments=None, raw=None):
        return statement_reference_checks("wss", arguments or self.arguments, raw or self.raw, self.root)

    def test_all_curated_statement_samples_retain_exact_cell_citations(self):
        for receipt in self.receipts:
            result = self.compare(receipt["arguments"], receipt["response"]["raw"])
            self.assertEqual(result["matched_cell_count"], len(receipt["response"]["raw"]["Fields"]))
            self.assertTrue(result["all_raw_cells_covered"])
            self.assertTrue(result["all_covered_cells_match"])
            for cell in result["cells"]:
                reference = cell["reference"]
                self.assertEqual(reference["unit"], "元")
                self.assertEqual(reference["currency"], "CNY")
                self.assertIn(reference["page"], reference["document"]["visually_reviewed_pages"])
            self.assertFalse(result["all_parameter_semantics_certified"])
            self.assertFalse(result["unit_scaling_rule_certified"])
            self.assertFalse(result["point_in_time_safe"])

    def test_option_case_order_spacing_and_literal_date_format(self):
        args = {**self.arguments, "options": " UNIT=1; RPTTYPE=1; rptDate=2025-12-31; "}
        self.assertEqual(self.compare(args)["matched_cell_count"], 4)

    def test_other_options_dates_and_issuers_cannot_inherit_coverage(self):
        for options in ["", "rptDate=20251231;rptType=1", "rptDate=20251231;rptType=1;unit=10000",
                        "rptDate=20251231;rptType=3;unit=1", "rptDate=20231231;rptType=1;unit=1",
                        "rptDate=20250230;rptType=1;unit=1", "rptDate=ED;rptType=1;unit=1",
                        self.arguments["options"] + ";rptType=1", self.arguments["options"] + ";currencyType=CNY"]:
            with self.subTest(options=options):
                result = self.compare({**self.arguments, "options": options})
                self.assertEqual(result["matched_cell_count"], 0)
                self.assertFalse(result["all_raw_cells_covered"])
        raw = {**self.raw, "Codes": ["000001.SZ"]}
        result = self.compare({**self.arguments, "codes": "000001.SZ"}, raw)
        self.assertEqual(result["covered_cell_count"], 0)
        self.assertIsNone(statement_reference_checks("wsd", self.arguments, self.raw, self.root))

    def test_mismatched_identifiers_or_shapes_do_not_produce_matches(self):
        for change in [{"Codes": ["000001.SZ"]}, {"Fields": self.raw["Fields"][::-1]},
                       {"Data": [[1]]}, {"ErrorCode": -1}]:
            result = self.compare(raw={**self.raw, **change})
            self.assertEqual(result["status"], "native_response_not_comparable")
            self.assertEqual(result["matched_cell_count"], 0)

    def test_partial_multicode_multifield_coverage_is_explicit(self):
        args = {**self.arguments, "codes": "600519.SH,000001.SZ", "fields": self.arguments["fields"] + ",close"}
        raw = {**self.raw, "Codes": ["600519.SH", "000001.SZ"], "Fields": self.raw["Fields"] + ["CLOSE"],
               "Data": [column + [100] for column in self.raw["Data"]] + [[1, 2]]}
        result = self.compare(args, raw)
        self.assertEqual(result["raw_cell_count"], 10)
        self.assertEqual(result["covered_cell_count"], 4)
        self.assertTrue(result["all_covered_cells_match"])
        self.assertFalse(result["all_raw_cells_covered"])

    def test_missing_boolean_text_and_nonfinite_values_do_not_match(self):
        for value in [None, True, "85310324833.67", float("nan"), float("inf")]:
            raw = copy.deepcopy(self.raw)
            raw["Data"][1][0] = value
            result = self.compare(raw=raw)
            self.assertEqual(result["cells"][1]["status"], "value_unavailable")
            self.assertEqual(result["matched_cell_count"], 3)
            self.assertFalse(result["all_covered_cells_match"])

    def test_difference_is_reported_without_replacing_vendor_values(self):
        raw = copy.deepcopy(self.raw)
        raw["Data"][1][0] = 82320067101.68  # The distinct attributable-profit line.
        before = copy.deepcopy(raw)
        result = self.compare(raw=raw)
        self.assertEqual(result["cells"][1]["status"], "different")
        self.assertEqual(result["cells"][1]["reference"]["not_equivalent_to"], "归属于母公司股东的净利润")
        self.assertEqual(raw, before)

    def test_changed_missing_or_malformed_reference_downgrades(self):
        self.pdf.write_bytes(b"changed")
        result = self.compare()
        self.assertEqual(result["status"], "reference_unavailable")
        self.assertEqual(result["issue"], "document_fingerprint_changed")
        self.pdf.unlink()
        self.assertEqual(self.compare()["status"], "reference_unavailable")
        (self.root / REFERENCE).write_text("[]", encoding="utf-8")
        self.assertEqual(self.compare()["status"], "reference_unavailable")
        (self.root / REFERENCE).unlink()
        self.assertEqual(self.compare()["status"], "reference_unavailable")

    def test_document_paths_cannot_escape_filing_directory(self):
        self.reference["documents"][0]["path"] = "../other.pdf"
        self.save_reference()
        self.assertEqual(self.compare()["issue"], "document_path_outside_reference_directory")

    def test_receipt_pagination_retains_whole_snapshot_check(self):
        receipt_id = self.receipts[0]["receipt_id"]
        before = self.store.read(receipt_id)
        with patch("wind_bridge.storage.statement_reference_checks",
                   side_effect=lambda m, a, r: statement_reference_checks(m, a, r, self.root)):
            page = self.store.page(receipt_id, offset=1, limit=1)
        self.assertEqual(page["response"]["raw"]["Codes"], [])
        self.assertEqual(page["statement_reference_checks"]["matched_cell_count"], 4)
        self.assertEqual(page["statement_reference_checks"]["raw_cell_count"], 4)
        self.assertEqual(self.store.read(receipt_id), before)

    def test_live_service_difference_remains_a_successful_raw_query(self):
        raw = copy.deepcopy(self.raw)
        raw["Data"][1][0] = 1
        receipt = self.store.save("wss", self.arguments, {"ok": True, "raw": raw})
        receipt["from_cache"] = False
        with patch("wind_bridge.service.statement_reference_checks",
                   side_effect=lambda m, a, r: statement_reference_checks(m, a, r, self.root)):
            service = Service(store=self.store)
            with patch.object(service.backend, "request", return_value=receipt):
                result = service.query("wss", self.arguments)
        self.assertTrue(result["ok"])
        self.assertFalse(result["statement_reference_checks"]["all_covered_cells_match"])
        self.assertEqual(result["raw"]["Data"][1], [1])
        self.assertFalse(result["validation_scope"]["units"])
        with self.store.connect() as con:
            status, detail = con.execute("SELECT status,detail_json FROM validations WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone()
        self.assertEqual(status, "passed")
        self.assertEqual(json.loads(detail)["statement_reference_checks"]["matched_cell_count"], 3)

    def test_field_search_exposes_scoped_evidence_separately_from_ui_definition(self):
        catalog = FieldCatalog(self.store, self.root)
        row = catalog.search("净利润", method="wss")["fields"][0]
        self.assertEqual(row["field"], "net_profit_is")
        self.assertEqual(row["verified_statement_sample_count"], 4)
        self.assertTrue(all(cell["native_receipt_verified"] for cell in row["statement_value_evidence"]))
        self.assertIsNone(row["definition"])
        self.assertIsNone(row["unit"])
        self.assertFalse(row["parameter_semantics_certified"])
        attributed = catalog.search("归母净利润")["fields"][0]
        self.assertEqual(attributed["field"], "np_belongto_parcomsh")
        self.assertEqual(attributed["statement_report_types_with_samples"], ["1"])
        self.assertIn("归母净利润", attributed["statement_aliases"])
        self.assertFalse(attributed["official_definition_observed"])
        self.assertEqual(catalog.search("资产总计")["fields"][0]["field"], "tot_assets")
        self.assertEqual(catalog.search("净利润", method="wsd")["fields"], [])

    def test_total_equity_and_attributable_profit_do_not_lose_scope(self):
        catalog = FieldCatalog(self.store, self.root)
        equity = catalog.search('股东权益合计', method='wss')['fields'][0]
        self.assertEqual(equity['field'], 'tot_equity')
        self.assertEqual({cell['reference']['not_equivalent_to'] for cell in equity['statement_value_evidence']
                          if cell['reference']['rpt_type'] == '1'}, {'归属于母公司所有者权益合计'})
        self.assertEqual(equity['statement_report_types_with_samples'], ['1', '2'])
        self.assertTrue(any(cell['wind_value'] < 0 for cell in catalog.search('财务费用')['fields'][0]['statement_value_evidence']))

    def test_incompatible_reference_scope_or_line_is_rejected(self):
        row = next(row for row in self.reference['rows'] if row['field'] == 'np_belongto_parcomsh')
        row['rpt_type'] = '2'
        self.save_reference()
        self.assertEqual(self.compare()['status'], 'reference_unavailable')
        row['rpt_type'] = '1'
        row['statement_line'] = '净利润'
        self.save_reference()
        self.assertEqual(self.compare()['status'], 'reference_unavailable')

    def test_alias_discovery_requires_at_least_one_verified_statement_sample(self):
        for sample in self.reference['native_samples']:
            if 'np_belongto_parcomsh' in sample['arguments']['fields']:
                self.store.validation(sample['receipt_id'], 'failed', {})
        catalog = FieldCatalog(self.store, self.root)
        self.assertEqual(catalog.search('归母净利润')['fields'], [])
        raw_field = catalog.search('np_belongto_parcomsh')['fields'][0]
        self.assertEqual(raw_field['statement_aliases'], [])
        self.assertFalse(raw_field['successful_sample_available'])

    def test_changed_native_fingerprint_downgrades_only_affected_sample(self):
        receipt = self.receipts[0]
        path = self.store.receipts / (receipt["receipt_id"] + ".json.gz")
        changed = copy.deepcopy(receipt)
        changed["response"]["raw"]["Data"][0][0] = 1
        with gzip.open(path, "wt") as file:
            json.dump(changed, file)
        fields, status = StatementEvidence(self.root).catalog(self.store)
        self.assertEqual(len(fields["oper_rev"]), 3)
        self.assertEqual(status["sample_issues"][0]["issue"], "STATEMENT_SAMPLE_CHANGED")

    def test_failed_validation_or_missing_receipt_is_not_native_evidence(self):
        receipt = self.receipts[0]
        self.store.validation(receipt["receipt_id"], "failed", {})
        fields, status = StatementEvidence(self.root).catalog(self.store)
        self.assertEqual(len(fields["oper_rev"]), 3)
        self.assertEqual(status["sample_issues"][0]["issue"], "UNVALIDATED_INPUT_RECEIPT")
        (self.store.receipts / (receipt["receipt_id"] + ".json.gz")).unlink()
        self.assertEqual(StatementEvidence(self.root).catalog(self.store)[1]["sample_issues"][0]["issue"], "NO_RECEIPT")

    def test_sample_request_mismatch_and_reference_difference_not_certified(self):
        self.reference["native_samples"][0]["arguments"]["options"] = "rptDate=20251231;rptType=2;unit=1"
        self.save_reference()
        fields, status = StatementEvidence(self.root).catalog(self.store)
        self.assertEqual(len(fields["net_profit_is"]), 3)
        self.assertEqual(status["sample_issues"][0]["issue"], "STATEMENT_REQUEST_CHANGED")
        self.reference["rows"][8]["value_decimal"] = "0"  # A parent-company reference cell.
        self.save_reference()
        fields, _ = StatementEvidence(self.root).catalog(self.store)
        self.assertEqual(sum(cell["matches_reference"] for cell in fields["oper_rev"]), 2)


if __name__ == "__main__":
    unittest.main()
