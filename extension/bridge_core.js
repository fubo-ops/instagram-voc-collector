(function (root) {
  'use strict';
  function canonicalMedia(raw) {
    try {
      const url = new URL(raw);
      if (url.protocol !== 'https:' || !['instagram.com', 'www.instagram.com'].includes(url.hostname)) return null;
      const match = url.pathname.match(/^\/(?:[A-Za-z0-9._]+\/)?(p|reels?)\/([A-Za-z0-9_-]+)(?:\/|$)/);
      if (!match) return null;
      const kind = match[1] === 'p' ? 'p' : 'reel';
      const ownerMatch = url.pathname.match(/^\/([A-Za-z0-9._]+)\/(?:p|reels?)\//);
      return {shortcode: match[2], media_type: kind, url: `https://www.instagram.com/${kind}/${match[2]}/`, owner: ownerMatch?.[1] || null};
    } catch (_) { return null; }
  }
  function extractMediaCandidates(values) {
    const out = [], seen = new Set();
    for (const value of values || []) {
      const item = canonicalMedia(value);
      if (!item || seen.has(item.shortcode)) continue;
      seen.add(item.shortcode); out.push(item);
    }
    return out;
  }
  const api = {canonicalMedia, extractMediaCandidates};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InstagramVocBridgeCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
