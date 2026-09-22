import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'instagram_collector.py'


def capture(comments, coverage='visible_only', remaining=1):
    return {
        'schema_version': 'instagram_visible_capture_v1',
        'captured_at': '2026-09-17T00:00:00Z',
        'media': {'media_url': 'https://www.instagram.com/reel/ABC_123/?igsh=x', 'owner': 'dogaccount'},
        'page': {'status': 'ready', 'coverage': coverage, 'visible_comment_count': 7,
                 'remaining_expand_controls': remaining},
        'comments': comments,
    }


def comment(comment_id, text, author='user', parent=None, depth=0):
    return {'comment_id': comment_id, 'comment_text': text, 'author': author,
            'parent_comment_id': parent, 'depth': depth, 'published_at': '2026-09-16T00:00:00Z'}


class CollectorTests(unittest.TestCase):
    def test_shortcode_mismatch_discards_comments_and_fails_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'mismatch.json'
            payload = capture([comment('1', 'Wrong media')])
            payload['page']['identity_verified'] = True
            payload['comments'][0]['comment_url'] = 'https://www.instagram.com/reel/WRONG/c/1/'
            source.write_text(json.dumps(payload), encoding='utf-8')
            result = self.run_cli('ingest', '--asin', 'B003ULL1NQ', '--captures', str(source), '--out-dir', str(Path(temp) / 'out'))
            self.assertEqual(result['manifest']['final_collected_count'], 0)
            self.assertGreater(result['manifest']['shortcode_mismatch_count'], 0)
            self.assertEqual(result['manifest']['quality_gate'], 'FAIL')

    def test_recovered_shortcode_mismatch_still_fails_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'recovered.json'
            payload = capture([comment('1', 'Verified media')])
            payload['page']['identity_verified'] = True
            payload['comments'][0]['comment_url'] = 'https://www.instagram.com/reel/ABC_123/c/1/'
            payload['media']['discovery'] = {'shortcode_mismatch_count': 1}
            source.write_text(json.dumps(payload), encoding='utf-8')
            result = self.run_cli('ingest', '--asin', 'B003ULL1NQ', '--captures', str(source), '--out-dir', str(Path(temp) / 'out'))
            self.assertEqual(result['manifest']['final_collected_count'], 1)
            self.assertEqual(result['manifest']['shortcode_mismatch_count'], 1)
            self.assertEqual(result['manifest']['quality_gate'], 'FAIL')

    def run_cli(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT)] + list(args),
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_preflight_reports_partial_instead_of_claiming_completeness(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'capture.json'
            source.write_text(json.dumps(capture([comment('1', 'Good')])) , encoding='utf-8')
            report = self.run_cli('preflight', '--capture', str(source))
            self.assertEqual(report['audit_status'], 'PARTIAL')
            self.assertIn('expand_controls_remaining', report['reason'])

    def test_plan_preserves_direct_media_seed_without_claiming_search_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_cli('plan', '--asin', 'B003ULL1NQ', '--media-urls',
                                  'https://www.instagram.com/p/XYZ123/?igsh=tracking', '--out-dir', temp)
            self.assertEqual(result['seed_media_count'], 1)
            plan = json.loads((Path(temp) / 'query_plan.json').read_text(encoding='utf-8'))
            self.assertEqual(plan['seed_media_urls'], ['https://www.instagram.com/p/XYZ123/'])
            self.assertTrue(plan['missing_product_context'])

    def test_raw_capture_dedup_resume_and_five_sheet_workbook(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / 'outputs'
            a = root / 'a.json'
            b = root / 'b.json'
            a.write_text(json.dumps(capture([
                comment('1', 'Buy now! Affiliate link'),
                comment('2', 'ok'),
                comment('3', 'This is about cats'),
                comment('4', 'Works for my dog'),
                comment('5', 'Works for my dog', author='another'),
                comment('6', 'Reply', parent='1', depth=1),
                comment(None, 'No ID but keep', author='u7'),
            ])), encoding='utf-8')
            b.write_text(json.dumps(capture([
                comment('1', 'Buy now! Affiliate link'),
                comment(None, 'No ID but keep', author='u7'),
                comment('7', 'Works for my dog', author='another'),
            ], remaining=0)), encoding='utf-8')
            self.run_cli('plan', '--asin', 'B003ULL1NQ', '--brand', 'Cosequin', '--out-dir', str(out))
            (out / 'automation_checkpoint.json').write_text(json.dumps({'access_control': {
                'navigation_delay_min_seconds': 20, 'navigation_delay_max_seconds': 35,
                'scroll_delay_min_seconds': 3, 'scroll_delay_max_seconds': 6,
                'post_batch_size': 5, 'batch_rest_min_seconds': 120, 'batch_rest_max_seconds': 300,
                'rate_limit_cooldown_level1_seconds': 3600, 'rate_limit_cooldown_level2_seconds': 14400,
                'rate_limit_cooldown_level3_seconds': 43200, 'rate_limit_count': 2, 'rate_limit_level': 2,
                'cooldown_started_at': '2026-09-20T00:00:00+00:00',
                'cooldown_until': '2026-09-20T00:15:00+00:00',
                'cooldown_ended_at': '2026-09-20T00:15:01+00:00'}}), encoding='utf-8')
            result = self.run_cli('ingest', '--asin', 'B003ULL1NQ', '--captures', str(a), str(b), '--out-dir', str(out))
            manifest = result['manifest']
            self.assertEqual(manifest['raw_captured_count'], 10)
            self.assertEqual(manifest['technical_duplicate_count'], 2)
            self.assertEqual(manifest['final_collected_count'], 8)
            self.assertEqual(manifest['reply_count'], 1)
            self.assertEqual(manifest['standalone_media_records'], 0)
            self.assertEqual(manifest['navigation_delay_min_seconds'], 20)
            self.assertEqual(manifest['navigation_delay_max_seconds'], 35)
            self.assertEqual(manifest['scroll_delay_min_seconds'], 3)
            self.assertEqual(manifest['scroll_delay_max_seconds'], 6)
            self.assertEqual(manifest['rate_limit_count'], 2)
            rows = [json.loads(line) for line in (out / 'raw_comments.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(rows), 8)
            self.assertTrue(all(row['record_type'] == 'comment' for row in rows))
            self.assertIn('Buy now! Affiliate link', [row['comment_text'] for row in rows])
            self.assertIn('This is about cats', [row['comment_text'] for row in rows])
            self.assertEqual(next(row for row in rows if row['comment_id'] == '6')['source_parent_id'], 'comment:1')
            self.assertTrue(all(row['username'] == row['author'] for row in rows))
            self.assertTrue(all(row['post_url'] == row['media_url'] for row in rows))
            self.assertTrue(all(row['shortcode'] == row['media_id'] for row in rows))
            with (out / 'raw_comments.csv').open(encoding='utf-8-sig', newline='') as f:
                self.assertEqual(len(list(csv.DictReader(f))), 8)
            with ZipFile(str(out / 'instagram_raw_comments.xlsx')) as z:
                for name in z.namelist():
                    if name.endswith('.xml') or name.endswith('.rels'):
                        ElementTree.fromstring(z.read(name))
                workbook = z.read('xl/workbook.xml').decode()
                self.assertTrue(all(name in workbook for name in ['Raw_Comments', 'Media_Audit', 'Query_Plan', 'Run_Summary', 'Quality_Gate']))
                self.assertEqual(z.read('xl/worksheets/sheet1.xml').decode().count('<row r='), 9)
                xml_text = ''.join(z.read(name).decode(errors='ignore') for name in z.namelist() if name.endswith('.xml'))
                self.assertIn('qualified_posts', xml_text)
            self.assertTrue((out / 'post_audit.csv').exists())
            # Exact same source file is skipped on resume; counts are unchanged.
            again = self.run_cli('ingest', '--asin', 'B003ULL1NQ', '--captures', str(a), str(b), '--out-dir', str(out))
            self.assertEqual(again['manifest']['raw_captured_count'], 10)
            self.assertEqual(again['manifest']['final_collected_count'], 8)

    def test_blocked_capture_is_audited_without_comment_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'blocked.json'
            payload = capture([])
            payload['page']['status'] = 'login_required'
            source.write_text(json.dumps(payload), encoding='utf-8')
            result = self.run_cli('ingest', '--asin', 'B003ULL1NQ', '--captures', str(source), '--out-dir', str(Path(temp) / 'out'))
            self.assertEqual(result['manifest']['final_collected_count'], 0)
            self.assertEqual(result['manifest']['blocked_media_count'], 1)

    def test_soft_target_ingests_past_1000_goal_and_reports_separate_statuses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); out = root / 'out'; a = root / 'a.json'; b = root / 'b.json'
            a.write_text(json.dumps(capture([comment('1', 'first')], remaining=0)), encoding='utf-8')
            b.write_text(json.dumps(capture([comment('2', 'second')], remaining=0)), encoding='utf-8')
            out.mkdir()
            (out / 'automation_checkpoint.json').write_text(json.dumps({
                'collection_goal': 'maximize_relevant_comments', 'stop_reason': None}), encoding='utf-8')
            result = self.run_cli('ingest', '--asin', 'B003ULL1NQ', '--captures', str(a), str(b),
                                  '--target-comments', '1', '--soft-target', 'true', '--out-dir', str(out))
            manifest = result['manifest']
            self.assertEqual(manifest['final_collected_count'], 2)
            self.assertEqual(manifest['Workflow_Status'], 'COMPLETED_BATCH')
            self.assertEqual(manifest['Coverage_Status'], 'SOFT_TARGET_MET')
            self.assertEqual(manifest['Valid_Comment_Total'], 2)
            self.assertIsNone(manifest['target_shortfall_reason'])

    def test_ineligible_media_stays_in_internal_raw_but_is_excluded_from_excel_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'capture.json'; audit = root / 'audit.json'; out = root / 'out'
            payload = capture([comment('1', 'Captured before strict relevance review')], remaining=0)
            payload['media']['discovery'] = {'relevance_tier': 'LOW', 'deliverable_eligible': False}
            source.write_text(json.dumps(payload), encoding='utf-8')
            audit.write_text(json.dumps([{'shortcode': 'ABC_123', 'media_type': 'reel',
                'url': 'https://www.instagram.com/reel/ABC_123/', 'relevance_tier': 'LOW',
                'deliverable_eligible': False, 'decision': 'AUDIT_ONLY'}]), encoding='utf-8')
            result = self.run_cli('ingest', '--asin', 'B003ULL1NQ', '--captures', str(source),
                                  '--candidate-audit', str(audit), '--out-dir', str(out))
            self.assertEqual(result['manifest']['internal_raw_unique_count'], 1)
            self.assertEqual(result['manifest']['final_collected_count'], 0)
            self.assertEqual(len((out / 'raw_comments.jsonl').read_text(encoding='utf-8').splitlines()), 1)
            with ZipFile(out / 'instagram_raw_comments.xlsx') as z:
                self.assertEqual(z.read('xl/worksheets/sheet1.xml').decode().count('<row r='), 1)

    def test_rate_limit_has_priority_over_trailing_low_audit_stop_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'capture.json'; audit = root / 'audit.json'
            source.write_text(json.dumps(capture([comment('1', 'Visible comment')], remaining=0)), encoding='utf-8')
            audit.write_text(json.dumps([
                {'shortcode': 'RATE123', 'media_type': 'p', 'url': 'https://www.instagram.com/p/RATE123/',
                 'relevance_tier': 'HIGH', 'decision': 'COLLECT', 'stop_reason': 'rate_limited'},
                {'shortcode': 'LOW123', 'media_type': 'p', 'url': 'https://www.instagram.com/p/LOW123/',
                 'relevance_tier': 'LOW', 'decision': 'AUDIT_ONLY', 'stop_reason': 'semantic_low_audit_only'},
            ]), encoding='utf-8')
            result = self.run_cli('ingest', '--asin', 'B003ULL1NQ', '--captures', str(source),
                                  '--candidate-audit', str(audit), '--target-comments', '1000',
                                  '--out-dir', str(root / 'out'))
            self.assertEqual(result['manifest']['stop_reason'], 'rate_limited')

    def test_visible_rows_survive_login_prompt_after_expand_and_unassociated_smoke(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'login_after_visible.json'
            payload = capture([comment('1', 'Visible before prompt')])
            payload['page']['status'] = 'login_required'
            source.write_text(json.dumps(payload), encoding='utf-8')
            self.run_cli('plan', '--smoke', '--media-urls',
                         'https://www.instagram.com/reel/ABC_123/', '--out-dir', str(root / 'out'))
            result = self.run_cli('ingest', '--smoke', '--captures', str(source), '--out-dir', str(root / 'out'))
            self.assertEqual(result['manifest']['final_collected_count'], 1)
            self.assertEqual(result['manifest']['partial_media_count'], 1)
            self.assertEqual(result['manifest']['stop_reason'], 'login_required')
            self.assertEqual(result['manifest']['amazon_asins'], [])


if __name__ == '__main__':
    unittest.main()
