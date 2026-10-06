from pathlib import Path
import tempfile
import unittest

from wind_bridge.bundle_metadata import search_bundle
from wind_bridge.common import Problem


class PublicReferenceTests(unittest.TestCase):
    def test_missing_bundle_returns_actionable_gap_before_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Problem) as caught:
                search_bundle("库存", path=Path(directory) / "absent.json")
        self.assertEqual(caught.exception.code, "LOCAL_REFERENCE_NOT_INSTALLED")
        self.assertFalse(caught.exception.details["reference_available"])
        self.assertEqual(caught.exception.details["setup_reference"], "docs/LOCAL_REFERENCES.md")
        self.assertFalse(caught.exception.details["executed"])
