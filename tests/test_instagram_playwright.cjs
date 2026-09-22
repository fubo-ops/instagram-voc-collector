const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {
  mediaIdentity, alternateMediaUrl, validateSnapshot, connectWithRetry, parseArgs, navigationDelay, randomDelayMs,
} = require('../scripts/instagram_playwright_collector.cjs');

function snapshot(url, extra={}) {
  return {final_url:url, location_url:url, canonical_url:url, og_url:url,
    comment_urls:[], page_ready:true, visible_error_state:null, ...extra};
}

test('Post and Reel routes preserve discovery type and shortcode', () => {
  assert.deepEqual(mediaIdentity('https://www.instagram.com/p/Db_csWCPGfa/'), {
    shortcode:'Db_csWCPGfa', media_type:'p', url:'https://www.instagram.com/p/Db_csWCPGfa/'
  });
  assert.equal(mediaIdentity('https://www.instagram.com/reel/DcJ_mKcpsE8/').media_type, 'reel');
  assert.equal(alternateMediaUrl('https://www.instagram.com/reel/DcJ_mKcpsE8/'),
    'https://www.instagram.com/p/DcJ_mKcpsE8/');
});

test('same-shortcode alternate route can validate but never changes the shortcode', () => {
  const fallback='https://www.instagram.com/p/DcJ_mKcpsE8/';
  assert.equal(validateSnapshot(fallback, snapshot(fallback)).ok, true);
  assert.equal(mediaIdentity(fallback).shortcode, 'DcJ_mKcpsE8');
});

test('single-tab stale media A is rejected before media B capture', () => {
  const requested='https://www.instagram.com/p/BBBBBBBBBBB/';
  const stale=snapshot('https://www.instagram.com/p/AAAAAAAAAAA/');
  const result=validateSnapshot(requested, stale);
  assert.equal(result.ok, false);
  assert.equal(result.shortcode_mismatch, true);
});

test('comment permalink mismatch rejects the entire page identity', () => {
  const requested='https://www.instagram.com/reel/DcJ_mKcpsE8/';
  const result=validateSnapshot(requested, snapshot(requested, {
    comment_urls:['https://www.instagram.com/p/WRONGCODE1/c/123/']
  }));
  assert.equal(result.ok, false);
  assert.equal(result.shortcode_mismatch, true);
});

test('route redirects and visible error pages never start capture', () => {
  const requested='https://www.instagram.com/p/Db_csWCPGfa/';
  assert.equal(validateSnapshot(requested, snapshot('https://www.instagram.com/reel/Db_csWCPGfa/')).ok, false);
  assert.equal(validateSnapshot(requested, snapshot(requested, {visible_error_state:'unavailable'})).ok, false);
});

test('CDP connect retries once after a dropped connection', async () => {
  let attempts=0;
  const connected=await connectWithRetry(async url => {
    attempts++;
    if(attempts===1) throw Error('dropped');
    return {url};
  }, 'http://127.0.0.1:9333', 2);
  assert.equal(attempts, 2);
  assert.equal(connected.url, 'http://127.0.0.1:9333');
});

test('worker defaults to fixed CDP endpoint and has no extension dependency', () => {
  const args=parseArgs([]);
  assert.equal(args.cdpUrl, 'http://127.0.0.1:9333');
  assert.equal(args.navigationDelayMinMs, 20000);
  assert.equal(args.navigationDelayMaxMs, 35000);
  const source=fs.readFileSync(path.join(__dirname,'..','scripts','instagram_playwright_collector.cjs'),'utf8');
  assert.equal(source.includes('chrome.runtime'), false);
  assert.equal(source.includes('BridgeServer'), false);
});

test('Instagram navigations use a configurable random 20 to 35 second interval', () => {
  assert.equal(randomDelayMs(20000, 35000, () => 0), 20000);
  assert.equal(randomDelayMs(20000, 35000, () => 1), 35000);
  assert.equal(navigationDelay(1000, 4000, 20000), 17000);
  const configured=parseArgs(['--navigation-delay-min-ms', '22000', '--navigation-delay-max-ms', '30000']);
  assert.equal(configured.navigationDelayMinMs, 22000);
  assert.equal(configured.navigationDelayMaxMs, 30000);
  const source=fs.readFileSync(path.join(__dirname,'..','scripts','instagram_playwright_collector.cjs'),'utf8');
  assert.match(source, /response\?\.status\(\)===429/);
  assert.match(source, /assertNotRateLimited/);
});

test('CDP Reel preparation dedupes controls and excludes offscreen virtualized Reels', () => {
  const source=fs.readFileSync(path.join(__dirname,'..','scripts','instagram_playwright_collector.cjs'),'utf8');
  assert.match(source, /seen=new Set\(\)/);
  assert.match(source, /r\.bottom<=0\|\|r\.top>=innerHeight/);
  assert.match(source, /comment_icon_clicks:clicked\?1:0/);
});

test('post-cooldown preflight performs one homepage navigation with no reload loop', () => {
  const source=fs.readFileSync(path.join(__dirname,'..','scripts','instagram_playwright_collector.cjs'),'utf8');
  assert.match(source, /kind==="preflight"\)\{await this\.goto\("https:\/\/www\.instagram\.com\/"\);const state=await this\.loginState/);
  const preflight=source.match(/if\(kind==="preflight"\)\{[^}]+\}/)?.[0] || '';
  assert.equal((preflight.match(/this\.goto/g)||[]).length, 1);
  assert.equal(preflight.includes('reload'), false);
});

test('comment rounds and reply expansion randomize each wait from 3 to 6 seconds', () => {
  const source=fs.readFileSync(path.join(__dirname,'..','extension','continuous_capture.js'),'utf8');
  assert.match(source, /delay_min_ms = Math\.max\(3000/);
  assert.match(source, /delay_max_ms = Math\.max\(options\.delay_min_ms/);
  assert.match(source, /wait_ms: waitMs/);
  assert.match(source, /action: 'scroll_wait', wait_ms: scrollWaitMs/);
});
