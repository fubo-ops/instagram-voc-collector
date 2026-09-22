(() => {
  'use strict';
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const asin = (location.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/) || [])[1] || null;
  const title = clean(document.querySelector('#productTitle')?.textContent);
  const brandText = clean(document.querySelector('#bylineInfo')?.textContent).replace(/^(Visit the|Brand:)\s*/i, '').replace(/ Store$/i, '');
  const bullets = [...document.querySelectorAll('#feature-bullets li span.a-list-item')].map(el => clean(el.textContent)).filter(Boolean).slice(0, 12);
  const breadcrumbs = [...document.querySelectorAll('#wayfinding-breadcrumbs_feature_div a')].map(el => clean(el.textContent)).filter(Boolean);
  return {asin, title: title || null, brand: brandText || null, category: breadcrumbs.at(-1) || null,
    bullets, source_status: title ? 'available' : 'unavailable_or_challenge', source_url: location.href,
    inspected_at: new Date().toISOString()};
})()
