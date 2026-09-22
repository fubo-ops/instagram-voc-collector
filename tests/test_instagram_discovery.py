import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.discovery = load('instagram_discovery', 'instagram_discovery.py')
        cls.product = {
            'asin': 'B003ULL1NQ', 'title': 'Nutramax Cosequin Joint Health Supplement for Dogs',
            'brand': 'Nutramax', 'category': 'Dog Joint Supplements',
            'bullets': ['Glucosamine and chondroitin support healthy joints',
                        'For senior dogs with stiffness and mobility needs']
        }

    def test_asin_product_profile_has_required_semantic_layers(self):
        profile = self.discovery.build_product_profile('B003ULL1NQ', self.product)
        for key in ('brand', 'product_name', 'category', 'core_functions', 'use_scenarios', 'pain_points', 'keywords'):
            self.assertIn(key, profile)
        self.assertIn('senior dog', [x.lower() for x in profile['use_scenarios']])
        self.assertIn('stiffness', [x.lower() for x in profile['pain_points']])

    def test_query_plan_is_generated_from_asin_profile(self):
        profile = self.discovery.build_product_profile('B003ULL1NQ', self.product)
        queries = self.discovery.generate_instagram_queries([profile])
        self.assertGreaterEqual(len(queries), 6)
        self.assertTrue(all(q['generation_source'] == 'asin_product_profile' for q in queries))
        self.assertIn('product_entity', {q['query_family'] for q in queries})

    def test_grouped_gate_expansion_queries_are_bounded_and_deduped(self):
        profile = self.discovery.build_product_profile('B0787FVCBW', {
            'title': 'Outward Hound Dog Fun Feeder Slo Bowl', 'brand': 'Outward Hound',
            'category': 'Slow Feeders', 'bullets': ['slow down eating for dogs']})
        seed = self.discovery.generate_instagram_queries([profile])
        expanded = self.discovery.generate_expansion_queries([profile], seed)
        self.assertEqual(len(expanded), 4)
        self.assertTrue(all(row['generation_source'] == 'grouped_semantic_gate_v2_expansion' for row in expanded))
        self.assertFalse({row['query'].casefold() for row in seed} & {row['query'].casefold() for row in expanded})

    def test_evidence_queries_keep_strong_anchor_and_page_provenance(self):
        profile = self.discovery.build_product_profile('B0787FVCBW', {
            'title': 'Outward Hound Dog Fun Feeder Slo Bowl', 'brand': 'Outward Hound',
            'category': 'Slow Feeders', 'bullets': ['slow down eating for dogs']})
        media = [{'shortcode': 'HIGH1', 'matched_asins': ['B0787FVCBW'], 'relevance_tier': 'HIGH',
                  'deliverable_eligible': True, 'evidence_terms': ['Slow Feeder'],
                  'hashtags': ['#slobowl']}]
        comments = [{'media_id': 'HIGH1', 'comment_id': '18123456789012345',
                     'comment_relevance_tier': 'MEDIUM', 'comment_evidence_terms': ['gulping food']}]
        rows = self.discovery.generate_evidence_queries([profile], media, comments, round_number=3)
        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(all(row['strong_anchor'] in row['query'] for row in rows))
        self.assertTrue(all(row['generation_source'] == 'verified_high_media_evidence_round_3' for row in rows))
        self.assertTrue(all(row['evidence_shortcodes'] == ['HIGH1'] for row in rows))

    def test_slow_feeder_profile_uses_product_semantics_not_amazon_ui_label(self):
        product = {
            'title': 'Outward Hound Fun Feeder Slo Bowl – Medium 2-Cup Capacity, Slow Feeding',
            'brand': 'Outward Hound', 'category': '产品和A+拼图',
            'bullets': ['Helps slow down eating up to 10X', 'Puzzle feeder for small to medium dogs',
                        'Non-slip, food-safe, and easy to clean']
        }
        profile = self.discovery.build_product_profile('B0787FVCBW', product)
        self.assertEqual(profile['category'], 'slow feeder dog bowl')
        self.assertIn('Fun Feeder Slo Bowl', profile['product_aliases'])
        self.assertNotIn('Medium', profile['product_aliases'])
        queries = [q['query'].lower() for q in self.discovery.generate_instagram_queries([profile])]
        self.assertTrue(any('slow feeder dog bowl' in q for q in queries))
        self.assertFalse(any('a+拼图' in q for q in queries))

    def test_candidate_shortcodes_are_deduped_and_provenance_merged(self):
        rows = [
            {'url': 'https://www.instagram.com/p/ABC123/?x=1', 'source_query': 'one'},
            {'url': 'https://instagram.com/p/ABC123/', 'source_query': 'two'},
            {'url': 'https://www.instagram.com/reel/ZZ_9/', 'source_query': 'three'},
        ]
        result = self.discovery.dedupe_media_candidates(rows)
        self.assertEqual([x['shortcode'] for x in result], ['ABC123', 'ZZ_9'])
        self.assertEqual(result[0]['matched_queries'], ['one', 'two'])

    def test_media_relevance_has_high_medium_low_tiers(self):
        profile = self.discovery.build_product_profile('B003ULL1NQ', self.product)
        high = self.discovery.score_media_relevance({'caption': 'Nutramax Cosequin for senior dog joint stiffness'}, [profile])
        profile['competitors'] = ['Dasuquin']
        medium = self.discovery.score_media_relevance({'caption': 'Dasuquin joint health supplement for dogs'}, [profile])
        low = self.discovery.score_media_relevance({'caption': 'summer fashion in New York'}, [profile])
        self.assertEqual([high['tier'], medium['tier'], low['tier']], ['HIGH', 'MEDIUM', 'LOW'])
        query_only = self.discovery.score_media_relevance({'source_query': 'Nutramax Cosequin dog joints'}, [profile])
        self.assertEqual(query_only['tier'], 'LOW')

    def test_brand_or_generic_category_alone_never_qualifies_media(self):
        profile = self.discovery.build_product_profile('B0787FVCBW', {
            'title': 'Outward Hound Dog Fun Feeder Slo Bowl', 'brand': 'Outward Hound',
            'category': 'Slow Feeders', 'bullets': ['slow down eating', 'non-slip feeding']})
        for candidate in (
            {'caption': 'Shop the Snoop treat dispenser toy', 'hashtags': ['#outwardhound']},
            {'caption': 'dog bowl slow feeder pet feeding tips'},
            {'matched_queries': ['Outward Hound Fun Feeder Slo Bowl'], 'source_query': 'Outward Hound'},
        ):
            score = self.discovery.score_media_relevance(candidate, [profile])
            self.assertEqual(score['tier'], 'LOW')
            self.assertFalse(score['deliverable_eligible'])

    def test_product_evidence_qualifies_without_using_discovery_provenance(self):
        profile = self.discovery.build_product_profile('B0787FVCBW', {
            'title': 'Outward Hound Fun Feeder Slo Bowl', 'brand': 'Outward Hound',
            'category': 'Slow Feeders', 'bullets': ['slow down eating', 'non-slip feeding']})
        score = self.discovery.score_media_relevance({
            'caption': 'Outward Hound Fun Feeder Slo Bowl slows down eating',
            'hashtags': ['#outwardhoundfunfeederslobowl']}, [profile])
        self.assertEqual(score['tier'], 'HIGH')
        self.assertTrue(score['deliverable_eligible'])

    def test_brand_account_plus_function_group_is_collectible(self):
        profile = self.discovery.build_product_profile('B0787FVCBW', {
            'title': 'Outward Hound Fun Feeder Slo Bowl', 'brand': 'Outward Hound',
            'category': 'Slow Feeders', 'bullets': ['slow down eating', 'non-slip feeding']})
        score = self.discovery.score_media_relevance({'owner': 'outwardhound',
            'caption': 'Designed to slow down eating at every meal'}, [profile])
        self.assertEqual(score['tier'], 'HIGH')
        self.assertEqual(set(score['matched_groups']), {'entity', 'category_function'})

    def test_brand_owner_only_and_species_conflict_are_rejected(self):
        profile = self.discovery.build_product_profile('B0787FVCBW', {
            'title': 'Outward Hound Dog Fun Feeder Slo Bowl', 'brand': 'Outward Hound',
            'category': 'Slow Feeders', 'bullets': ['slow down eating for dogs']})
        owner_only = self.discovery.score_media_relevance({'owner': 'outwardhound'}, [profile])
        wrong_species = self.discovery.score_media_relevance({'owner': 'outwardhound',
            'caption': 'Slow feeder bowl designed for cats and feline mealtimes'}, [profile])
        self.assertEqual(owner_only['tier'], 'LOW')
        self.assertEqual(wrong_species['reason'], 'species_or_category_conflict')
        self.assertFalse(wrong_species['deliverable_eligible'])

    def test_comment_semantics_are_labels_not_delivery_filters(self):
        profile = self.discovery.build_product_profile('B003ULL1NQ', self.product)
        levels = [self.discovery.classify_comment_relevance(text, [profile])['tier'] for text in (
            'Cosequin helped my senior dog', 'joint mobility improved', 'beautiful photo')]
        self.assertEqual(levels, ['HIGH', 'MEDIUM', 'LOW'])


