import json
from pathlib import Path
import tempfile
import unittest

from wind_bridge.common import Problem
from wind_bridge.edb_export import decode_export, parse_export
from wind_bridge.service import Service
from wind_bridge.storage import Store


TEXT = ('国家,中国,中国\n指标名称,"产量,原煤",CPI\n频率,月,月\n单位,万吨,%\n'
        '指标ID,S0026989,M0000612\n时间区间,1986-01:2026-08,1987-01:2026-08\n'
        '来源,国家统计局,国家统计局\n更新时间,2026-09-17,2026-09-09\n'
        '1986-01-31,6662,\n1987-01-31,6434,7.1\n数据来源：Wind\n')


class ExportTests(unittest.TestCase):
    def test_multi_indicator_headers_stay_separate_from_native_metadata(self):
        result = parse_export(TEXT, 'test fixture, not live evidence')
        self.assertEqual(result['indicator_count'], 2)
        coal, cpi = result['indicators']
        self.assertEqual(coal['name'], '产量,原煤')
        self.assertEqual(coal['export_metadata']['unit'], '万吨')
        self.assertEqual(cpi['export_metadata']['source'], '国家统计局')
        self.assertEqual(cpi['displayed_nonblank_observations'], 1)
        self.assertIsNone(coal['unit'])
        self.assertIsNone(coal['freq'])
        self.assertIsNone(coal['source'])
        self.assertFalse(result['native_metadata_promoted'])
        self.assertFalse(result['origin_independently_verified'])
        self.assertFalse(result['executed'])

    def test_export_only_candidates_cannot_drive_frequency_based_native_query(self):
        with tempfile.TemporaryDirectory() as folder:
            service = Service(Store(Path(folder)))
            result = parse_export(TEXT, 'unreviewed display metadata')
            service.register(result['indicators'], result['metadata_source_id'], result['evidence'])
            with self.assertRaises(Problem) as raised:
                service.economic('S0026989', observation='10')
            self.assertEqual(raised.exception.code, 'FREQUENCY_REQUIRED')

    def test_utf8_and_gb18030_are_decoded_without_loss(self):
        for encoding in ('utf-8-sig', 'gb18030'):
            decoded, _ = decode_export(TEXT.encode(encoding))
            self.assertEqual(decoded, TEXT)
        with self.assertRaises(Problem):
            decode_export(b'\xff')

    def test_descending_dates_and_missing_optional_headers(self):
        value = parse_export('指标ID,S0026989\n指标名称,原煤\n2026-08-31,1\n2026-07-31,0\n', 'fixture')
        self.assertEqual(value['display_observations']['first_date'], '2026-07-31')
        self.assertEqual(value['indicators'][0]['displayed_nonblank_observations'], 2)
        self.assertIsNone(value['indicators'][0]['export_metadata']['source'])

    def test_malformed_and_ambiguous_files_rejected(self):
        invalid = [TEXT.replace('M0000612', 'S0026989'),
                   TEXT.replace('M0000612', '=S0026989'),
                   TEXT.replace('频率,月,月', '频率,月'),
                   TEXT.replace('6434,7.1', '6434'),
                   TEXT.replace('指标名称', '未知行'),
                   TEXT.replace('1987-01-31', '1986-01-31'),
                   TEXT.replace('1987-01-31', '1987-02-30'),
                   TEXT + '1988-01-31,1,2\n',
                   TEXT.replace('国家,中国,中国', '指标ID,S0026989,M0000612'),
                   '指标ID,指标名称,单位\nS0026989,原煤,万吨\n',
                   '指标ID,S0026989\n指标名称,"unfinished\n']
        for text in invalid:
            with self.subTest(text=text[:60]), self.assertRaises(Problem):
                parse_export(text, 'fixture')

    def test_limit_and_provenance_errors(self):
        for text, evidence in [('', 'x'), (123, 'x'), ('中' * 1_400_000, 'x'),
                               (TEXT + '\x00', 'x'), (TEXT, ''), (TEXT, 'x' * 2001)]:
            with self.assertRaises(Problem):
                parse_export(text, evidence)
        with self.assertRaises(Problem):
            decode_export(b'x' * 4_000_001)

    def test_formula_cells_are_not_executed_or_imported(self):
        result = parse_export(TEXT.replace('6662', '=1+1'), 'fixture')
        self.assertFalse(result['values_imported'])
        self.assertNotIn('=1+1', json.dumps(result))

    def test_gb18030_file_fixture_without_publishing_raw_wind_data(self):
        path = Path(__file__).resolve().parent / 'fixtures/edb-export-gb18030.csv'
        text, encoding = decode_export(path.read_bytes())
        result = parse_export(text, 'Synthetic parser fixture; not a Wind export or market observation')
        self.assertEqual(encoding, 'gb18030')
        self.assertEqual(result['display_observations']['rows'], 3)
        self.assertEqual(result['indicators'][0]['export_metadata']['time_range'], '2000-01:2000-03')
        self.assertFalse(result['values_imported'])
