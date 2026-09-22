(() => {
  'use strict';
  const core = globalThis.InstagramVocCore;
  const media = core.mediaIdentity(location.href);
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const root = document.querySelector('article') || document.querySelector('main') || document.body;
  const accountLinks = [...root.querySelectorAll('header a[href], a[href]')].filter(a => /^\/[A-Za-z0-9._]+\/$/.test(new URL(a.href).pathname) && clean(a.textContent));
  const ownerLink = accountLinks[0];
  const captionNode = root.querySelector('h1, ul li span[dir="auto"]');
  const visibleText = clean(root.innerText).slice(0, 5000);
  const hashtags = [...new Set((visibleText.match(/#[\p{L}\p{N}_]+/gu) || []))];
  const images = [...root.querySelectorAll('img[alt]')].map(img => clean(img.alt)).filter(Boolean).slice(0, 10);
  const mediaAltText = images.filter(value => !/头像|profile picture/i.test(value)).sort((a, b) => b.length - a.length)[0] || null;
  const commentUrls = [...document.querySelectorAll('a[href*="/c/"],a[href*="comment_id"]')].map(a => a.href);
  const identity = core.verifyMediaIdentity({location_url: location.href,
    canonical_url: document.querySelector('link[rel="canonical"]')?.href,
    og_url: document.querySelector('meta[property="og:url"]')?.content,
    comment_urls: commentUrls});
  return {...media, owner: ownerLink ? clean(ownerLink.textContent).replace(/已验证|verified/ig, '').trim() : null,
    accounts: [...new Set(accountLinks.map(a => clean(a.textContent).replace(/已验证|verified/ig, '').trim()).filter(Boolean))],
    caption: captionNode ? clean(captionNode.textContent) : null, hashtags, visible_text: visibleText,
    media_alt_text: mediaAltText, visible_image_alts: images, identity_verified: identity.ok, identity_sources: identity.sources,
    observed_shortcodes: identity.observed_shortcodes, shortcode_mismatch_count: identity.mismatch_count,
    status: identity.mismatch_count ? 'MEDIA_ID_MISMATCH' : identity.ok ? core.restriction(document.body.innerText.slice(0, 5000)) : identity.reason,
    inspected_at: new Date().toISOString()};
})()
