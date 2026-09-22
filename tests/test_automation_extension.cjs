const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const extension = name => path.join(__dirname, '../extension', name);

test('manifest enables passive localhost jobs and automatic Instagram navigation', () => {
  const manifest = JSON.parse(fs.readFileSync(extension('manifest.json'), 'utf8'));
  assert.ok(manifest.permissions.includes('tabs'));
  assert.ok(manifest.permissions.includes('alarms'));
  assert.ok(manifest.permissions.includes('storage'));
  assert.ok(manifest.host_permissions.includes('https://www.instagram.com/*'));
  assert.ok(manifest.host_permissions.includes('http://127.0.0.1/*'));
  assert.equal(manifest.content_scripts[0].js[0], 'wake.js');
});

test('bridge helpers canonicalize and dedupe post and reel URLs', () => {
  const core = require(extension('bridge_core.js'));
  assert.deepEqual(core.canonicalMedia('https://instagram.com/p/ABC_1/?x=1'), {
    shortcode: 'ABC_1', media_type: 'p', url: 'https://www.instagram.com/p/ABC_1/', owner: null
  });
  assert.deepEqual(core.canonicalMedia('https://www.instagram.com/reels/R_2/'), {
    shortcode: 'R_2', media_type: 'reel', url: 'https://www.instagram.com/reel/R_2/', owner: null
  });
  assert.deepEqual(core.canonicalMedia('https://www.instagram.com/outwardhound/reel/R_3/'), {
    shortcode: 'R_3', media_type: 'reel', url: 'https://www.instagram.com/reel/R_3/', owner: 'outwardhound'
  });
  assert.deepEqual(core.extractMediaCandidates([
    'https://www.instagram.com/p/ABC_1/',
    'https://instagram.com/p/ABC_1/?x=2',
    'https://www.instagram.com/reel/R_2/'
  ]).map(x => x.shortcode), ['ABC_1', 'R_2']);
});

test('service worker polls only localhost and reloads only for a changed source version', () => {
  const script = fs.readFileSync(extension('service_worker.js'), 'utf8');
  assert.match(script, /http:\/\/127\.0\.0\.1:8765/);
  assert.match(script, /bridge\('\/v1\/next'/);
  assert.match(script, /chrome\.alarms/);
  assert.match(script, /collect_media/);
  assert.match(script, /\/v1\/extension\/hello/);
  assert.match(script, /chrome\.runtime\.getManifest\(\)\.version/);
  assert.match(script, /reloadIfSourceVersionChanged/);
  assert.match(script, /source\.version !== loaded/);
  assert.match(script, /chrome\.runtime\.reload\(\)/);
  assert.doesNotMatch(script, /graphql|api\/v1|web_profile_info|document\.cookie|chrome\.cookies/);
});

test('automatic collector defaults cover multiple replies and bounded container scrolling', () => {
  const script = fs.readFileSync(extension('continuous_capture.js'), 'utf8');
  assert.match(script, /max_expand_passes_per_round/);
  assert.match(script, /expand_replies/);
  assert.match(script, /auto_scroll/);
  assert.doesNotMatch(script, /window\.scrollBy/);
  assert.doesNotMatch(script, /const clicked = new WeakSet/);
  assert.match(script, /expanded_thread_signatures/);
  assert.match(script, /comment_container_ready/);
});

test('media jobs activate rendered tabs and prepare Post or Reel comment surfaces', () => {
  const worker = fs.readFileSync(extension('service_worker.js'), 'utf8');
  const prepare = fs.readFileSync(extension('media_prepare.js'), 'utf8');
  const capture = fs.readFileSync(extension('capture.js'), 'utf8');
  assert.match(worker, /active: true/);
  assert.match(worker, /reusableMediaTab/);
  assert.match(worker, /instagramVocMediaTabId/);
  assert.match(worker, /waitForMediaLoad/);
  assert.match(worker, /clickExistingMediaAnchor/);
  assert.match(worker, /instagram_media_route_error_page/);
  assert.match(worker, /core\.alternateMediaUrl/);
  assert.match(worker, /route_fallback_attempt_count/);
  assert.match(worker, /method: 'reload'/);
  assert.match(worker, /previous_url/);
  assert.doesNotMatch(worker, /site:instagram\.com/);
  assert.match(worker, /MEDIA_ID_MISMATCH_NAVIGATION/);
  assert.match(worker, /collect_media_completion_timeout/);
  assert.match(worker, /chrome\.tabs\.onRemoved/);
  assert.match(worker, /media_prepare\.js/);
  assert.match(worker, /if \(!prepared\?\.ready\)/);
  assert.match(worker, /chrome\.tabs\.reload/);
  assert.match(worker, /if \(owned\) await chrome\.tabs\.remove/);
  assert.match(prepare, /isCommentTriggerLabel/);
  assert.match(prepare, /commentState\(\)\.ready/);
  assert.match(capture, /svg\[aria-label\]/);
  assert.match(capture, /last_comment_scrollIntoView/);
  assert.match(capture, /comment_container_scroll/);
  assert.match(capture, /\[aria-busy="true"\]/);
});

test('controller retries zero or partial checkpoints instead of permanently skipping them', () => {
  const script = fs.readFileSync(path.join(__dirname, '../scripts/instagram_automation.py'), 'utf8');
  assert.match(script, /media_result_needs_retry/);
  assert.match(script, /--retry-partial/);
  assert.match(script, /--force-reaudit/);
  assert.match(script, /expanded_thread_signatures/);
  assert.match(script, /scroll_frontier/);
});
