import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'instagram_opencli.py'
SPEC = importlib.util.spec_from_file_location('instagram_opencli', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InstagramOpenCliTests(unittest.TestCase):
    def test_selects_only_in_viewport_comment_button(self):
        rows = [
            {'index': 0, 'expanded': False, 'in_viewport': False, 'distance': 10},
            {'index': 1, 'expanded': False, 'in_viewport': True, 'distance': 20},
        ]
        self.assertEqual(MODULE.choose_comment_button(rows)['index'], 1)

    def test_prefers_expanded_button_in_viewport(self):
        rows = [
            {'index': 0, 'expanded': False, 'in_viewport': True, 'distance': 1},
            {'index': 1, 'expanded': True, 'in_viewport': True, 'distance': 20},
        ]
        self.assertEqual(MODULE.choose_comment_button(rows)['index'], 1)

    def test_identity_accepts_reel_route_variants(self):
        probe = {
            'href': 'https://www.instagram.com/reels/ABC_123/',
            'pathname': '/reels/ABC_123/',
            'canonical': 'https://www.instagram.com/reel/ABC_123/',
            'og_url': 'https://www.instagram.com/user/reel/ABC_123/',
        }
        self.assertEqual(MODULE.validate_identity(probe, 'ABC_123'), 'ABC_123')

    def test_identity_rejects_shortcode_mismatch(self):
        probe = {
            'href': 'https://www.instagram.com/reels/ABC_123/',
            'canonical': 'https://www.instagram.com/reel/WRONG/',
        }
        with self.assertRaisesRegex(RuntimeError, 'MEDIA_ID_MISMATCH'):
            MODULE.validate_identity(probe, 'ABC_123')

    def test_windows_npm_wrapper_uses_node_entrypoint(self):
        wrapper = Path.home() / 'AppData' / 'Roaming' / 'npm' / 'opencli.cmd'
        prefix = MODULE.opencli_command_prefix(wrapper, platform_name='nt')
        self.assertEqual(Path(prefix[-1]).name, 'main.js')
        self.assertNotEqual(Path(prefix[0]).suffix.lower(), '.cmd')

    def test_native_click_point_accounts_for_browser_chrome(self):
        probe = {'viewport': {'outer_width': 1920, 'outer_height': 1080,
                              'inner_width': 1904, 'inner_height': 863}}
        button = {'rect': {'x': 1208, 'y': 553, 'width': 40, 'height': 36}}
        self.assertEqual(MODULE.native_click_point(probe, button, (0, 0, 1920, 1080)), (1236, 780))

    def test_expansion_labels_exclude_plain_reply_action(self):
        self.assertTrue(MODULE.is_expansion_label('查看所有1条回复'))
        self.assertTrue(MODULE.is_expansion_label('View all 12 replies'))
        self.assertFalse(MODULE.is_expansion_label('回复'))

    def test_comment_merge_is_exact_id_dedupe_and_keeps_parent(self):
        existing = [{'comment_id': '100', 'parent_comment_id': None}]
        incoming = [{'comment_id': '100', 'parent_comment_id': None},
                    {'comment_id': '101', 'parent_comment_id': '100'}]
        rows = MODULE.merge_comment_rows(existing, incoming)
        self.assertEqual([row['comment_id'] for row in rows], ['100', '101'])
        self.assertEqual(rows[1]['parent_comment_id'], '100')


if __name__ == '__main__':
    unittest.main()
