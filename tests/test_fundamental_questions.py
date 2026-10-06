import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from wind_bridge.backend import Backend
from wind_bridge.common import Problem
from wind_bridge.compat import execute
from wind_bridge.fundamental_questions import execute_fundamentals, mapping_evidence, parse_intent, plan_fundamentals
from wind_bridge.service import Service
from wind_bridge.storage import Store


BASE = "查询贵州茅台2025-12-31合并报表的营业收入、净利润、资产总计和负债合计，单位元"


class FundamentalQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.calls = []
        self.entities_override = None
        self.fail_on_date = None
        self.missing_value = False
        self.service = Service(self.store, Backend(self.store, self.fake))
        self.evidence_patch = patch('wind_bridge.fundamental_questions.mapping_evidence', return_value={"scope": "unit_test_fixture_only"})
        self.evidence_patch.start()
        self.addCleanup(self.evidence_patch.stop)

    def fake(self, method, args):
        self.calls.append((method, args))
        if method == 'wai':
            text = args['input']
            profiles = {
                '贵州茅台': [('600519.SH', 'stockCN')], '五粮液': [('000858.SZ', 'stockCN')],
                '中国平安': [('601318.SH', 'stockCN'), ('2318.HK', 'stockHK'), ('PNGAY.OO', 'stockUS')],
                '腾讯控股': [('0700.HK', 'stockHK')],
                '沪深300': [('000300.SH', 'indicator')],
                '600519.SH': [('600519.SH', 'stockCN')], '601318.SH': [('601318.SH', 'stockCN')],
                '2318.HK': [('2318.HK', 'stockHK')], '200001.SZ': [('200001.SZ', 'stockCN')],
                '营业收入': [('84952', 'stockBondIndex')], '净利润': [('84959', 'stockBondIndex')],
            }
            entities = []
            for text_name, choices in profiles.items():
                for match in re.finditer(re.escape(text_name), text, re.I):
                    entities.append({'entity': match.group(), 'id': choices[0][0], 'type': choices[0][1],
                        'startIndex': match.start(), 'endIndex': match.end() - 1,
                        'candidateEntities': [{'entity': text_name, 'fullName': text_name, 'id': code, 'type': kind,
                                               'matchConfidence': 1 if i == 0 else 0} for i, (code, kind) in enumerate(choices)]})
            if self.entities_override is not None:
                entities = self.entities_override
            body = {'status': '0', 'body': {'status_code': 200, 'succeed': True, 'data': [entities]}}
            raw = {'ErrorCode': 0, 'Codes': ['fer'], 'Fields': ['details'], 'Times': ['2026-09-30'],
                   'Data': [[json.dumps(body, ensure_ascii=False)]]}
        else:
            self.assertEqual(method, 'wss')
            fields, codes = args['fields'].split(','), args['codes'].split(',')
            data = [[100 * (i + 1) + j for j in range(len(codes))] for i in range(len(fields))]
            if self.missing_value:
                data[0][0] = None
            raw = {'ErrorCode': -40522017 if self.fail_on_date and self.fail_on_date in args['options'] else 0,
                   'Codes': codes, 'Fields': [field.upper() for field in fields], 'Times': ['2026-09-30'], 'Data': data}
        return {'ok': True, 'raw': raw}

    def test_complete_question_runs_wai_then_wss_and_keeps_raw_and_plan(self):
        result = execute(self.service, 'stock_data', 'get_stock_fundamentals', {'question': BASE})
        self.assertEqual([method for method, _ in self.calls], ['wai', 'wss'])
        request = self.calls[1][1]
        self.assertEqual(request['codes'], '600519.SH')
        self.assertEqual(request['fields'], 'oper_rev,net_profit_is,tot_assets,tot_liab')
        self.assertEqual(request['options'], 'rptDate=20251231;rptType=1;unit=1')
        self.assertEqual(result['raw_value_count'], 4)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['alice_contract']['params']['question'], BASE)
        self.assertFalse(result['semantic_equivalence_verified'])
        self.assertFalse(result['unit_conversion_applied'])
        self.assertEqual(result['rows'][0]['report_date_requested'], '2025-12-31')
        self.assertIsNone(result['rows'][0]['unit'])
        saved = self.store.read(result['derived_receipt_id'])
        plan = self.store.read(result['plan_receipt_id'])
        self.assertEqual(saved['response']['derived']['rows'], result['rows'])
        self.assertEqual(plan['sha256'], result['plan_sha256'])
        self.assertEqual(plan['response']['derived']['question'], BASE)
        self.assertNotIn('84952', request['fields'])

    def test_plan_only_does_not_query_values(self):
        result = plan_fundamentals(self.service, BASE)
        self.assertEqual([method for method, _ in self.calls], ['wai'])
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['value_query_executed'])
        self.assertEqual(result['queries'][0]['arguments']['fields'], 'oper_rev,net_profit_is,tot_assets,tot_liab')
        self.assertEqual(self.store.read(result['recognition_receipt_id'])['sha256'], result['recognition_sha256'])

    def test_multiple_companies_dates_and_report_types_are_explicit_combinations(self):
        question = '比较贵州茅台和五粮液2024-12-31与2025-12-31的合并报表和母公司报表营业收入、净利润，单位人民币元'
        result = execute_fundamentals(self.service, question)
        self.assertEqual([q['arguments']['codes'] for q in result['plan']['queries']], ['600519.SH,000858.SZ'] * 4)
        self.assertEqual([(q['report_date_requested'], q['report_type_requested']) for q in result['queries']],
                         [('2024-12-31', '1'), ('2024-12-31', '2'), ('2025-12-31', '1'), ('2025-12-31', '2')])
        self.assertEqual(result['raw_value_count'], 16)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(result['rows'][0]['value'], 100)
        self.assertEqual(result['rows'][2]['value'], 101)

    def test_annual_report_and_absolute_date_spellings(self):
        for text in ['2025年报', '2025年度', '2025年年度', '2025年12月31日', '20251231', '2025/12/31']:
            with self.subTest(text=text):
                intent = parse_intent(f'贵州茅台{text}合并营业收入，单位为元')
                self.assertEqual(intent['report_dates'], ['2025-12-31'])
        with self.assertRaises(Problem):
            parse_intent('贵州茅台2025-02-29合并营业收入，单位元')

    def test_aliases_and_fields_deduplicate_without_changing_meaning(self):
        intent = parse_intent('贵州茅台2025年报母公司总资产和资产总计及总负债，单位：元')
        self.assertEqual(intent['fields'], ['tot_assets', 'tot_liab'])
        self.assertEqual(intent['report_types'], ['2'])
        result = execute_fundamentals(self.service, '贵州茅台2025年报母公司总资产和总负债，单位：元')
        self.assertEqual([row['label'] for row in result['rows']], ['资产总计', '负债合计'])

    def test_missing_parameters_fail_before_recognition(self):
        for question, missing in [
            ('贵州茅台合并营业收入，单位元', 'date'),
            ('贵州茅台2025年报营业收入，单位元', 'report_type'),
            ('贵州茅台2025年报合并营业收入', 'unit'),
            ('贵州茅台2025年报合并营收，单位元', 'field')]:
            with self.subTest(question=question), self.assertRaises(Problem) as error:
                execute_fundamentals(self.service, question)
            self.assertEqual(error.exception.code, 'FUNDAMENTAL_PARAMETERS_REQUIRED')
            self.assertIn(missing, error.exception.details['missing'])
        self.assertEqual(self.calls, [])

    def test_unsupported_accounting_and_time_semantics_fail_before_recognition(self):
        for term in ['营业总收入', '归母净资产', '扣非净利润', '净利润同比增长', '净利润TTM',
                     '净利润单季度', '截至当时的营业收入', '最新营业收入', '营业收入，单位万元']:
            with self.subTest(term=term), self.assertRaises(Problem) as error:
                execute_fundamentals(self.service, f'贵州茅台2025年报合并{term}，单位元')
            self.assertEqual(error.exception.code, 'QUERY_PLAN_REQUIRED')
        self.assertEqual(self.calls, [])

    def test_attributable_profit_is_distinct_from_total_net_profit(self):
        question = '贵州茅台2025年报合并报表净利润和归母净利润，单位元'
        result = execute_fundamentals(self.service, question)
        self.assertEqual(self.calls[-1][1]['fields'], 'net_profit_is,np_belongto_parcomsh')
        self.assertEqual([row['label'] for row in result['rows']], ['净利润', '归属于母公司股东的净利润'])
        self.assertNotEqual(result['rows'][0]['value'], result['rows'][1]['value'])

    def test_full_attributable_names_do_not_inject_parent_report_type(self):
        for name in ['归属于母公司股东的净利润', '归属母公司股东的净利润', '归属于上市公司股东的净利润', '归母净利润', 'np_belongto_parcomsh']:
            with self.subTest(name=name):
                intent = parse_intent(f'贵州茅台2025年报合并{name}，单位元')
                self.assertEqual(intent['fields'], ['np_belongto_parcomsh'])
                self.assertEqual(intent['report_types'], ['1'])

    def test_parent_statement_cannot_receive_attributable_profit_mapping(self):
        for scope in ['母公司报表', '合并报表和母公司报表']:
            with self.subTest(scope=scope), self.assertRaises(Problem) as error:
                execute_fundamentals(self.service, f'贵州茅台2025年报{scope}归母净利润，单位元')
            self.assertEqual(error.exception.code, 'QUERY_PLAN_REQUIRED')
            self.assertEqual(error.exception.details['incompatible_statement_mappings'][0]['requested_report_type'], '2')
        self.assertEqual(self.calls, [])

    def test_attributable_profit_explicitly_records_implied_consolidated_scope(self):
        result = execute_fundamentals(self.service, '贵州茅台2025年报净利润和归母净利润，单位元')
        intent = result['plan']['intent']
        self.assertEqual(intent['report_types'], ['1'])
        self.assertEqual(intent['statement_scope_inference']['fields'], ['np_belongto_parcomsh'])
        self.assertEqual(self.calls[-1][1]['options'], 'rptDate=20251231;rptType=1;unit=1')

    def test_single_available_report_type_alone_does_not_justify_inference(self):
        with patch.dict('wind_bridge.fundamental_questions.FIELDS', {'opprofit': {'label': '营业利润', 'aliases': ['营业利润'], 'report_types': ['1']}}):
            with self.assertRaises(Problem) as error:
                parse_intent('贵州茅台2025年报营业利润，单位元')
        self.assertEqual(error.exception.code, 'FUNDAMENTAL_PARAMETERS_REQUIRED')
        self.assertIn('report_type', error.exception.details['missing'])

    def test_unsupported_ownership_growth_and_deduction_are_not_stripped(self):
        for field in ['扣非归母净利润', '归母净利润同比', '归母股东权益合计', '归属于母公司股东权益合计']:
            with self.subTest(field=field), self.assertRaises(Problem) as error:
                execute_fundamentals(self.service, f'贵州茅台2025年报合并{field}，单位元')
            self.assertEqual(error.exception.code, 'QUERY_PLAN_REQUIRED')
        self.assertEqual(self.calls, [])

    def test_additional_statement_fields_keep_complete_aliases(self):
        question = '贵州茅台2025年报母公司报表营业利润、所有者权益（或股东权益）合计、货币资金、财务费用，单位元'
        result = execute_fundamentals(self.service, question)
        self.assertEqual(result['plan']['intent']['fields'], ['opprofit', 'tot_equity', 'monetary_cap', 'fin_exp_is'])
        self.assertEqual(result['raw_value_count'], 4)
        self.assertEqual(self.calls[-1][1]['options'], 'rptDate=20251231;rptType=2;unit=1')

    def test_unknown_fields_conditions_and_aggregation_are_not_silently_ignored(self):
        for extra in ['和ROE', '>0', '合计', '平均', '不要净利润', '只要大于零的']:
            with self.subTest(extra=extra), self.assertRaises(Problem) as error:
                execute_fundamentals(self.service, f'贵州茅台2025年报合并营业收入{extra}，单位元')
            self.assertEqual(error.exception.code, 'QUERY_PLAN_REQUIRED')
            self.assertFalse(error.exception.details['value_query_executed'])
            self.assertTrue(error.exception.details.get('unparsed_terms'))
        self.assertTrue(all(method == 'wai' for method, _ in self.calls))

    def test_per_company_dates_or_per_field_dates_are_not_cross_joined(self):
        questions = [
            '贵州茅台2024年报营业收入和五粮液2025年报净利润，合并报表，单位元',
            '贵州茅台2024年报营业收入和2025年报净利润，母公司报表，单位元',
            '贵州茅台2025年报合并营业收入和母公司净利润，单位元',
        ]
        for question in questions:
            with self.subTest(question=question), self.assertRaises(Problem) as error:
                execute_fundamentals(self.service, question)
            self.assertEqual(error.exception.code, 'QUERY_PLAN_REQUIRED')
            self.assertIn('dimension_order', error.exception.details)
        self.assertTrue(all(method == 'wai' for method, _ in self.calls))

    def test_date_range_is_not_treated_as_two_report_dates(self):
        with self.assertRaises(Problem) as error:
            execute_fundamentals(self.service, '贵州茅台2024年报至2025年报合并营业收入，单位元')
        self.assertEqual(error.exception.details['unparsed_terms'], ['至'])
        self.assertEqual(len(self.calls), 1)

    def test_all_multi_market_candidates_survive_and_ambiguity_stops_values(self):
        with self.assertRaises(Problem) as error:
            execute_fundamentals(self.service, '中国平安2025年报合并营业收入，单位元')
        self.assertEqual(error.exception.code, 'SECURITY_SELECTION_REQUIRED')
        plan = error.exception.details['fundamental_plan']
        self.assertEqual(len(plan['security_mappings'][0]['candidates']), 3)
        self.assertIsNone(plan['security_mappings'][0]['value'])
        self.assertEqual(self.store.read(plan['derived_receipt_id'])['response']['derived']['status'], 'unresolved')
        self.assertEqual(len(self.calls), 1)

    def test_parenthesized_code_or_explicit_a_share_constraint_disambiguates(self):
        for question in ['中国平安（601318.SH）2025年报合并营业收入，单位元',
                         'A股中国平安2025年报合并营业收入，单位元']:
            with self.subTest(question=question):
                result = execute_fundamentals(self.service, question)
                self.assertEqual(result['plan']['codes'], ['601318.SH'])
                self.assertEqual(result['raw_value_count'], 1)
                self.assertEqual(len(result['plan']['security_mappings'][0]['candidates']), 3)

    def test_explicit_code_and_name_must_agree(self):
        for question in ['中国平安（600519.SH）2025年报合并营业收入，单位元',
                         'A股中国平安（2318.HK）2025年报合并营业收入，单位元']:
            with self.subTest(question=question), self.assertRaises(Problem) as error:
                execute_fundamentals(self.service, question)
            self.assertEqual(error.exception.code, 'SECURITY_SELECTION_REQUIRED')
        self.assertTrue(all(method == 'wai' for method, _ in self.calls))

    def test_code_only_question_still_checks_stock_type(self):
        result = execute_fundamentals(self.service, '600519.SH的2025-12-31合并营业收入，单位元')
        self.assertEqual(result['plan']['codes'], ['600519.SH'])
        self.assertEqual(result['plan']['security_mappings'][0]['selection_rule'], 'explicit_code_in_verified_candidates')
        for name in ['沪深300', '腾讯控股', '200001.SZ']:
            with self.subTest(name=name), self.assertRaises(Problem):
                execute_fundamentals(self.service, f'{name}2025年报合并营业收入，单位元')
        self.assertEqual([method for method, _ in self.calls].count('wss'), 1)

    def test_misaligned_stock_entity_is_not_used(self):
        self.entities_override = [{'entity': '贵州茅台', 'type': 'stockCN', 'id': '600519.SH', 'startIndex': 1, 'endIndex': 4}]
        with self.assertRaises(Problem) as error:
            execute_fundamentals(self.service, BASE)
        self.assertEqual(error.exception.code, 'SECURITY_RECOGNITION_ALIGNMENT')
        self.assertIn('fundamental_plan', error.exception.details)
        self.assertEqual(len(self.calls), 1)

    def test_missing_native_values_are_preserved(self):
        self.missing_value = True
        result = execute_fundamentals(self.service, BASE)
        self.assertEqual(result['missing_value_count'], 1)
        self.assertIsNone(result['rows'][0]['value'])
        self.assertFalse(result['rows'][0]['numeric_value_available'])
        native = self.store.read(result['queries'][0]['response']['receipt_id'])
        self.assertIsNone(native['response']['raw']['Data'][0][0])

    def test_partial_failure_preserves_plan_completed_values_and_failed_receipt(self):
        self.fail_on_date = '20251231'
        question = '贵州茅台2024年报与2025年报合并营业收入，单位元'
        with self.assertRaises(Problem) as error:
            execute_fundamentals(self.service, question)
        self.assertEqual(error.exception.code, 'WIND_UPSTREAM_ERROR')
        result = error.exception.details['fundamental_workflow']
        self.assertEqual(result['status'], 'partial_failure')
        self.assertEqual(result['rows'][0]['report_date_requested'], '2024-12-31')
        self.assertEqual(result['failed_query']['report_date_requested'], '2025-12-31')
        self.assertEqual(self.store.read(result['derived_receipt_id'])['response']['derived']['rows'], result['rows'])
        self.assertEqual(len(self.calls), 3)

    def test_unavailable_mapping_evidence_prevents_recognition_and_value_queries(self):
        self.evidence_patch.stop()
        with self.assertRaises(Problem) as error:
            execute_fundamentals(self.service, BASE)
        self.assertEqual(error.exception.code, 'FUNDAMENTAL_MAPPING_EVIDENCE_UNAVAILABLE')
        self.assertEqual(self.calls, [])

    def test_explicit_verified_request_keeps_existing_override_path(self):
        request = {'method': 'wss', 'arguments': {'codes': '600519.SH', 'fields': 'roe', 'options': 'rptDate=20251231'},
                   'evidence': 'test fixture explicitly verified plan'}
        result = execute(self.service, 'stock_data', 'get_stock_fundamentals', {'question': '茅台ROE'}, request)
        self.assertEqual([method for method, _ in self.calls], ['wss'])
        self.assertEqual(result['raw']['Fields'], ['ROE'])
        self.assertFalse(result['semantic_equivalence_verified'])

    def test_large_or_multiline_intents_fail_before_recognition(self):
        questions = [BASE + '\n' + BASE,
                     '贵州茅台2021年报2022年报2023年报2024年报2025年报合并营业收入，单位元']
        for question in questions:
            with self.subTest(question=question), self.assertRaises(Problem):
                execute_fundamentals(self.service, question)
        self.assertEqual(self.calls, [])


