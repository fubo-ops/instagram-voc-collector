/* Opens and waits for the rendered Post/Reel comment surface before collection. */
(async () => {
  'use strict';
  const core = globalThis.InstagramVocCore;
  const capture = globalThis.InstagramVocCapture;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const labelParts = el => [el.textContent, el.getAttribute?.('aria-label'), el.getAttribute?.('title'),
    el.querySelector?.('svg[aria-label]')?.getAttribute('aria-label')];
  const clickable = el => el.closest?.('button, [role="button"], a[href]') || el;
  let clickCount = 0;
  async function openCommentPanel() {
    const candidates = [...document.querySelectorAll('button, [role="button"], [aria-label], svg[aria-label]')]
      .filter(visible).filter(el => labelParts(el).some(core.isCommentTriggerLabel));
    for (const raw of candidates) {
      const el = clickable(raw);
      el.scrollIntoView({block: 'center', behavior: 'auto'});
      el.click(); clickCount += 1; await sleep(700);
      if (capture.commentState().ready) return true;
    }
    return false;
  }
  const isReel = (globalThis.__IG_VOC_OPTIONS__?.expected_media_type || core.mediaIdentity(location.href).media_type) === 'reel';
  let state = capture.commentState();
  if (!state.ready) await openCommentPanel();
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    state = capture.commentState();
    if (state.ready) break;
    if (isReel && clickCount < 3) await openCommentPanel();
    await sleep(500);
  }
  const result = {ready: state.ready, empty: state.empty, media_type: isReel ? 'reel' : 'p',
    comment_icon_clicks: clickCount, node_count: state.node_count, pending_controls: state.pending_controls,
    frontier: state.frontier, prepared_at: new Date().toISOString()};
  globalThis.__IG_VOC_PREPARE_RESULT__ = result;
  return result;
})()
