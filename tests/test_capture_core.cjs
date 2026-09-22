const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../extension/capture_core.js');
const fs = require('node:fs');
const path = require('node:path');

test('only Instagram post and reel URLs are accepted', () => {
  assert.deepEqual(core.mediaIdentity('https://www.instagram.com/reel/ABC_123/?igsh=x'), {
    media_id: 'ABC_123', media_type: 'reel', media_url: 'https://www.instagram.com/reel/ABC_123/'
  });
  assert.deepEqual(core.mediaIdentity('https://www.instagram.com/reels/ABC_123/'), {
    media_id: 'ABC_123', media_type: 'reel', media_url: 'https://www.instagram.com/reel/ABC_123/'
  });
  assert.throws(() => core.mediaIdentity('https://other.example/reel/ABC_123/'));
  assert.throws(() => core.mediaIdentity('https://www.instagram.com/accounts/login/'));
});

test('rendered media identity must match location metadata comments and candidate', () => {
  const good = core.verifyMediaIdentity({
    location_url: 'https://www.instagram.com/reel/ABC_123/',
    canonical_url: 'https://www.instagram.com/reel/ABC_123/',
    og_url: 'https://www.instagram.com/reel/ABC_123/',
    comment_urls: ['https://www.instagram.com/reel/ABC_123/c/18121140331709618/'],
    expected_shortcode: 'ABC_123'
  });
  assert.equal(good.ok, true);
  const bad = core.verifyMediaIdentity({
    location_url: 'https://www.instagram.com/reel/WRONG/',
    canonical_url: 'https://www.instagram.com/reel/WRONG/',
    comment_urls: ['https://www.instagram.com/reel/WRONG/c/18121140331709618/'],
    expected_shortcode: 'ABC_123'
  });
  assert.equal(bad.ok, false);
  assert.equal(bad.reason, 'MEDIA_ID_MISMATCH');
  const spa = core.verifyMediaIdentity({
    location_url: 'https://www.instagram.com/outwardhound/p/ABC/',
    expected_shortcode: 'ABC',
    comment_urls: ['https://www.instagram.com/p/ABC/c/123/']
  });
  assert.equal(spa.ok, true);
});

test('comment permalink never rewrites a Reel route to Post', () => {
  const result = core.routeEvidence({requested_url: 'https://www.instagram.com/reel/ABC_123/',
    final_url: 'https://www.instagram.com/reel/ABC_123/', location_url: 'https://www.instagram.com/reel/ABC_123/',
    canonical_url: 'https://www.instagram.com/p/ABC_123/',
    comment_urls: ['https://www.instagram.com/p/ABC_123/c/123/'], page_ready: true});
  assert.equal(result.ok, true);
  assert.equal(result.rendered_media_type, 'reel');
  assert.deepEqual(result.evidence_conflicts, ['canonical_route_type']);
});

test('same-shortcode fallback flips only the route type', () => {
  assert.equal(core.alternateMediaUrl('https://www.instagram.com/reel/ABC_123/'), 'https://www.instagram.com/p/ABC_123/');
  assert.equal(core.alternateMediaUrl('https://www.instagram.com/p/ABC_123/'), 'https://www.instagram.com/reel/ABC_123/');
});

test('stale previous media and route redirects are rejected', () => {
  const stale = core.routeEvidence({requested_url: 'https://www.instagram.com/p/POST_B/',
    final_url: 'https://www.instagram.com/p/POST_A/', location_url: 'https://www.instagram.com/p/POST_A/', page_ready: true});
  assert.equal(stale.ok, false);
  assert.equal(stale.shortcode_mismatch, true);
  const redirected = core.routeEvidence({requested_url: 'https://www.instagram.com/reel/SAME/',
    final_url: 'https://www.instagram.com/p/SAME/', location_url: 'https://www.instagram.com/p/SAME/', page_ready: true});
  assert.equal(redirected.ok, false);
  assert.equal(redirected.route_mismatch, true);
});

test('comment IDs come only from visible links', () => {
  assert.equal(core.commentId('https://www.instagram.com/p/ABC/?comment_id=123'), '123');
  assert.equal(core.commentId('https://www.instagram.com/p/ABC/c/18121140331709618/'), '18121140331709618');
  assert.equal(core.commentId('https://www.instagram.com/p/ABC/'), null);
});

test('visible restriction labels become stop reasons', () => {
  assert.equal(core.restriction('Log in to Instagram to continue'), 'login_required');
  assert.equal(core.restriction('Try again later'), 'rate_limited');
  assert.equal(core.restriction('ordinary visible comments'), 'ready');
});

test('visible platform totals support localized compact counts', () => {
  assert.equal(core.visibleCount('6,565 comments'), 6565);
  assert.equal(core.visibleCount('评论 6.6万'), 66000);
  assert.equal(core.visibleCount('6,566'), 6566);
  assert.equal(core.visibleCount('no count here'), null);
});

