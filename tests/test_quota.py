import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.quota import local_quota_observation, quota_details
from wind_bridge.service import Service
from wind_bridge.storage import Store


ERROR = {"ErrorCode": -40522017, "Codes": ["ErrorReport"], "Fields": ["OUTMESSAGE"],
         "Times": ["2026-09-30"], "Data": [["CWSDService: quota exceeded."]]}


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []

    def runner(self, method, args):
        self.calls.append((method, args))
        return {"ok": True, "raw": json.loads(json.dumps(ERROR))}

    def test_quota_error_preserves_compatibility_raw_and_unknown_reset(self):
        backend = Backend(self.store, self.runner)
        with self.assertRaises(Problem) as raised:
            backend.request("wsd", {"codes": "000852.SH", "fields": "close", "beginTime": "2026-09-21", "endTime": "2026-09-29"})
        error = raised.exception
        self.assertEqual(error.code, "WIND_UPSTREAM_ERROR")
        self.assertEqual(error.details["error_category"], "quota_exceeded")
        self.assertFalse(error.details["automatic_retry_recommended"])
        self.assertIsNone(error.details["quota"]["reset_at"])
        self.assertIsNone(error.details["quota"]["quota_scope"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.read(error.details["receipt_id"])["response"]["raw"], ERROR)

    def test_local_status_never_queries_wind_or_asserts_current_quota(self):
        receipt = self.store.save("wsd", {}, {"ok": True, "raw": ERROR})
        result = Backend(self.store, self.runner).status(False)
        observed = result["quota_observation"]
        self.assertEqual(self.calls, [])
        self.assertIsNone(result["connected"])
        self.assertFalse(observed["live_quota_checked"])
        self.assertIsNone(observed["current_quota_available"])
        self.assertEqual(observed["last_observed_error"]["receipt_id"], receipt["receipt_id"])
        self.assertTrue(observed["last_observed_error"]["historical_error_not_current_quota_measurement"])

    def test_no_error_or_newer_success_does_not_certify_current_quota(self):
        self.assertIsNone(local_quota_observation(self.store)["last_observed_error"])
        self.assertIsNone(local_quota_observation(self.store)["current_quota_available"])
        old = self.store.save("wsd", {}, {"ok": True, "raw": ERROR})
        successful = self.store.save("wsd", {}, {"ok": True, "raw": {"ErrorCode": 0, "Data": [[1]]}})
        observed = local_quota_observation(self.store)
        self.assertEqual(observed["last_observed_error"]["receipt_id"], old["receipt_id"])
        self.assertIsNone(observed["current_remaining_quota"])
        self.assertEqual(observed["later_same_request_success"]["receipt_id"], successful["receipt_id"])
        self.assertTrue(observed["later_same_request_success"]["identical_request"])
        self.assertIsNone(observed["current_quota_available"])

    def test_success_for_different_request_does_not_clear_failure(self):
        self.store.save("wsd", {"codes": "A"}, {"ok": True, "raw": ERROR})
        self.store.save("wsd", {"codes": "B"}, {"ok": True, "raw": {"ErrorCode": 0}})
        self.store.save("wss", {"codes": "A"}, {"ok": True, "raw": {"ErrorCode": 0}})
        self.assertIsNone(local_quota_observation(self.store)["later_same_request_success"])

    def test_success_receipt_is_read_and_validation_state_preserved(self):
        self.store.save("wsd", {}, {"ok": True, "raw": ERROR})
        good = self.store.save("wsd", {}, {"ok": True, "raw": {"ErrorCode": 0}})
        self.store.validation(good["receipt_id"], "failed", {"code": "SHAPE_MISMATCH"})
        missing = self.store.save("wsd", {}, {"ok": True, "raw": {"ErrorCode": 0}})
        (self.store.receipts / (missing["receipt_id"] + '.json.gz')).unlink()
        observed = local_quota_observation(self.store)
        self.assertEqual(observed["later_same_request_success"]["receipt_id"], good["receipt_id"])
        self.assertEqual(observed["later_same_request_success"]["data_validation_status"], "failed")
        self.assertEqual(observed["unreadable_receipts"][0]["receipt_id"], missing["receipt_id"])

    def test_documented_window_does_not_invent_account_limit_or_reset(self):
        result = Backend(self.store, self.runner).status(False)
        observed = result["quota_observation"]
        policy = observed["documented_general_policy"]
        self.assertEqual(policy["window_type"], "rolling")
        self.assertEqual(policy["window_hours"], 168)
        self.assertFalse(policy["fixed_calendar_reset"])
        self.assertFalse(policy["recovery_time_computable"])
        self.assertFalse(policy["account_specific_values_verified"])
        for key in ("function_limits", "account_usage", "billable_usage_unit", "quota_group_mapping"):
            self.assertIsNone(policy[key])
        self.assertIsNone(observed["current_reset_at"])
        self.assertIsNone(observed["current_remaining_quota"])
        self.assertEqual(self.calls, [])

    def test_other_errors_and_code_alone_not_mislabeled(self):
        for raw in [{"ErrorCode": -40522017, "Data": [[1]]},
                    {"ErrorCode": -40521007, "Data": [["权限验证不通过"]]},
                    {"ErrorCode": -1, "Data": None},
                    {**ERROR, "ErrorCode": 0}]:
            self.assertIsNone(quota_details(raw, "wsd"))

    def test_corrupt_or_missing_receipt_is_reported_without_native_call(self):
        old = self.store.save("wsd", {}, {"ok": True, "raw": ERROR})
        recent = self.store.save("wss", {}, {"ok": False})
        (self.store.receipts / (recent["receipt_id"] + '.json.gz')).unlink()
        observed = local_quota_observation(self.store)
        self.assertEqual(observed["last_observed_error"]["receipt_id"], old["receipt_id"])
        self.assertEqual(observed["unreadable_receipts"][0]["receipt_id"], recent["receipt_id"])

    def test_batch_stops_and_preserves_completed_series_on_quota(self):
        def runner(method, args):
            self.calls.append((method, args))
            raw = {"ErrorCode": 0, "Codes": [args["codes"]], "Fields": ["CLOSE"],
                   "Times": ["2026-09-29"], "Data": [[1]]}
            if len(self.calls) == 2:
                raw = {**ERROR, "Data": [["EDB: quota exceeded."]]}
            return {"ok": True, "raw": raw}
        service = Service(self.store, Backend(self.store, runner))
        with self.assertRaises(Problem) as raised:
            service.economic("M0000001,M0000002,M0000003", "2026-09-29", "2026-09-29")
        detail = raised.exception.details
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(detail["error_category"], "quota_exceeded")
        self.assertEqual(detail["completed_metrics"][0]["value"], [1])
        self.assertEqual(detail["stopped_before_codes"], ["M0000003"])
