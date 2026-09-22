(async () => {
  'use strict';
  const payload = globalThis.__IG_DISCOVERY_JOB__ || {};
  const core = globalThis.InstagramVocBridgeCore;
  const found = new Map();
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  function scan() {
    for (const anchor of document.querySelectorAll('a[href]')) {
      let href = anchor.href;
      try {
        const parsed = new URL(href); const wrapped = parsed.searchParams.get('q') || parsed.searchParams.get('url');
        if (wrapped && wrapped.includes('instagram.com/')) href = wrapped;
      } catch (_) {}
      const media = core.canonicalMedia(href); if (!media) continue;
      const context = anchor.closest('article, [data-snhf], li, div') || anchor;
      const text = clean(context.innerText || anchor.textContent).slice(0, 1200);
      const prior = found.get(media.shortcode) || {...media, visible_text: '', caption: '', hashtags: [], source_query: payload.query || null};
      if (text.length > prior.visible_text.length) prior.visible_text = text;
      if (text.length > prior.caption.length) {
        prior.caption = text;
        prior.hashtags = [...new Set(text.match(/#[\p{L}\p{N}_]+/gu) || [])];
      }
      found.set(media.shortcode, prior);
    }
  }
  await new Promise(resolve => setTimeout(resolve, 1800));
  let stagnant = 0, previous = 0;
  for (let round = 0; round < 12 && found.size < Number(payload.max_results || 10); round += 1) {
    scan(); stagnant = found.size === previous ? stagnant + 1 : 0; previous = found.size;
    if (stagnant >= 5) break;
    window.scrollBy({top: Math.max(700, window.innerHeight * .85), behavior: 'auto'});
    await new Promise(resolve => setTimeout(resolve, 900));
  }
  scan();
  return {query: payload.query || null, source_kind: payload.source_kind || null,
    candidates: [...found.values()].slice(0, Number(payload.max_results || 10)),
    discovered_at: new Date().toISOString(), page_url: location.href};
})()