test('localized expansion controls are recognized conservatively', () => {
  assert.equal(core.isExpansionLabel('View all 56 replies'), true);
  assert.equal(core.isExpansionLabel('加载更多评论'), true);
  assert.equal(core.isExpansionLabel('查看所有85条回复'), true);
  assert.equal(core.isExpansionLabel('Follow'), false);
  assert.equal(core.isExpansionControl({text: '', aria: 'Load more comments', title: ''}), true);
  assert.equal(core.isExpansionControl(['', '', '查看更多回复']), true);
  assert.equal(core.isExpansionLabel('View previous comments'), true);
});

test('idle rounds require a ready container with no expansion or loading frontier', () => {
  assert.equal(core.shouldCountIdle({ready: false}), false);
  assert.equal(core.shouldCountIdle({ready: true, pending_controls: 1}), false);
  assert.equal(core.shouldCountIdle({ready: true, actions: 1}), false);
  assert.equal(core.shouldCountIdle({ready: true, frontier_changed: true}), false);
  assert.equal(core.shouldCountIdle({ready: true}), true);
});

test('visible exhaustion is distinguished from ambiguous idle rounds', () => {
  assert.equal(core.visibleExhausted({ready: true, empty: true}), true);
  assert.equal(core.visibleExhausted({ready: true, node_count: 3, frontier: {scroll_top: 900, client_height: 100, scroll_height: 1000}}), true);
  assert.equal(core.visibleExhausted({ready: true, loading: true, node_count: 3, frontier: {scroll_top: 900, client_height: 100, scroll_height: 1000}}), false);
  assert.equal(core.visibleExhausted({ready: true, pending_controls: 1, node_count: 3, frontier: {scroll_top: 900, client_height: 100, scroll_height: 1000}}), false);
});

test('continuous mode stops at explicit safety boundaries', () => {
  const limits = {target_comments: 200, max_rounds: 40, max_expand_actions: 100, idle_rounds: 3, max_runtime_ms: 300000};
  assert.equal(core.continuousStopReason({comment_count: 200}, limits), 'target_comments');
  assert.equal(core.continuousStopReason({round_count: 40}, limits), 'max_rounds');
  assert.equal(core.continuousStopReason({expand_action_count: 100}, limits), 'max_expand_actions');
  assert.equal(core.continuousStopReason({idle_round_count: 3}, limits), 'idle_rounds');
  assert.equal(core.continuousStopReason({elapsed_ms: 300000}, limits), 'max_runtime');
  assert.equal(core.continuousStopReason({comment_count: 12, round_count: 2, elapsed_ms: 5000}, limits), null);
});

test('extension permissions are bounded to browser automation and localhost bridge', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../extension/manifest.json'), 'utf8'));
  assert.deepEqual(manifest.permissions, ['activeTab', 'tabs', 'scripting', 'downloads', 'alarms', 'storage']);
  assert.ok(manifest.host_permissions.every(value => /instagram\.com|amazon\.com|google\.com|bing\.com|127\.0\.0\.1/.test(value)));
  assert.equal(manifest.background.service_worker, 'service_worker.js');
  const pageScripts = ['capture_core.js', 'capture.js', 'continuous_capture.js', 'popup.js'].map(name => fs.readFileSync(path.join(__dirname, '../extension', name), 'utf8')).join('\n');
  assert.doesNotMatch(pageScripts, /document\.cookie|chrome\.cookies|chrome\.history|fetch\(|XMLHttpRequest|webRequest/);
});

test('popup exposes start and stop controls for continuous mode', () => {
  const html = fs.readFileSync(path.join(__dirname, '../extension/popup.html'), 'utf8');
  assert.match(html, /id="startContinuous"/);
  assert.match(html, /id="stopContinuous"/);
});

test('capture supports current time-based comment cards and never scrolls the whole page', () => {
  const script = fs.readFileSync(path.join(__dirname, '../extension/capture.js'), 'utf8');
  assert.match(script, /time\[datetime\]/);
  assert.match(script, /hasReplyAction/);
  assert.doesNotMatch(script, /window\.scrollBy/);
  assert.match(script, /target\.clientHeight \* 0\.85/);
  assert.doesNotMatch(script, /expected\.expected_shortcode \? \{media_id/);
  assert.match(script, /MEDIA_ID_MISMATCH/);
  assert.match(script, /parentElement\?\.closest\('li'\)/);
});

test('formal controller owns bounded per-post defaults', () => {
  const script = fs.readFileSync(path.join(__dirname, '../scripts/instagram_automation.py'), 'utf8');
  assert.match(script, /--max-scroll-rounds-per-post[\s\S]*default=200/);
  assert.match(script, /--scroll-delay-min-seconds[\s\S]*default=3\.0/);
  assert.match(script, /--scroll-delay-max-seconds[\s\S]*default=6\.0/);
  assert.match(script, /--navigation-delay-min-seconds[\s\S]*default=20\.0/);
  assert.match(script, /--navigation-delay-max-seconds[\s\S]*default=35\.0/);
});
