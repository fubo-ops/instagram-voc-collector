import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import instagram_automation as automation


class AutomationRetryTests(unittest.TestCase):
    def test_candidate_and_rendered_media_mismatch_is_rejected(self):
        candidate = {'shortcode': 'EXPECTED'}
        capture = {'media': {'media_id': 'ACTUAL', 'media_url': 'https://www.instagram.com/reel/ACTUAL/'},
            'page': {'identity_verified': True},
            'comments': [{'comment_id': '1', 'comment_url': 'https://www.instagram.com/reel/ACTUAL/c/1/'}]}
        result = automation.capture_identity_check(candidate, capture)
        self.assertFalse(result['ok'])
        self.assertGreater(result['shortcode_mismatch_count'], 0)

    def test_missing_comment_permalink_is_not_a_shortcode_mismatch(self):
        candidate = {'shortcode': 'EXPECTED'}
        capture = {'media': {'media_id': 'EXPECTED', 'media_url': 'https://www.instagram.com/p/EXPECTED/'},
            'page': {'identity_verified': True, 'shortcode_mismatch_count': 0},
            'comments': [{'comment_id': '1', 'comment_url': None},
                         {'comment_id': '2', 'comment_url': 'https://www.instagram.com/p/EXPECTED/c/2/'}]}
        result = automation.capture_identity_check(candidate, capture)
        self.assertTrue(result['ok'])
        self.assertEqual(result['shortcode_mismatch_count'], 0)

    def test_same_extension_handshake_starts_two_tasks_without_reload(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            config = root / 'extension_runtime.json'
            spec = {'extension_dir': str(root / 'extension'), 'version': '2.4.0', 'source_fingerprint': 'abc'}
            for index in range(2):
                broker = automation.JobBroker(root / f'checkpoint-{index}.json', {})
                generation = broker.extension_generation
                broker.record_extension_health({'extension_id': 'fixed-extension-id', 'version': '2.4.0'})
                health = broker.wait_for_extension(generation, 0.1)
                runtime = automation.verify_extension_health(health, config, spec)
                self.assertEqual(runtime['extension_id'], 'fixed-extension-id')
                job = broker.enqueue('collect_media', {'url': 'https://www.instagram.com/p/ABC/'})
                claimed = broker.claim_next()
                self.assertEqual(claimed['job_id'], job['job_id'])
                broker.complete({'job_id': job['job_id'], 'result': {'started': True}})
                self.assertTrue(broker.wait(job['job_id'], 0.1)['started'])

    def test_instagram_job_delay_only_paces_instagram(self):
        self.assertEqual(automation.instagram_job_delay({'url': 'https://example.com/'}, 1, 2), 0)
        self.assertEqual(automation.instagram_job_delay({'url': 'https://www.instagram.com/p/ABC/'}, 8, 10, 5), 3)
        self.assertEqual(automation.instagram_job_delay({'url': 'https://www.instagram.com/reel/ABC/'}, 1, 10, 5), 0)

    def test_formal_defaults_use_randomized_access_and_round_waits(self):
        args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW'])
        self.assertEqual((args.navigation_delay_min_seconds, args.navigation_delay_max_seconds), (20.0, 35.0))
        self.assertEqual((args.scroll_delay_min_seconds, args.scroll_delay_max_seconds), (3.0, 6.0))
        self.assertEqual((args.batch_rest_min_seconds, args.batch_rest_max_seconds), (120.0, 300.0))
        self.assertEqual((args.rate_limit_cooldown_level1_seconds, args.rate_limit_cooldown_level2_seconds,
                          args.rate_limit_cooldown_level3_seconds), (3600, 14400, 43200))

    def test_maximize_goal_treats_1000_as_soft_target_and_saturates_after_two_empty_rounds(self):
        args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW',
            '--collection-goal', 'maximize_relevant_comments', '--target-comments', '1000'])
        self.assertFalse(automation.hard_target_reached(1000, args))
        state = {}
        self.assertEqual(automation.update_semantic_saturation(state, 0, 0, 'one'), 'ACTIVE')
        self.assertEqual(automation.update_semantic_saturation(state, 0, 0, 'two'), 'semantic_saturation')
        self.assertEqual(state['semantic_discovery']['consecutive_no_growth_rounds'], 2)

    def test_access_governor_serially_enforces_minimum_interval(self):
        class FakeCdp(automation.CdpTransport):
            def __init__(self): self.calls = []
            def run(self, kind, payload, timeout=900):
                self.calls.append((kind, payload)); return {'status': 'ready'}
        with tempfile.TemporaryDirectory() as root:
            broker = automation.JobBroker(Path(root) / 'checkpoint.json', {})
            args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW'])
            now, sleeps = [100.0], []
            def sleep(seconds): sleeps.append(seconds); now[0] += seconds
            runner = FakeCdp()
            gate = automation.InstagramAccessGovernor(runner, broker, args, root, lambda: now[0], sleep,
                                                       lambda low, high: low)
            gate.run('inspect_media', {'url': 'https://www.instagram.com/p/AAA/'})
            gate.run('inspect_media', {'url': 'https://www.instagram.com/p/BBB/'})
            self.assertEqual(sleeps, [20.0])
            self.assertEqual(len(runner.calls), 2)

    def test_rate_limit_saves_checkpoint_and_stops_with_level_one_cooldown(self):
        class FakeCdp(automation.CdpTransport):
            def __init__(self): self.calls = 0
            def run(self, kind, payload, timeout=900):
                self.calls += 1; return {'stop_reason': 'Try again later'}
        with tempfile.TemporaryDirectory() as root:
            checkpoint = Path(root) / 'checkpoint.json'
            broker = automation.JobBroker(checkpoint, {})
            args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW'])
            runner = FakeCdp()
            gate = automation.InstagramAccessGovernor(runner, broker, args, root)
            with self.assertRaises(automation.RateLimitDetected):
                gate.run('inspect_media', {'url': 'https://www.instagram.com/p/AAA/'})
            saved = automation.json.loads(checkpoint.read_text(encoding='utf-8'))
            access = saved['access_control']
            self.assertEqual(runner.calls, 1)
            self.assertEqual(saved['stop_reason'], 'rate_limited')
            self.assertEqual(access['rate_limit_count'], 1)
            self.assertGreaterEqual((datetime.fromisoformat(access['cooldown_until']) -
                                     datetime.fromisoformat(access['cooldown_started_at'])).total_seconds(), 3600)
            self.assertEqual(automation.json.loads((Path(root) / 'manifest.json').read_text(encoding='utf-8'))['rate_limit_count'], 1)

    def test_graded_cooldown_is_one_four_then_twelve_hours(self):
        access = {'rate_limit_cooldown_level1_seconds': 3600, 'rate_limit_cooldown_level2_seconds': 14400,
                  'rate_limit_cooldown_level3_seconds': 43200}
        self.assertEqual([automation.cooldown_seconds(n, access) for n in (1, 2, 3, 8)],
                         [3600, 14400, 43200, 43200])

    def test_active_cooldown_rejects_resume_before_browser_start(self):
        with tempfile.TemporaryDirectory() as root:
            broker = automation.JobBroker(Path(root) / 'checkpoint.json', {'access_control': {
                'rate_limit_count': 1,
                'cooldown_started_at': datetime.now(timezone.utc).isoformat(),
                'cooldown_until': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()}})
            args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW'])
            with self.assertRaises(automation.RateLimitDetected):
                automation.ensure_cooldown_ready(broker, args)

    def test_controller_does_not_construct_cdp_transport_during_cooldown(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); checkpoint = root / 'automation_checkpoint.json'
            automation.atomic_json(checkpoint, {'input_asins': ['B0787FVCBW'], 'access_control': {
                'rate_limit_count': 1, 'cooldown_started_at': datetime.now(timezone.utc).isoformat(),
                'cooldown_until': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}})
            args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW', '--resume', str(checkpoint),
                                                   '--out-dir', str(root)])
            with patch.object(automation, 'CdpTransport') as transport:
                with self.assertRaises(automation.RateLimitDetected): automation.controller(args)
                transport.assert_not_called()

    def test_every_five_completed_posts_triggers_random_two_to_five_minute_rest(self):
        class FakeCdp(automation.CdpTransport):
            def __init__(self): pass
            def run(self, kind, payload, timeout=900): return {}
        with tempfile.TemporaryDirectory() as root:
            broker = automation.JobBroker(Path(root) / 'checkpoint.json', {})
            args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW'])
            sleeps = []
            gate = automation.InstagramAccessGovernor(FakeCdp(), broker, args, root,
                sleeper=sleeps.append, randomizer=lambda low, high: low)
            for _ in range(5): gate.record_post_completed()
            self.assertEqual(gate.rest_if_due(), 120.0)
            self.assertEqual(sleeps, [120.0])
            self.assertEqual(broker.state['access_control']['posts_completed_since_rest'], 0)

    def test_readonly_preflight_is_single_gate_before_small_batch(self):
        with tempfile.TemporaryDirectory() as root:
            broker = automation.JobBroker(Path(root) / 'checkpoint.json', {'access_control': {
                'rate_limit_count': 3, 'readonly_preflight_required': True}})
            self.assertTrue(automation.complete_readonly_preflight(broker, root, {'status': 'ready'}))
            access = broker.state['access_control']
            self.assertFalse(access['readonly_preflight_required'])
            self.assertEqual(access['readonly_preflight_attempt_count'], 1)
            self.assertEqual(access['recovery_phase'], 'small_batch')
            args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW'])
            self.assertEqual(automation.recovery_limits(access, args), {'query_budget': 1, 'media_budget': 1})

    def test_query_plan_execution_and_qualification_stats_are_written_back(self):
        queries = [{'query': 'slow feeder dog bowl', 'executed': True, 'posts_found': 2},
                   {'query': 'dog bloating', 'executed': False, 'posts_found': 0}]
        candidates = [{'shortcode': 'A', 'matched_queries': ['slow feeder dog bowl'], 'decision': 'COLLECT'},
                      {'shortcode': 'B', 'source_query': 'slow feeder dog bowl', 'decision': 'AUDIT_ONLY'}]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'query_plan.json'
            automation.write_query_plan(path, [], queries, candidates, ['B0787FVCBW'], 1000, 30)
            saved = json.loads(path.read_text(encoding='utf-8'))['queries']
        self.assertEqual(saved[0]['posts_found'], 2)
        self.assertEqual(saved[0]['qualified_posts'], 1)
        self.assertFalse(saved[1]['executed'])
        self.assertEqual(saved[1]['qualified_posts'], 0)

    def test_grouped_gate_is_reapplied_before_media_collection(self):
        candidate = {'owner': 'outwardhound', 'caption': 'unrelated shelter announcement'}
        profile = automation.discovery.build_product_profile('B0787FVCBW', {
            'title': 'Outward Hound Dog Fun Feeder Slo Bowl', 'brand': 'Outward Hound',
            'category': 'Slow Feeders', 'bullets': ['slow down eating']})
        score = automation.discovery.score_media_relevance(candidate, [profile])
        self.assertFalse(automation.apply_media_gate(candidate, score))
        self.assertEqual(candidate['decision'], 'AUDIT_ONLY')
        self.assertEqual(candidate['relevance_model_version'], 'grouped-semantic-gate-v2')

    def test_post_and_reel_collection_use_canonical_media_route(self):
        self.assertEqual(automation.collection_url({'url': 'https://www.instagram.com/p/ABC/', 'media_type': 'p'}),
                         'https://www.instagram.com/p/ABC/')
        self.assertEqual(automation.collection_url({'url': 'https://www.instagram.com/reel/XYZ/', 'media_type': 'reel', 'shortcode': 'XYZ'}),
                         'https://www.instagram.com/reel/XYZ/')
        self.assertEqual(automation.extension_navigation_url({'url': 'https://www.instagram.com/p/ABC/', 'media_type': 'p'}),
                         'https://www.instagram.com/p/ABC/')
        self.assertEqual(automation.extension_navigation_url({'url': 'https://www.instagram.com/reel/XYZ/', 'media_type': 'reel', 'shortcode': 'XYZ'}),
                         'https://www.instagram.com/reel/XYZ/')
        self.assertEqual(automation.extension_navigation_url({'url': 'https://www.instagram.com/reel/XYZ/',
            'media_type': 'reel', 'shortcode': 'XYZ', 'working_media_url': 'https://www.instagram.com/p/XYZ/'}),
            'https://www.instagram.com/p/XYZ/')

    def test_rendered_route_does_not_overwrite_discovery_route(self):
        candidate = {'shortcode': 'ABC', 'url': 'https://www.instagram.com/reel/ABC/', 'media_type': 'reel'}
        automation.merge_route_inspection(candidate, {'media_id': 'ABC', 'media_type': 'p',
            'media_url': 'https://www.instagram.com/p/ABC/', 'rendered_url': 'https://www.instagram.com/p/ABC/',
            'rendered_media_type': 'p', 'working_media_url': 'https://www.instagram.com/p/ABC/',
            'requested_url': 'https://www.instagram.com/p/ABC/'}, 'https://www.instagram.com/reel/ABC/')
        self.assertEqual(candidate['discovery_media_type'], 'reel')
        self.assertEqual(candidate['discovery_url'], 'https://www.instagram.com/reel/ABC/')
        self.assertEqual(candidate['rendered_media_type'], 'p')
        self.assertEqual(candidate['working_media_url'], 'https://www.instagram.com/p/ABC/')

    def test_zero_partial_and_platform_gap_are_retried(self):
        self.assertTrue(automation.media_result_needs_retry({'comment_count': 0, 'audit_status': 'PARTIAL'}))
        self.assertTrue(automation.media_result_needs_retry({'comment_count': 4, 'audit_status': 'PARTIAL'}))
        self.assertTrue(automation.media_result_needs_retry({'comment_count': 4, 'audit_status': 'PASS', 'platform_comment_count': 40}))
        self.assertFalse(automation.media_result_needs_retry({'comment_count': 40, 'audit_status': 'PASS', 'platform_comment_count': 40}))

    def test_retry_merge_uses_only_technical_dedupe_and_keeps_lineage(self):
        previous = {'media': {'media_id': 'ABC'}, 'page': {'platform_comment_count': 9},
            'comments': [{'comment_id': '1', 'author': 'a', 'comment_text': 'x', 'parent_comment_id': None}],
            'continuous_scroll': {'action_ledger': [{'round': 1}], 'attempt_count': 1}}
        current = {'media': {'media_id': 'ABC'}, 'page': {'platform_comment_count': 10},
            'comments': [{'comment_id': '1', 'author': 'a', 'comment_text': 'x'},
                         {'comment_id': '2', 'author': 'b', 'comment_text': 'y', 'parent_comment_id': '1'}],
            'continuous_scroll': {'action_ledger': [{'round': 2}]}}
        merged = automation.merge_captures(previous, current)
        self.assertEqual(len(merged['comments']), 2)
        self.assertEqual(merged['comments'][1]['parent_comment_id'], '1')
        self.assertEqual(merged['page']['platform_comment_count'], 10)
        self.assertEqual(len(merged['continuous_scroll']['action_ledger']), 2)

    def test_progress_checkpoint_keeps_frontier_thread_and_retry_state(self):
        with tempfile.TemporaryDirectory() as root:
            broker = automation.JobBroker(Path(root) / 'checkpoint.json', {})
            job = broker.enqueue('collect_media', {})
            claimed = broker.claim_next()
            self.assertEqual(job['job_id'], claimed['job_id'])
            progress = {'collected_comment_ids': ['1'], 'expanded_thread_signatures': ['thread-a'],
                        'scroll_frontier': {'last_comment_id': '1'}, 'retry_count': 2}
            broker.record_progress({'job_id': job['job_id'], 'progress': progress})
            saved = broker.state['active_jobs'][job['job_id']]
            for key, value in progress.items(): self.assertEqual(saved[key], value)
            self.assertEqual(saved['progress_history'][0]['comment_count'] if 'comment_count' in progress else
                             saved['progress_history'][0]['retry_count'], progress.get('comment_count', progress['retry_count']))

    def test_failed_reaudit_keeps_last_valid_capture_authoritative(self):
        capture = {'page': {'status': 'ready', 'platform_comment_count': 9},
            'comments': [{'comment_id': '18134840677624024'}, {'comment_id': '18081571562494667'}],
            'continuous_scroll': {'stop_reason': 'idle_rounds', 'expanded_thread_signatures': ['thread-a'],
                                  'scroll_frontier': {'last_comment_id': '18081571562494667'}}}
        stale = {'comment_count': 2, 'stop_reason': 'Frame with ID 0 is showing error page', 'audit_status': 'BLOCKED'}
        result = automation.result_from_capture(stale, capture, Path('capture.json'), 3)
        self.assertEqual(result['comment_count'], 2)
        self.assertEqual(result['stop_reason'], 'idle_rounds')
        self.assertEqual(result['audit_status'], 'PARTIAL')
        self.assertEqual(result['collected_comment_ids'], ['18134840677624024', '18081571562494667'])

    def test_abandoned_job_leaves_no_pending_or_active_work(self):
        with tempfile.TemporaryDirectory() as root:
            broker = automation.JobBroker(Path(root) / 'checkpoint.json', {})
            pending = broker.enqueue('inspect_media', {})
            self.assertTrue(broker.abandon(pending['job_id'], 'stopped'))
            active = broker.enqueue('inspect_media', {})
            broker.claim_next()
            self.assertTrue(broker.abandon(active['job_id'], 'stopped'))
            self.assertEqual(broker.state['pending_jobs'], [])
            self.assertEqual(broker.state['active_jobs'], {})
            self.assertEqual(len(broker.state['failed_jobs']), 2)


if __name__ == '__main__':
    unittest.main()
