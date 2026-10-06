from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from wind_bridge.analytics import raw_receipt
from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.edb_observations import observe_edb_series
from wind_bridge.service import Service
from wind_bridge.storage import Store


class EdbObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.arguments = {"codes": "M123456,M123457,M123458", "beginTime": "2026-01-01",
                          "endTime": "2026-09-30", "options": ""}
        self.raw = {"ErrorCode": 0, "Codes": ["M123456", "M123457", "M123458"], "Fields": ["close"],
                    "Times": ["2024-12-31", "2026-01-02", "2026-09-30"],
                    "Data": [[None, 0, 5], [70.1, None, None], [None, None, None]]}
        self.calls = []
        self.service = Service(self.store, Backend(self.store, self.fake))

    def fake(self, method, arguments):
        self.calls.append((method, arguments))
        return {"ok": True, "raw": deepcopy(self.raw)}

    def test_shared_axis_does_not_attribute_stale_value_or_nulls_to_other_codes(self):
        original = deepcopy(self.raw)
        observed = observe_edb_series(self.raw, self.arguments)
        self.assertEqual(observed["populated_out_of_range_codes"], ["M123457"])
        self.assertEqual(observed["out_of_range_dates_sample"], ["2024-12-31"])
        a, b, c = observed["series"]
        self.assertEqual(a["status"], "values_only_inside_requested_interval")
        self.assertEqual(a["in_range"]["non_null_count"], 2)
        self.assertEqual(a["in_range"]["zero_count"], 1)
        self.assertEqual(a["out_of_range"]["null_count"], 1)
        self.assertEqual(b["status"], "values_only_outside_requested_interval")
        self.assertEqual(b["out_of_range"]["first_non_null_date"], "2024-12-31")
        self.assertEqual(c["status"], "no_non_null_values_in_returned_matrix")
        self.assertEqual(c["in_range"]["null_count"], 2)
        self.assertFalse(observed["frequency_inferred"])
        self.assertFalse(observed["numeric_data_selected"])
        self.assertEqual(self.raw, original)

    def test_failed_query_explains_each_code_without_importing_or_approving_values(self):
        with self.assertRaises(Problem) as raised:
            self.service.query("edb", self.arguments)
        problem = raised.exception
        self.assertEqual(problem.code, "OUT_OF_RANGE_DATA")
        observed = problem.details["edb_series_observations"]
        self.assertEqual(observed["populated_out_of_range_codes"], ["M123457"])
        receipt = self.store.read(problem.details["receipt_id"])
        self.assertEqual(receipt["response"]["raw"], self.raw)
        with self.store.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM validations WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone()[0], "failed")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
        with self.assertRaises(Problem) as invalid:
            raw_receipt(self.store, receipt["receipt_id"], {"edb"})
        self.assertEqual(invalid.exception.code, "UNVALIDATED_INPUT_RECEIPT")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.catalog(), [])

    def test_receipt_page_keeps_full_matrix_diagnostics_and_failed_validation(self):
        with self.assertRaises(Problem) as raised:
            self.service.query("edb", self.arguments)
        receipt_id = raised.exception.details["receipt_id"]
        before = self.store.read(receipt_id)
        page = self.store.page(receipt_id, offset=1, limit=1)
        self.assertEqual(page["response"]["raw"]["Times"], ["2026-01-02"])
        self.assertEqual(page["response"]["raw"]["Data"], [[0], [None], [None]])
        self.assertEqual(page["edb_series_observations"], raised.exception.details["edb_series_observations"])
        self.assertEqual(page["edb_series_observations"]["returned_date_count"], 3)
        self.assertEqual(page["stored_query_validation"]["status"], "failed")
        self.assertEqual(page["stored_query_validation"]["detail"]["code"], "OUT_OF_RANGE_DATA")
        self.assertEqual(self.store.read(receipt_id), before)
        self.assertEqual(len(self.calls), 1)

    def test_malformed_layouts_do_not_silently_truncate_or_assign_columns(self):
        for changed, status in [
            ({"Codes": ["M123457", "M123456", "M123458"]}, "code_mapping_unverified"),
            ({"Codes": ["M123456"] * 3}, "code_mapping_unverified"),
            ({"Fields": ["close", "other"]}, "field_layout_unverified"),
            ({"Fields": ["other"]}, "field_layout_unverified"),
            ({"Data": [[1]] * 3}, "matrix_shape_unverified"),
            ({"Data": [None] * 3}, "matrix_shape_unverified"),
            ({"Data": self.raw["Data"][:2]}, "matrix_shape_unverified"),
            ({"ErrorCode": -40521007}, "upstream_not_successful"),
        ]:
            with self.subTest(status=status, changed=changed):
                result = observe_edb_series({**self.raw, **changed}, self.arguments)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["series"], [])
                self.assertFalse(result["dates_compared"])

    def test_invalid_dates_and_bounds_do_not_produce_guessed_intervals(self):
        for dates in [["not-a-date", "2026-01-02", "2026-09-30"],
                      ["2026-01-02", "2026-01-02", "2026-09-30"],
                      ["2026-01-02", "2024-12-31", "2026-09-30"]]:
            with self.subTest(dates=dates):
                result = observe_edb_series({**self.raw, "Times": dates}, self.arguments)
                self.assertEqual(result["status"], "returned_dates_unverified")
                self.assertEqual(result["series"], [])
        result = observe_edb_series(self.raw, {**self.arguments, "beginTime": "ED-1Y"})
        self.assertEqual(result["status"], "request_date_bounds_unverified")
        self.assertFalse(result["dates_compared"])

    def test_empty_series_and_non_numeric_values_are_not_successful_numeric_coverage(self):
        observed = observe_edb_series({**self.raw, "Times": [], "Data": [[], [], []]}, self.arguments)
        self.assertIsNone(observed["all_returned_dates_within_bounds"])
        self.assertTrue(all(row["status"] == "no_non_null_values_in_returned_matrix" for row in observed["series"]))
        raw = {**self.raw, "Data": [[None, "0", True], [70.1, float("inf"), None], [None, 10 ** 400, None]]}
        observed = observe_edb_series(raw, self.arguments)
        self.assertEqual(observed["series"][0]["in_range"]["numeric_count"], 0)
        self.assertEqual(observed["series"][0]["in_range"]["non_numeric_non_null_count"], 2)
        self.assertEqual(observed["series"][0]["in_range"]["zero_count"], 0)
        self.assertEqual(observed["series"][1]["in_range"]["numeric_count"], 0)
        self.assertEqual(observed["series"][2]["in_range"]["numeric_count"], 1)

    def test_valid_query_keeps_sparse_data_and_returns_diagnostics(self):
        self.raw["Times"] = self.raw["Times"][1:]
        self.raw["Data"] = [column[1:] for column in self.raw["Data"]]
        result = self.service.query("edb", self.arguments)
        self.assertTrue(result["edb_series_observations"]["all_returned_dates_within_bounds"])
        self.assertEqual(result["raw"], self.raw)
        page = self.store.page(result["receipt_id"])
        self.assertEqual(page["stored_query_validation"]["status"], "passed")
        self.assertEqual(page["edb_series_observations"], result["edb_series_observations"])


if __name__ == "__main__":
    unittest.main()
