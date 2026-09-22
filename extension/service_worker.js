'use strict';
importScripts('capture_core.js');
const core = globalThis.InstagramVocCore;
const BRIDGE = 'http://127.0.0.1:8765';
const states = new Map();
const completionWaiters = new Map();
let polling = false;
let lastHelloAt = 0;

async function reloadIfSourceVersionChanged() {
  const loaded = chrome.runtime.getManifest().version;
  try {
    const response = await fetch(`${chrome.runtime.getURL('manifest.json')}?source=${Date.now()}`, {cache: 'no-store'});
    const source = await response.json();
    if (source.version && source.version !== loaded) {
      chrome.runtime.reload();
      return true;
    }
  } catch (_) {
    // Keep the loaded build running when its local source cannot be inspected.
  }
  return false;
}

async function bridge(path, options = {}) {
  const response = await fetch(`${BRIDGE}${path}`, {cache: 'no-store', ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})}});
  if (!response.ok) throw new Error(`bridge_http_${response.status}`);
  return response.json();
}
async function report(path, body) { return bridge(path, {method: 'POST', body: JSON.stringify(body)}); }
async function hello(force = false) {
  if (!force && Date.now() - lastHelloAt < 10000) return;
  await report('/v1/extension/hello', {extension_id: chrome.runtime.id,
    version: chrome.runtime.getManifest().version, worker_started_at: new Date().toISOString()});
  lastHelloAt = Date.now();
}
function waitForLoad(tabId, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { chrome.tabs.onUpdated.removeListener(listener); reject(new Error('tab_load_timeout')); }, timeoutMs);
    const listener = (id, info) => {
      if (id === tabId && info.status === 'complete') { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); resolve(); }
    };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then(tab => { if (tab.status === 'complete') { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); resolve(); } });
  });
}
function mediaPath(value) {
  const identity = core.mediaIdentityOrNull(value);
  return identity ? `/${identity.media_type}/${identity.media_id}/` : null;
}
async function routeSnapshot(tabId) {
  const tab = await chrome.tabs.get(tabId);
  try {
    const rows = await chrome.scripting.executeScript({target: {tabId}, func: () => {
      const text = String(document.body?.innerText || '').slice(0, 6000);
      const lower = text.toLowerCase();
      const visibleError = /http error 429|网页无法正常运作|too many requests|try again later|操作过于频繁/.test(lower) ? 'rate_limited' :
        /sorry, this page isn.?t available|page not available|页面不可用/.test(lower) ? 'unavailable' : null;
      return {location_url: location.href, location_pathname: location.pathname,
        canonical_url: document.querySelector('link[rel="canonical"]')?.href || null,
        og_url: document.querySelector('meta[property="og:url"]')?.content || null,
        comment_urls: [...document.querySelectorAll('a[href*="/c/"],a[href*="comment_id"]')].map(a => a.href).slice(0, 200),
        document_ready_state: document.readyState, page_ready: Boolean(document.querySelector('main, article, [role="dialog"]')),
        visible_error_state: visibleError};
    }});
    return {tab_id: tabId, final_url: tab.url, tab_status: tab.status, ...(rows[0]?.result || {})};
  } catch (error) {
    return {tab_id: tabId, final_url: tab.url, tab_status: tab.status, location_url: tab.url,
      location_pathname: null, canonical_url: null, og_url: null, comment_urls: [], page_ready: false,
      visible_error_state: 'browser_error_page', injection_error: String(error?.message || error)};
  }
}
async function waitForMediaLoad(tabId, url, previousUrl = null, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === 'complete') {
      const snapshot = await routeSnapshot(tabId);
      const evidence = core.routeEvidence({requested_url: url, final_url: snapshot.final_url,
        location_url: snapshot.location_url, canonical_url: snapshot.canonical_url, og_url: snapshot.og_url,
        comment_urls: snapshot.comment_urls, error_state: snapshot.visible_error_state, page_ready: snapshot.page_ready});
      last = {...snapshot, ...evidence};
      if (evidence.ok) return last;
      if (snapshot.visible_error_state || evidence.shortcode_mismatch || evidence.route_mismatch) return last;
    }
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  return last || {...await routeSnapshot(tabId), error_state: 'navigation_timeout', ok: false};
}
async function clickExistingMediaAnchor(tabId, url) {
  const rows = await chrome.scripting.executeScript({target: {tabId}, func: target => {
    const parse = value => {
      try {
        const match = new URL(value).pathname.match(/^\/(?:[A-Za-z0-9._]+\/)?(p|reels?)\/([A-Za-z0-9_-]+)(?:\/|$)/);
        return match ? `/media/${match[2]}/` : null;
      } catch (_) { return null; }
    };
    const wanted = parse(target);
    const existing = [...document.querySelectorAll('a[href]')].find(node => parse(node.href) === wanted);
    if (!existing) return false;
    existing.removeAttribute('target'); existing.target = '_self'; existing.click(); return true;
  }, args: [url]});
  return Boolean(rows[0]?.result);
}
async function reusableMediaTab(url, navigationSourceUrl = null) {
  const saved = await chrome.storage.local.get('instagramVocMediaTabId');
  const active = await chrome.tabs.query({active: true, currentWindow: true, url: 'https://www.instagram.com/*'});
  const instagram = await chrome.tabs.query({url: 'https://www.instagram.com/*'});
  const savedTab = saved.instagramVocMediaTabId ? await chrome.tabs.get(saved.instagramVocMediaTabId).catch(() => null) : null;
  const ordered = [savedTab, ...active, ...instagram].filter(Boolean);
  let tab = ordered[0] || null;
  if (!tab) {
    tab = await chrome.tabs.create({url: 'https://www.instagram.com/', active: true});
    await waitForLoad(tab.id);
  }
  await chrome.storage.local.set({instagramVocMediaTabId: tab.id});
  await chrome.tabs.update(tab.id, {active: true});
  const previousUrl = tab.url || null;
  const navigationStartedAt = new Date().toISOString();
  const attempts = [];
  const routes = [url, core.alternateMediaUrl(url)];
  for (let routeIndex = 0; routeIndex < routes.length; routeIndex++) {
    const requested = routes[routeIndex];
    const currentSnapshot = await routeSnapshot(tab.id);
    const currentEvidence = core.routeEvidence({requested_url: requested, final_url: currentSnapshot.final_url,
      location_url: currentSnapshot.location_url, canonical_url: currentSnapshot.canonical_url,
      og_url: currentSnapshot.og_url, comment_urls: currentSnapshot.comment_urls,
      error_state: currentSnapshot.visible_error_state, page_ready: currentSnapshot.page_ready});
    if (currentEvidence.ok) {
      const evidence = {...currentSnapshot, ...currentEvidence};
      attempts.push({requested_url: requested, requested_media_type: core.mediaIdentity(requested).media_type,
        method: 'current_page', refreshed: false, ...evidence, completed_at: new Date().toISOString()});
      return {tab, navigation: {tab_id: tab.id, previous_url: previousUrl,
        discovery_url: url, requested_url: requested, requested_media_type: core.mediaIdentity(requested).media_type,
        rendered_url: evidence.location_url, rendered_media_type: evidence.rendered_media_type,
        working_media_url: requested, navigation_started_at: navigationStartedAt,
        navigation_completed_at: new Date().toISOString(), route_fallback_attempt_count: routeIndex,
        route_fallback_success_count: routeIndex ? 1 : 0, attempts}};
    }
    let clicked = false;
    if (routeIndex === 0) {
      clicked = await clickExistingMediaAnchor(tab.id, requested).catch(() => false);
      const sourceHost = (() => { try { return new URL(navigationSourceUrl).hostname; } catch (_) { return ''; } })();
      if (!clicked && navigationSourceUrl && !mediaPath(navigationSourceUrl) &&
          ['www.instagram.com', 'www.google.com', 'www.bing.com'].includes(sourceHost)) {
        await chrome.tabs.update(tab.id, {url: navigationSourceUrl, active: true});
        await waitForLoad(tab.id).catch(() => {}); await new Promise(resolve => setTimeout(resolve, 1500));
        clicked = await clickExistingMediaAnchor(tab.id, requested).catch(() => false);
      }
    }
    if (!clicked) await chrome.tabs.update(tab.id, {url: requested, active: true});
    let evidence = await waitForMediaLoad(tab.id, requested, previousUrl);
    attempts.push({requested_url: requested, requested_media_type: core.mediaIdentity(requested).media_type,
      method: clicked ? 'existing_anchor' : 'tabs_update', refreshed: false, ...evidence,
      completed_at: new Date().toISOString()});
    if (evidence.ok) return {tab, navigation: {tab_id: tab.id, previous_url: previousUrl,
      discovery_url: url, requested_url: requested, requested_media_type: core.mediaIdentity(requested).media_type,
      rendered_url: evidence.location_url, rendered_media_type: evidence.rendered_media_type,
      working_media_url: requested, navigation_started_at: navigationStartedAt,
      navigation_completed_at: new Date().toISOString(), route_fallback_attempt_count: routeIndex,
      route_fallback_success_count: routeIndex ? 1 : 0, attempts}};
    if (evidence.route_mismatch && !evidence.shortcode_mismatch && !evidence.error_state) continue;
    await chrome.tabs.reload(tab.id).catch(() => {});
    await waitForLoad(tab.id).catch(() => {});
    evidence = await waitForMediaLoad(tab.id, requested, previousUrl, 15000);
    attempts.push({requested_url: requested, requested_media_type: core.mediaIdentity(requested).media_type,
      method: 'reload', refreshed: true, ...evidence, completed_at: new Date().toISOString()});
    if (evidence.ok) return {tab, navigation: {tab_id: tab.id, previous_url: previousUrl,
      discovery_url: url, requested_url: requested, requested_media_type: core.mediaIdentity(requested).media_type,
      rendered_url: evidence.location_url, rendered_media_type: evidence.rendered_media_type,
      working_media_url: requested, navigation_started_at: navigationStartedAt,
      navigation_completed_at: new Date().toISOString(), route_fallback_attempt_count: routeIndex,
      route_fallback_success_count: routeIndex ? 1 : 0, attempts}};
    if (evidence.shortcode_mismatch) break;
  }
  const last = attempts[attempts.length - 1] || {};
  const error = new Error(last.shortcode_mismatch ? 'MEDIA_ID_MISMATCH_NAVIGATION' : 'instagram_media_route_error_page');
  error.navigation = {tab_id: tab.id, previous_url: previousUrl, discovery_url: url,
    requested_url: last.requested_url || url, requested_media_type: last.requested_media_type || null,
    rendered_url: last.location_url || last.final_url || null, rendered_media_type: last.rendered_media_type || null,
    working_media_url: null, navigation_started_at: navigationStartedAt, navigation_completed_at: new Date().toISOString(),
    route_fallback_attempt_count: Math.max(0, routes.length - 1), route_fallback_success_count: 0, attempts};
  throw error;
}
async function jobTab(url, reuse = false, navigationSourceUrl = null) {
  const active = await chrome.tabs.query({active: true, currentWindow: true});
  const previousActiveId = active[0]?.id;
  if (reuse) {
    const tabs = await chrome.tabs.query({url: ['https://www.instagram.com/p/*', 'https://www.instagram.com/reel/*', 'https://www.instagram.com/reels/*']});
    const mediaKey = value => new URL(value).pathname.replace(/^\/reels\//, '/reel/');
    const found = tabs.find(tab => tab.url && mediaKey(tab.url) === mediaKey(url));
    if (found) { await chrome.tabs.update(found.id, {url, active: true}); await waitForLoad(found.id); return {tabId: found.id, owned: false, previousActiveId}; }
  }
  const mediaUrl = Boolean(mediaPath(url));
  const reusable = mediaUrl ? await reusableMediaTab(url, navigationSourceUrl) : null;
  const tab = mediaUrl ? reusable.tab : await chrome.tabs.create({url, active: true});
  if (!mediaUrl) await waitForLoad(tab.id);
  return {tabId: tab.id, owned: !mediaUrl, previousActiveId, navigation: reusable?.navigation || null};
}
async function injectResult(tabId, files) {
  let rows;
  try { rows = await chrome.scripting.executeScript({target: {tabId}, files}); }
  catch (error) {
    if (!/showing error page|No frame with id|Frame with ID/i.test(String(error?.message || error))) throw error;
    throw new Error('instagram_media_route_error_page');
  }
  return rows.length ? rows[rows.length - 1].result : null;
}
async function prepareCollectionTab(tabId, job) {
  const initialize = () => chrome.scripting.executeScript({target: {tabId}, func: (value, jobId) => {
      globalThis.__IG_VOC_OPTIONS__ = value;
      globalThis.__IG_VOC_JOB_ID__ = jobId;
      globalThis.__IG_VOC_STOP_REQUESTED__ = false;
    }, args: [job.payload.options || {}, job.job_id]});
  try { await initialize(); }
  catch (error) {
    if (!/showing error page|No frame with id|Frame with ID/i.test(String(error?.message || error))) throw error;
    throw new Error('instagram_media_route_error_page');
  }
  await chrome.scripting.executeScript({target: {tabId}, func: async () => {
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline && (document.readyState !== 'complete' || !document.querySelector('main, article, [role="dialog"]'))) {
      await new Promise(resolve => setTimeout(resolve, 300));
    }
  }});
  await chrome.scripting.executeScript({target: {tabId}, files: ['capture_core.js', 'capture.js']});
  return injectResult(tabId, ['media_prepare.js']);
}
async function collectMedia(job) {
  const context = await jobTab(job.payload.url, false, job.payload.navigation_source_url);
  const {tabId, owned, previousActiveId, navigation} = context;
  if (navigation?.working_media_url) {
    job.payload.options = {...(job.payload.options || {}), expected_media_type: navigation.requested_media_type,
      expected_media_url: navigation.working_media_url};
  }
  states.set(tabId, {phase: 'running', job_id: job.job_id, shortcode: job.payload.shortcode});
  try {
    let prepared = await prepareCollectionTab(tabId, job);
    if (!prepared?.ready) {
      await chrome.tabs.reload(tabId); await waitForLoad(tabId);
      prepared = await prepareCollectionTab(tabId, job);
    }
    await report('/v1/progress', {job_id: job.job_id, progress: {comment_prepare: prepared}}).catch(() => {});
    if (previousActiveId && previousActiveId !== tabId) await chrome.tabs.update(previousActiveId, {active: true}).catch(() => {});
    const timeoutMs = Number(job.payload.options?.max_runtime_ms || 120000) + 60000;
    const resultPromise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        completionWaiters.delete(job.job_id);
        reject(new Error('collect_media_completion_timeout'));
      }, timeoutMs);
      completionWaiters.set(job.job_id, {resolve, reject, tabId, owned, timer, navigation});
    });
    await chrome.scripting.executeScript({target: {tabId}, files: ['continuous_capture.js']});
    return resultPromise;
  } catch (error) {
    completionWaiters.delete(job.job_id); states.delete(tabId);
    if (owned) await chrome.tabs.remove(tabId).catch(() => {});
    if (previousActiveId && previousActiveId !== tabId) await chrome.tabs.update(previousActiveId, {active: true}).catch(() => {});
    throw error;
  }
}
async function executeJob(job) {
  if (job.kind === 'collect_media') return collectMedia(job);
  let context;
  try { context = await jobTab(job.payload.url, false, job.payload.navigation_source_url); }
  catch (error) {
    if (job.kind === 'inspect_media' && error.navigation) return {identity_verified: false,
      stop_reason: error.message, navigation: error.navigation,
      requested_url: error.navigation.requested_url, requested_media_type: error.navigation.requested_media_type,
      rendered_url: error.navigation.rendered_url, rendered_media_type: error.navigation.rendered_media_type,
      working_media_url: null, route_fallback_attempt_count: error.navigation.route_fallback_attempt_count || 0,
      route_fallback_success_count: 0};
    throw error;
  }
  const {tabId, owned, navigation} = context;
  try {
    if (job.kind === 'discover_query') {
      await chrome.scripting.executeScript({target: {tabId}, func: payload => { globalThis.__IG_DISCOVERY_JOB__ = payload; }, args: [job.payload]});
      return await injectResult(tabId, ['bridge_core.js', 'discovery_capture.js']);
    }
    if (job.kind === 'inspect_media') return {...await injectResult(tabId, ['capture_core.js', 'media_metadata.js']),
      navigation, requested_url: navigation?.requested_url, requested_media_type: navigation?.requested_media_type,
      rendered_url: navigation?.rendered_url, rendered_media_type: navigation?.rendered_media_type,
      working_media_url: navigation?.working_media_url,
      route_fallback_attempt_count: navigation?.route_fallback_attempt_count || 0,
      route_fallback_success_count: navigation?.route_fallback_success_count || 0};
    if (job.kind === 'inspect_product') return await injectResult(tabId, ['amazon_capture.js']);
    throw new Error(`unsupported_job:${job.kind}`);
  } finally { if (owned) await chrome.tabs.remove(tabId).catch(() => {}); }
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    if (await reloadIfSourceVersionChanged()) return;
    await hello();
    const next = await bridge('/v1/next');
    if (next.job) {
      try {
        const result = await executeJob(next.job);
        if (next.job.kind !== 'collect_media') await report('/v1/result', {job_id: next.job.job_id, result});
      } catch (error) { await report('/v1/error', {job_id: next.job.job_id, error: error.message,
        navigation: error.navigation || null}); }
    }
  } catch (_) {
    // Local controller is optional; remain quiet while it is offline.
  } finally { polling = false; setTimeout(poll, 1500); }
}
chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create('instagram-voc-poll', {periodInMinutes: 0.5}); void poll(); });
chrome.runtime.onStartup.addListener(() => { chrome.alarms.create('instagram-voc-poll', {periodInMinutes: 0.5}); void poll(); });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'instagram-voc-poll') void poll(); });
chrome.tabs.onRemoved.addListener(tabId => {
  for (const [jobId, waiter] of completionWaiters) {
    if (waiter.tabId !== tabId) continue;
    clearTimeout(waiter.timer); completionWaiters.delete(jobId);
    waiter.reject(new Error('collection_tab_closed'));
  }
});
void poll();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab && sender.tab.id;
  if (message.action === 'wake') {
    hello(true).catch(() => {}); void poll();
    sendResponse({ok: true, extension_id: chrome.runtime.id, version: chrome.runtime.getManifest().version}); return;
  }
  if (message.action === 'continuous_progress') {
    const jobId = message.job_id || (tabId != null && states.get(tabId)?.job_id);
    if (tabId != null) states.set(tabId, {phase: 'running', job_id: jobId, ...message.progress});
    if (jobId) report('/v1/progress', {job_id: jobId, progress: message.progress}).catch(() => {});
    sendResponse({ok: true}); return;
  }
  if (message.action === 'continuous_complete') {
      const capture = message.capture;
    const jobId = message.job_id || (tabId != null && states.get(tabId)?.job_id);
    if (jobId && !String(jobId).startsWith('manual-')) {
      const waiter = completionWaiters.get(jobId);
      if (waiter) { clearTimeout(waiter.timer); completionWaiters.delete(jobId); }
      if (waiter?.navigation) {
        capture.navigation = waiter.navigation;
        capture.media = {...(capture.media || {}), working_media_url: waiter.navigation.working_media_url,
          rendered_media_type: waiter.navigation.rendered_media_type, rendered_url: waiter.navigation.rendered_url};
      }
      report('/v1/result', {job_id: jobId, result: capture})
        .then(() => { if (waiter) waiter.resolve(capture); })
        .catch(error => { if (waiter) waiter.reject(error); })
        .finally(() => { if (waiter?.owned) chrome.tabs.remove(waiter.tabId).catch(() => {}); });
      sendResponse({ok: true}); return true;
    }
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const filename = `instagram-voc/${capture.media.media_id}-continuous-${stamp}.json`;
    const data = 'data:application/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(capture, null, 2));
    chrome.downloads.download({url: data, filename, saveAs: false}).then(() => sendResponse({ok: true}));
    return true;
  }
  if (message.action === 'start_continuous') {
    const job = {job_id: `manual-${Date.now()}`, kind: 'collect_media', payload: {url: message.url, options: message.options || {}}};
    collectMedia(job).catch(() => {}); sendResponse({ok: true}); return;
  }
  if (message.action === 'stop_continuous') {
    chrome.scripting.executeScript({target: {tabId: message.tabId}, func: () => { globalThis.__IG_VOC_STOP_REQUESTED__ = true; }})
      .then(() => sendResponse({ok: true})).catch(error => sendResponse({ok: false, error: error.message})); return true;
  }
  if (message.action === 'get_status') sendResponse({ok: true, state: states.get(message.tabId) || {phase: 'idle'}});
});
