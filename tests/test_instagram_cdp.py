import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import instagram_automation as automation


class InstagramCdpContractTests(unittest.TestCase):
    def test_cdp_is_the_formal_default(self):
        args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW'])
        self.assertEqual(args.transport, 'cdp')
        self.assertEqual(args.cdp_url, 'http://127.0.0.1:9333')
        self.assertTrue(args.cdp_profile_dir.endswith('outputs\\instagram-cdp-profile'))

    def test_extension_remains_an_explicit_compatibility_mode(self):
        args = automation.parser().parse_args(['collect', '--asin', 'B0787FVCBW', '--transport', 'extension'])
        self.assertEqual(args.transport, 'extension')

    def test_cdp_dispatch_does_not_enqueue_bridge_jobs(self):
        class FakeCdp(automation.CdpTransport):
            def __init__(self): pass
            def run(self, kind, payload, timeout=900): return {'kind': kind, 'payload': payload}
        broker = automation.JobBroker(Path(self._testMethodName + '.json'), {})
        try:
            result = automation.run_browser_job(FakeCdp(), 'inspect_media', {'url': 'URL'})
            self.assertEqual(result['kind'], 'inspect_media')
            self.assertEqual(broker.state['pending_jobs'], [])
            self.assertEqual(broker.state['active_jobs'], {})
        finally:
            Path(self._testMethodName + '.json').unlink(missing_ok=True)

    def test_start_script_disables_extensions_and_reuses_fixed_profile(self):
        source = (ROOT / 'scripts' / 'start_instagram_cdp.ps1').read_text(encoding='utf-8')
        self.assertIn('--remote-debugging-port=$Port', source)
        self.assertIn('--user-data-dir=', source)
        self.assertIn('instagram-cdp-profile', source)
        self.assertIn('--disable-extensions', source)
        self.assertIn('about:blank', source)
        self.assertNotIn('"https://www.instagram.com/"', source)


if __name__ == '__main__':
    unittest.main()
