"""Run all existing and relay unit tests with real WindPy imports forbidden."""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.abc
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent


class NoRealWindPy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "WindPy" or fullname.startswith("WindPy."):
            raise RuntimeError("Real WindPy is forbidden during offline tests")


def main():
    sys.meta_path.insert(0, NoRealWindPy())
    started = time.monotonic()
    destination = ROOT / "verification"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wind-relay-offline-") as directory:
        os.environ["WIND_TERMINAL_MCP_DATA_DIR"] = directory
        with (destination / "relay-offline-suite.txt").open("w", encoding="utf-8") as log:
            with contextlib.redirect_stderr(log), contextlib.redirect_stdout(log):
                suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    report = {"ok": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "seconds": round(time.monotonic() - started, 3),
              "WindPy_imported": "WindPy" in sys.modules, "real_wind_queries": 0,
              "cloud_or_uos_deployed": False,
              "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    (destination / "relay-offline-suite.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