class AutomationContractTests(unittest.TestCase):
    def test_cli_defaults_match_formal_workflow(self):
        run = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'instagram_automation.py'), '--help'],
                             capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(run.returncode, 0, run.stderr)
        for flag in ('--target-posts', '--target-comments', '--max-scroll-rounds-per-post',
                     '--scroll-delay-min-seconds', '--scroll-delay-max-seconds',
                     '--navigation-delay-min-seconds', '--navigation-delay-max-seconds',
                     '--post-batch-size', '--batch-rest-min-seconds', '--batch-rest-max-seconds',
                     '--no-growth-limit', '--smoke-url', '--resume'):
            self.assertIn(flag, run.stdout)

    def test_job_broker_writes_progress_checkpoint(self):
        automation = load('instagram_automation', 'instagram_automation.py')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'checkpoint.json'
            broker = automation.JobBroker(path, {'schema_version': 'instagram_automation_checkpoint_v2'})
            job = broker.enqueue('collect_media', {'url': 'https://www.instagram.com/p/ABC/'})
            claimed = broker.claim_next()
            self.assertEqual(claimed['job_id'], job['job_id'])
            broker.record_progress({'job_id': job['job_id'], 'progress': {'round_count': 2, 'comment_count': 17}})
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['active_jobs'][job['job_id']]['comment_count'], 17)


if __name__ == '__main__':
    unittest.main()