class MappingEvidenceTests(unittest.TestCase):
    def test_mapping_checks_sample_label_and_report_type_instead_of_numeric_wai_id(self):
        row = {'native_receipt_verified': True, 'matches_reference': True, 'code': '600519.SH',
               'receipt_id': 'a' * 32, 'native_sha256': 'receipt_hash', 'reference_sha256': 'reference_hash',
               'reference': {'rpt_type': '1', 'unit_option': '1', 'statement_line': '营业收入',
                             'report_date': '2025-12-31', 'document': {'sha256': 'pdf_hash'}, 'page': 61}}
        intent = parse_intent('贵州茅台2025年报合并营业收入，单位元')
        status = {'status': 'checked'}
        with patch('wind_bridge.fundamental_questions.StatementEvidence') as cls:
            cls.return_value.catalog.return_value = ({'oper_rev': [row]}, status)
            result = mapping_evidence(None, intent)
            self.assertEqual(result['mappings'][0]['field'], 'oper_rev')
            row['reference']['statement_line'] = '营业总收入'
            with self.assertRaises(Problem):
                mapping_evidence(None, intent)
            row['reference']['statement_line'] = '营业收入'
            row['reference']['rpt_type'] = '2'
            with self.assertRaises(Problem):
                mapping_evidence(None, intent)


if __name__ == '__main__':
    unittest.main()
