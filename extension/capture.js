/* Injected by the passive controller, or by popup debug mode, into one media tab. */
(() => {
  'use strict';
  const core = globalThis.InstagramVocCore;
  if (!core) throw new Error('Capture core did not load');
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const clickable = el => {
    let node = el;
    for (let depth = 0; node && node !== document.body && depth < 5; depth += 1, node = node.parentElement) {
      if (node.matches?.('button, [role="button"], a[href]')) return node;
    }
    return el;
  };
  const controlParts = el => {
    const svg = el.matches?.('svg') ? el : el.querySelector?.('svg[aria-label], svg[title]');
    return [clean(el.textContent), el.getAttribute?.('aria-label'), el.getAttribute?.('title'),
      svg?.getAttribute('aria-label'), svg?.getAttribute('title')];
  };
  const isProfileLink = link => {
    try { return /^\/[A-Za-z0-9._]+\/$/.test(new URL(link.href).pathname); }
    catch (_) { return false; }
  };
  const hasReplyAction = node => [...node.querySelectorAll('button, [role="button"]')]
    .some(button => /^(reply|回复)$/i.test(clean(button.textContent)));
  function commentNodes() {
    const derived = [];
    for (const time of document.querySelectorAll('time[datetime]')) {
      const permalink = time.closest('a[href]');
      if (!permalink || !core.commentId(permalink.href)) continue;
      let node = time.parentElement;
      for (let depth = 0; node && node !== document.body && depth < 12; depth += 1, node = node.parentElement) {
        if (hasReplyAction(node) && [...node.querySelectorAll('a[href]')].some(link => isProfileLink(link) && clean(link.textContent))) {
          derived.push(node);
          break;
        }
      }
    }
    if (derived.length) return [...new Set(derived)].filter(visible);
    return [...new Set(document.querySelectorAll('article ul li, [role="dialog"] ul li'))].filter(visible);
  }
  const ownText = (node, author, time) => {
    const ignored = new Set([...node.querySelectorAll('button, [role="button"]')]
      .map(el => clean(el.textContent)).filter(Boolean));
    if (time) ignored.add(clean(time.textContent));
    const lines = String(node.innerText || '').split(/\r?\n/).map(clean).filter(Boolean);
    const content = lines.filter(value => {
      const withoutVerified = value.replace(/已验证|verified/ig, '').trim();
      return withoutVerified !== author && !ignored.has(value) &&
        !/^(reply|回复|like|赞|查看翻译|see translation)$/i.test(value) &&
        !/^\d[\d,.]*\s*(?:likes?|次赞)$/i.test(value);
    });
    return content.join(' ').trim() || null;
  };
  function expansionControls() {
    const controls = [];
    const seen = new Set();
    for (const raw of document.querySelectorAll('button, [role="button"], [aria-label], [title], svg[aria-label], svg[title]')) {
      if (!core.isExpansionControl(controlParts(raw))) continue;
      const el = clickable(raw);
      if (!seen.has(el) && visible(el)) { seen.add(el); controls.push(el); }
    }
    return controls;
  }
  const controlLabel = el => core.combinedLabel(controlParts(el));
  const controlSignature = el => {
    const container = el.closest('li, ul, article, [role="dialog"]');
    const anchor = container?.querySelector('a[href*="/c/"], a[href*="comment_id"]');
    return core.combinedLabel([controlLabel(el), core.commentId(anchor?.href), el.getAttribute('aria-expanded'),
      container?.querySelectorAll('time[datetime]').length]);
  };
  let stableCommentContainer = null;
  function commentContainer(nodes = commentNodes()) {
    if (stableCommentContainer?.isConnected && visible(stableCommentContainer)) return stableCommentContainer;
    const scored = new Map();
    for (const node of nodes) {
      for (let el = node.parentElement, depth = 0; el && el !== document.body && depth < 10; el = el.parentElement, depth += 1) {
        const style = getComputedStyle(el);
        if (!/(auto|scroll)/.test(style.overflowY) || el.scrollHeight <= el.clientHeight + 8 || !visible(el)) continue;
        const score = (scored.get(el) || 0) + 4 + el.querySelectorAll('time[datetime]').length;
        scored.set(el, score);
      }
    }
    for (const dialog of document.querySelectorAll('[role="dialog"]')) {
      for (const el of dialog.querySelectorAll('div, section, ul')) {
        const style = getComputedStyle(el);
        if (visible(el) && /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 8) {
          scored.set(el, (scored.get(el) || 0) + el.querySelectorAll('time[datetime]').length * 5 + 2);
        }
      }
    }
    stableCommentContainer = [...scored.entries()].sort((a, b) => b[1] - a[1] || a[0].clientHeight - b[0].clientHeight)[0]?.[0] || null;
    return stableCommentContainer;
  }
  function commentState() {
    const nodes = commentNodes();
    const container = commentContainer(nodes);
    const empty = core.isEmptyCommentsLabel(document.body.innerText.slice(0, 5000));
    const last = nodes[nodes.length - 1] || null;
    const lastLink = last && [...last.querySelectorAll('a[href]')].find(a => core.commentId(a.href));
    const loading = [...document.querySelectorAll('[role="progressbar"], [aria-busy="true"]')].some(visible);
    return {ready: Boolean(nodes.length || container || empty), empty, loading, node_count: nodes.length,
      pending_controls: expansionControls().length, container, last,
      frontier: {scroll_top: container?.scrollTop || 0, scroll_height: container?.scrollHeight || 0,
        client_height: container?.clientHeight || 0, last_comment_id: core.commentId(lastLink?.href)}};
  }
  async function prepareComments(timeoutMs = 20000) {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const isReel = (globalThis.__IG_VOC_OPTIONS__?.expected_media_type || core.mediaIdentity(location.href).media_type) === 'reel';
    let clicks = 0;
    const openPanel = async () => {
      for (const raw of document.querySelectorAll('button, [role="button"], [aria-label], svg[aria-label]')) {
        if (!visible(raw) || !controlParts(raw).some(core.isCommentTriggerLabel)) continue;
        const el = clickable(raw); el.scrollIntoView({block: 'center', behavior: 'auto'}); el.click();
        clicks += 1; await sleep(700);
        if (commentState().ready) return true;
      }
      return false;
    };
    let state = commentState();
    if (!state.ready) await openPanel();
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      state = commentState();
      if (state.ready) break;
      if (isReel && clicks < 3) await openPanel();
      await sleep(500);
    }
    const result = {ready: state.ready, empty: state.empty, media_type: isReel ? 'reel' : 'p',
      comment_icon_clicks: clicks, node_count: state.node_count, pending_controls: state.pending_controls,
      frontier: state.frontier, prepared_at: new Date().toISOString()};
    globalThis.__IG_VOC_PREPARE_RESULT__ = result;
    return result;
  }
  function collect() {
    const actualMedia = core.mediaIdentity(location.href);
    const expected = globalThis.__IG_VOC_OPTIONS__ || {};
    const media = actualMedia;
    const nodes = commentNodes();
    const result = [];
    const nodeIds = new Map();
    for (const node of nodes) {
      const authorLink = [...node.querySelectorAll('a[href]')].find(a => {
        return isProfileLink(a) && clean(a.textContent) && visible(a);
      });
      const author = authorLink ? clean(authorLink.textContent).replace(/已验证|verified/ig, '').replace(/^@/, '').trim() : null;
      const commentLink = [...node.querySelectorAll('a[href]')].find(a => core.commentId(a.href));
      const comment_id = node.getAttribute('data-comment-id') || (commentLink ? core.commentId(commentLink.href) : null);
      const time = node.querySelector('time[datetime]');
      const text = ownText(node, author, time);
      if (!author || !text || text === author) continue;
      let parent_comment_id = node.getAttribute('data-parent-comment-id') || null;
      const replyList = node.closest('ul');
      const parentCard = replyList?.parentElement?.closest('li');
      if (!parent_comment_id && parentCard && !replyList.contains(parentCard)) {
        const parentTime = [...parentCard.querySelectorAll('time[datetime]')].find(candidate => {
          const link = candidate.closest('a[href]');
          return !replyList.contains(candidate) && link && core.commentId(link.href);
        });
        if (parentTime) parent_comment_id = core.commentId(parentTime.closest('a[href]').href);
      }
      const likes = clean(node.innerText).match(/(?:^|\s)(\d[\d,.]*)\s*(?:likes?|次赞|赞)(?:\s|$)/i);
      result.push({comment_id, parent_comment_id, depth: parent_comment_id ? 1 : 0,
        author, comment_text: text, published_at: time ? time.getAttribute('datetime') : null,
        published_label: time ? clean(time.textContent) : (commentLink ? clean(commentLink.textContent) : null),
        like_count: likes ? likes[1] : null, comment_url: commentLink ? commentLink.href : null});
      if (comment_id) nodeIds.set(node, comment_id);
    }
    const owner = document.querySelector('article header a[href], [role="dialog"] header a[href]');
    const identity = core.verifyMediaIdentity({location_url: location.href,
      canonical_url: document.querySelector('link[rel="canonical"]')?.href,
      og_url: document.querySelector('meta[property="og:url"]')?.content,
      comment_urls: result.map(row => row.comment_url).filter(Boolean),
      expected_shortcode: expected.expected_shortcode || null});
    if (identity.mismatch_count) result.length = 0;
    let platformCommentCount = null;
    const commentTriggers = [...new Set([...document.querySelectorAll('button, [role="button"], svg[aria-label]')]
      .filter(el => controlParts(el).some(core.isCommentTriggerLabel)).map(clickable))].filter(visible)
      .sort((a, b) => Math.abs(a.getBoundingClientRect().top + a.getBoundingClientRect().height / 2 - innerHeight / 2) -
        Math.abs(b.getBoundingClientRect().top + b.getBoundingClientRect().height / 2 - innerHeight / 2));
    const commentControl = commentTriggers[0];
    if (commentControl) {
      const own = clean(commentControl.innerText || commentControl.textContent).match(/[\d,.]+(?:\.\d+)?\s*(?:万|k|m)?/i);
      platformCommentCount = own ? core.visibleCount(`${own[0]} comments`) : null;
      if (platformCommentCount == null) {
        const sibling = commentControl.nextElementSibling;
        const number = clean(sibling?.innerText || sibling?.textContent).match(/^[\d,.]+(?:\.\d+)?\s*(?:万|k|m)?$/i);
        if (number) platformCommentCount = core.visibleCount(`${number[0]} comments`);
      }
    }
    return {
      schema_version: 'instagram_visible_capture_v1', captured_at: new Date().toISOString(),
      capture_method: 'chrome_extension_visible_once_v1',
      media: {...media, owner: owner ? clean(owner.textContent).replace(/^@/, '') : null,
        identity_verified: identity.ok, identity_sources: identity.sources},
      page: {status: identity.mismatch_count ? 'MEDIA_ID_MISMATCH' : identity.ok ? core.restriction(document.body.innerText.slice(0, 3000)) : identity.reason, coverage: 'visible_only',
        identity_verified: identity.ok, actual_shortcode: identity.actual_shortcode,
        expected_shortcode: identity.expected_shortcode, observed_shortcodes: identity.observed_shortcodes,
        shortcode_mismatch_count: identity.mismatch_count,
        visible_comment_count: result.length, remaining_expand_controls: expansionControls().length,
        platform_comment_count: platformCommentCount,
        selector_candidate_count: nodes.length,
        extraction_note: 'Only currently rendered LI candidates; scroll/expand and recapture.'},
      comments: result
    };
  }
  function scrollNext() {
    const state = commentState();
    const target = state.container;
    if (!target) {
      if (state.last) {
        state.last.scrollIntoView({block: 'end', behavior: 'auto'});
        return {attempted: true, moved: false, method: 'last_comment_scrollIntoView', frontier: commentState().frontier};
      }
      return {attempted: false, moved: false, method: 'comment_container_missing', frontier: state.frontier};
    }
    const before = target.scrollTop;
    const maximum = Math.max(0, target.scrollHeight - target.clientHeight);
    target.scrollTop = Math.min(maximum, before + Math.max(480, Math.floor(target.clientHeight * 0.85)));
    return {attempted: true, moved: target.scrollTop !== before, method: 'comment_container_scroll', frontier: commentState().frontier};
  }
  globalThis.InstagramVocCapture = {collect, expansionControls, controlLabel, controlSignature, commentState, prepareComments, scrollNext};
  return collect();
})()
