import unittest

from wind_bridge.codegen import parse_query
from wind_bridge.common import Problem


class CodegenTests(unittest.TestCase):
    def test_codegen_positional_and_keyword_inputs_map_to_sdk_arguments(self):
        result = parse_query('data = w.edb("S0117164", "2026-09-21", "2026-09-28", options="");')
        self.assertEqual(result["method"], "edb")
        self.assertEqual(result["arguments"]["codes"], "S0117164")
        self.assertEqual(result["arguments"]["endTime"], "2026-09-28")
        self.assertFalse(result["executed"])
        self.assertFalse(result["field_semantics_verified"])

    def test_literal_lists_are_supported(self):
        result = parse_query('w.wss(["600519.SH", "510300.SH"], ["sec_name", "close"], "tradeDate=20260928")')
        self.assertEqual(result["arguments"]["codes"], "600519.SH,510300.SH")

    def test_calls_expressions_mutations_and_subscriptions_are_rejected(self):
        for code in ['w.wss(open("secret").read(), "close")', 'w.wss("600519.SH", "close", **params)',
                     'w.wss("600519.SH", "close"); print("ran")', 'x.y = w.wss("600519.SH", "close")',
                     'w.wupf("anything")', 'w.wsq("600519.SH", "rt_last", func="callback")',
                     'w.wss("600519.SH", fields="close", codes="x")', 'w.wss("600519.SH", "close", usedf=True)']:
            with self.assertRaises(Problem, msg=code):
                parse_query(code)


if __name__ == "__main__":
    unittest.main()
