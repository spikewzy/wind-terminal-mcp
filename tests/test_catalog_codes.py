import tempfile
from pathlib import Path
import unittest

from wind_bridge.catalog import Catalog
from wind_bridge.common import Problem
from wind_bridge.storage import Store


class CatalogCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.rows = [
            {"code": "S5708175", "name": "(停止)中国:高炉开工率(163家)", "source": "Wind", "freq": "周", "unit": "%"},
            {"code": "S5100860", "name": "中国:产量:多晶硅", "source": "Wind", "freq": "年", "unit": "吨"},
            {"code": "M0325687", "name": "中国:国债收益率:10年", "source": "中国货币网", "freq": "日", "unit": "%"},
            {"code": "H6924748", "name": "中国:社会库存量:螺纹钢", "source": "Wind", "freq": "周", "unit": "万吨"},
            {"code": "K4569350", "name": "中国:社会库存量:螺纹钢", "source": "根据新闻整理", "freq": "周", "unit": "万吨"},
        ]
        for row in self.rows:
            self.store.put_indicator(row, {"metadata_source_id": "test_fixture", "evidence": "Metadata fixture; no entitlement"})
        self.catalog = Catalog(self.store)

    def test_known_identifier_returns_only_exact_code(self):
        for text in ["S5100860", "  s5100860  ", "Ｓ５１００８６０"]:
            with self.subTest(text=text):
                result = self.catalog.search(text)
                self.assertEqual([r["code"] for r in result["metrics"]], ["S5100860"])
                self.assertEqual(result["match_mode"], "exact_code")
                self.assertEqual(result["requested_code"], "S5100860")
                self.assertEqual(result["metrics"][0]["windpy_access"], "not_implied_by_catalog")

    def test_missing_identifier_does_not_match_digits_in_names(self):
        before = self.store.catalog()
        result = self.catalog.search("S5716165")
        self.assertEqual(result["metrics"], [])
        self.assertEqual(result["total_matches"], 0)
        self.assertIsNone(result["next_offset"])
        self.assertEqual(result["match_mode"], "exact_code")
        self.assertEqual(self.store.catalog(), before)
        self.assertEqual(list(self.store.receipts.iterdir()), [])

    def test_exact_identifier_still_obeys_filters_and_pagination(self):
        self.assertEqual(self.catalog.search("S5100860", filters={"freq": "周"})["metrics"], [])
        matched = self.catalog.search("S5100860", filters={"source": "Wind", "freq": "年"}, limit=1)
        self.assertEqual(matched["total_matches"], 1)
        paged = self.catalog.search("S5100860", offset=1)
        self.assertEqual(paged["metrics"], [])
        self.assertEqual(paged["total_matches"], 1)
        self.assertIsNone(paged["next_offset"])

    def test_same_name_different_codes_remain_separate_candidates(self):
        result = self.catalog.search("螺纹钢 社会库存")
        self.assertEqual(result["match_mode"], "lexical_name")
        self.assertIsNone(result["requested_code"])
        self.assertEqual({r["code"] for r in result["metrics"]}, {"H6924748", "K4569350"})
        self.assertEqual({r["source"] for r in result["metrics"]}, {"Wind", "根据新闻整理"})
        with self.assertRaises(Problem) as error:
            self.catalog.resolve("中国:社会库存量:螺纹钢")
        self.assertEqual(error.exception.code, "INDICATOR_SELECTION_REQUIRED")
        self.assertEqual(self.catalog.resolve("S5716165"), ["S5716165"])


if __name__ == "__main__":
    unittest.main()
