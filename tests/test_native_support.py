import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wind_bridge.common import Problem
from wind_bridge.backend import Backend
from wind_bridge.native_support import inspect_native_support, require_native_method
from wind_bridge.storage import Store
from wind_bridge.native_lock import native_lock


class NativeSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = self.root / "WindPy.py"
        self.library = self.root / "library.dylib"
        self.evidence = self.root / "evidence.json"
        self.module.write_bytes(b"module fixture")
        self.library.write_bytes(b"library fixture")
        record = {"platform": "Darwin", "architecture": "arm64", "app_version": "fixture",
                  "app_build": "fixture", "library_path": str(self.library),
                  "module_sha256": hashlib.sha256(self.module.read_bytes()).hexdigest(),
                  "library_sha256": hashlib.sha256(self.library.read_bytes()).hexdigest(),
                  "methods": {"wnd": {}, "wnq": {}, "wnc": {}}}
        self.evidence.write_text(json.dumps(record), encoding="utf-8")
        for name, value in [("EVIDENCE", self.evidence), ("MODULE_DIR", self.root)]:
            mock = patch("wind_bridge.native_support." + name, value)
            mock.start()
            self.addCleanup(mock.stop)
        for name, value in [("system", "Darwin"), ("machine", "arm64")]:
            mock = patch("wind_bridge.native_support.platform." + name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_only_matching_sdk_news_methods_are_declared_unavailable(self):
        support = inspect_native_support()
        self.assertTrue(support["verified_build_match"])
        self.assertEqual(support["unavailable_methods"], ["wnc", "wnd", "wnq"])
        with self.assertRaises(Problem) as raised:
            require_native_method("wnd")
        self.assertEqual(raised.exception.code, "WIND_SDK_METHOD_UNAVAILABLE")
        self.assertFalse(raised.exception.details["authentication_checked"])
        require_native_method("wsd")

    def test_sdk_update_invalidates_old_unavailability_evidence(self):
        self.assertTrue(inspect_native_support()["verified_build_match"])
        self.library.write_bytes(b"updated official library fixture")
        support = inspect_native_support()
        self.assertFalse(support["verified_build_match"])
        self.assertEqual(support["reason"], "sdk_fingerprint_changed")
        self.assertEqual(support["unavailable_methods"], [])
        require_native_method("wnd")

    def test_different_module_or_architecture_is_not_blocked(self):
        with patch("wind_bridge.native_support.platform.machine", return_value="x86_64"):
            self.assertEqual(inspect_native_support()["reason"], "different_platform_or_architecture")
            require_native_method("wnq")
        self.module.write_bytes(b"another WindPy module fixture")
        self.assertEqual(inspect_native_support()["reason"], "sdk_fingerprint_changed")
        require_native_method("wnq")

    def test_missing_evidence_does_not_invent_a_platform_limitation(self):
        self.evidence.unlink()
        support = inspect_native_support()
        self.assertFalse(support["verified_build_match"])
        self.assertEqual(support["unavailable_methods"], [])
        require_native_method("wnc")

    def test_unavailable_method_is_reported_even_when_native_session_is_busy(self):
        store = Store(self.root / "runtime")
        with native_lock(store.directory / "native.lock"):
            with self.assertRaises(Problem) as raised:
                Backend(store).request("wnq", {"codes": "600519.SH"})
            self.assertEqual(raised.exception.code, "WIND_SDK_METHOD_UNAVAILABLE")
            self.assertFalse(raised.exception.details["detail"]["authentication_checked"])


if __name__ == "__main__":
    unittest.main()
