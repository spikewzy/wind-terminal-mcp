import copy
import unittest

from wind_bridge.table_dates import observe_table_dates


class TableDateTests(unittest.TestCase):
    def test_request_row_dates_and_table_metadata_are_distinct(self):
        raw = {"Fields": ["date", "i_weight"], "Data": [["2026-09-01T00:00:00"] * 2, [1.0, 99.0]], "Times": ["2026-09-30"]}
        before = copy.deepcopy(raw)
        result = observe_table_dates(raw, "date=20260929;windcode=000300.SH")
        self.assertEqual(result["unambiguous_requested_date"], "2026-09-29")
        self.assertEqual(result["raw_metadata_times"], ["2026-09-30"])
        self.assertEqual(result["date_columns"][0]["different_from_requested_date_count"], 2)
        self.assertFalse(result["date_columns"][0]["all_rows_equal_requested_date"])
        self.assertFalse(result["requested_date_coverage_certified"])
        self.assertEqual(raw, before)

    def test_unknown_or_duplicate_date_options_are_not_guessed(self):
        raw = {"Fields": ["date"], "Data": [["20260831"]]}
        for options in ["", "date=ED-1D", "date=20260831;DATE=20260929"]:
            result = observe_table_dates(raw, options)
            self.assertIsNone(result["unambiguous_requested_date"])
            self.assertIsNone(result["date_columns"][0]["all_rows_equal_requested_date"])

    def test_null_invalid_and_empty_date_columns_cannot_pass_vacuously(self):
        raw = {"Fields": ["date", "tradedate"], "Data": [[None, " ", "2026-02-30"], []]}
        result = observe_table_dates(raw, "date=20260831")
        self.assertEqual(result["date_columns"][0]["missing_count"], 2)
        self.assertEqual(result["date_columns"][0]["unparsed_count"], 1)
        self.assertTrue(all(row["all_rows_equal_requested_date"] is None for row in result["date_columns"]))

    def test_matching_dates_do_not_certify_effective_or_publication_dates(self):
        raw = {"Fields": ["date", "sec_name"], "Data": [["20260831", "2026-08-31T00:00:00"], ["A", "B"]]}
        result = observe_table_dates(raw, "date=2026-08-31")
        self.assertTrue(result["date_columns"][0]["all_rows_equal_requested_date"])
        self.assertFalse(result["date_column_semantics_certified"])
        result = observe_table_dates({"Fields": ["maturitydate"], "Data": [["2026-08-31"]]}, "date=20260831")
        self.assertEqual(result["date_columns"], [])
