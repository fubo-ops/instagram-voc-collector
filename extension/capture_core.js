/* Pure parsing helpers shared by the browser injection and offline tests. */
(function (root) {
  'use strict';
  function mediaIdentity(raw) {
    const url = new URL(raw);
    if (url.protocol !== 'https:' || !['www.instagram.com', 'instagram.com'].includes(url.hostname)) throw new Error('Open an HTTPS Instagram media URL');
    const match = url.pathname.match(/^\/(?:[A-Za-z0-9._]+\/)?(p|reels?)\/([A-Za-z0-9_-]+)(?:\/|$)/);
    if (!match) throw new Error('Open a /p/ or /reel/ page first');
    const kind = match[1] === 'p' ? 'p' : 'reel';
    return { media_id: match[2], media_type: kind, media_url: `https://www.instagram.com/${kind}/${match[2]}/` };
  }
  function mediaIdentityOrNull(raw) {
    try { return mediaIdentity(raw); } catch (_) { return null; }
  }
  function alternateMediaUrl(raw) {
    const identity = mediaIdentity(raw);
    const kind = identity.media_type === 'reel' ? 'p' : 'reel';
    return `https://www.instagram.com/${kind}/${identity.media_id}/`;
  }
  function routeEvidence({requested_url, final_url, location_url, canonical_url, og_url, comment_urls,
    error_state, page_ready} = {}) {
    const requested = mediaIdentityOrNull(requested_url);
    const final = mediaIdentityOrNull(final_url);
    const location = mediaIdentityOrNull(location_url);
    const canonical = mediaIdentityOrNull(canonical_url);
    const og = mediaIdentityOrNull(og_url);
    const comments = (comment_urls || []).map(mediaIdentityOrNull).filter(Boolean);
    const rendered = location || final;
    const shortcodeMismatch = !requested || !rendered || rendered.media_id !== requested.media_id ||
      [canonical, og, ...comments].filter(Boolean).some(item => item.media_id !== requested.media_id);
    const routeMismatch = !requested || !final || !location || final.media_type !== requested.media_type ||
      location.media_type !== requested.media_type;
    const conflicts = [];
    for (const [source, item] of [['canonical', canonical], ['og:url', og]]) {
      if (item && rendered && item.media_type !== rendered.media_type) conflicts.push(`${source}_route_type`);
    }
    return {ok: Boolean(page_ready) && !error_state && !shortcodeMismatch && !routeMismatch,
      requested_shortcode: requested?.media_id || null, requested_media_type: requested?.media_type || null,
      actual_shortcode: rendered?.media_id || null, rendered_media_type: rendered?.media_type || null,
      shortcode_mismatch: shortcodeMismatch, route_mismatch: routeMismatch,
      evidence_conflicts: conflicts, error_state: error_state || null};
  }
  function verifyMediaIdentity({location_url, canonical_url, og_url, comment_urls, expected_shortcode} = {}) {
    const sources = [];
    const add = (source, raw) => { const identity = mediaIdentityOrNull(raw); if (identity) sources.push({source, ...identity}); };
    add('location', location_url);
    add('canonical', canonical_url);
    add('og:url', og_url);
    for (const raw of comment_urls || []) add('comment_permalink', raw);
    const locationIdentity = sources.find(item => item.source === 'location') || null;
    const metadata = sources.filter(item => item.source === 'canonical' || item.source === 'og:url');
    const comments = sources.filter(item => item.source === 'comment_permalink');
    const observed = [...new Set(sources.map(item => item.media_id))];
    const actual = locationIdentity?.media_id || null;
    const corroborated = metadata.length > 0 || comments.length > 0;
    const mismatch = !actual || metadata.some(item => item.media_id !== actual) ||
      comments.some(item => item.media_id !== actual) || (expected_shortcode && expected_shortcode !== actual);
    return {ok: !mismatch && corroborated, actual_shortcode: actual, expected_shortcode: expected_shortcode || null,
      observed_shortcodes: observed, sources, mismatch_count: mismatch ? 1 : 0,
      reason: mismatch ? 'MEDIA_ID_MISMATCH' : corroborated ? 'verified' : 'identity_evidence_not_ready'};
  }
  function commentId(raw) {
    if (!raw) return null;
    try {
      const url = new URL(raw, 'https://www.instagram.com');
      const pathId = url.pathname.match(/\/c\/(\d+)(?:\/|$)/);
      return (pathId && pathId[1]) || url.searchParams.get('comment_id') || url.searchParams.get('commentId') || null;
    } catch (_) { return null; }
  }
  function restriction(text) {
    const value = String(text || '').toLowerCase();
    if (/captcha|confirm you.?re human|验证码/.test(value)) return 'captcha';
    if (/try again later|too many requests|rate limit|操作过于频繁/.test(value)) return 'rate_limited';
    if (/this account is private|此帐号为私密帐号/.test(value)) return 'private';
    if (/sorry, this page isn.?t available|page not available|页面不可用/.test(value)) return 'unavailable';
    if (/log in to instagram|登录 instagram 以继续/.test(value)) return 'login_required';
    return 'ready';
  }
  function isExpansionLabel(text) {
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    return /^(?:load|show|view)\s+(?:all\s+)?(?:previous\s+)?(?:\d+\s+)?(?:more\s+)?comments?(?:\s*\(\d+\))?$|^(?:view|show|load)\s+(?:all\s+)?(?:\d+\s+)?(?:more\s+)?repl(?:y|ies)(?:\s*\(\d+\))?$|^(?:加载|显示|查看)(?:全部|所有|更多|之前的)?\s*\d*\s*条?(?:更多)?评论$|^查看更多评论$|^(?:查看|显示|加载)(?:全部|所有|更多)?\s*\d*\s*条?(?:更多)?回复$/i.test(value);
  }
  function combinedLabel(parts) {
    const values = Array.isArray(parts) ? parts : (parts && typeof parts === 'object' ? Object.values(parts) : [parts]);
    return [...new Set(values.map(value => String(value || '').replace(/\s+/g, ' ').trim()).filter(Boolean))].join(' | ');
  }
  function isExpansionControl(parts) {
    return combinedLabel(parts).split(' | ').some(isExpansionLabel);
  }
  function isCommentTriggerLabel(text) {
    return /^(?:comment|comments|open comments|view comments|评论|打开评论|查看评论)$/i.test(String(text || '').replace(/\s+/g, ' ').trim());
  }
  function isEmptyCommentsLabel(text) {
    return /(?:no comments yet|be the first to comment|comments are turned off|暂无评论|还没有评论|成为第一个评论的人|评论已关闭)/i.test(String(text || ''));
  }
  function shouldCountIdle(metrics) {
    metrics = metrics || {};
    return Boolean(metrics.ready) && !metrics.added && !metrics.actions && !metrics.frontier_changed &&
      !metrics.pending_controls && !metrics.loading;
  }
  function visibleExhausted(state) {
    state = state || {};
    if (!state.ready || state.loading || state.pending_controls) return false;
    if (state.empty) return true;
    const frontier = state.frontier || {};
    return Boolean(state.node_count) && Number(frontier.scroll_height) > 0 &&
      Number(frontier.scroll_top) + Number(frontier.client_height) >= Number(frontier.scroll_height) - 8;
  }
  function continuousStopReason(metrics, limits) {
    metrics = metrics || {};
    limits = limits || {};
    if (limits.target_comments > 0 && metrics.comment_count >= limits.target_comments) return 'target_comments';
    if (limits.max_rounds > 0 && metrics.round_count >= limits.max_rounds) return 'max_rounds';
    if (limits.max_expand_actions > 0 && metrics.expand_action_count >= limits.max_expand_actions) return 'max_expand_actions';
    if (limits.idle_rounds > 0 && metrics.idle_round_count >= limits.idle_rounds) return 'idle_rounds';
    if (limits.max_runtime_ms > 0 && metrics.elapsed_ms >= limits.max_runtime_ms) return 'max_runtime';
    return null;
  }
  function visibleCount(raw) {
    const text = String(raw || '').replace(/\u00a0/g, ' ');
    const match = text.match(/([\d,.]+(?:\.\d+)?)\s*(万|k|m)?\s*(?:comments?|条评论|评论)/i) ||
      text.match(/(?:comments?|条评论|评论)\s*([\d,.]+(?:\.\d+)?)\s*(万|k|m)?/i) ||
      text.trim().match(/^([\d,.]+(?:\.\d+)?)\s*(万|k|m)?$/i);
    if (!match) return null;
    const number = Number(String(match[1]).replace(/,/g, ''));
    if (!Number.isFinite(number)) return null;
    const multiplier = match[2] === '万' ? 10000 : String(match[2] || '').toLowerCase() === 'k' ? 1000 :
      String(match[2] || '').toLowerCase() === 'm' ? 1000000 : 1;
    return Math.round(number * multiplier);
  }
  const api = { mediaIdentity, mediaIdentityOrNull, alternateMediaUrl, routeEvidence, verifyMediaIdentity, commentId, restriction, isExpansionLabel, combinedLabel, isExpansionControl,
    isCommentTriggerLabel, isEmptyCommentsLabel, shouldCountIdle, visibleExhausted, continuousStopReason, visibleCount };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InstagramVocCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
