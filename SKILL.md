---
name: instagram-voc-collector
description: Automatically discover and collect Instagram Post/Reel VOC from one or more Amazon ASINs through a logged-in Chrome session without the Instagram API, including semantic qualification, passive browser control, comment/reply scrolling, checkpoint resume, technical deduplication, media audits, and JSONL/CSV/Excel delivery. Use direct Post/Reel URLs only for smoke tests and debugging.
---

# Instagram VOC Collector

Run the formal workflow with ASIN input only through Chrome CDP and Playwright. The existing fixed extension is compatibility-only; do not upgrade or reload it during CDP runs.

## Formal entry point

```powershell
python scripts/instagram_automation.py collect --asin B003ULL1NQ --transport cdp --out-dir .\outputs\instagram
python scripts/instagram_automation.py collect --asins B003ULL1NQ,B0XXXXXXXX --out-dir .\outputs\instagram
python scripts/instagram_automation.py collect --asin-file .\asins.csv --out-dir .\outputs\instagram
```

Formal runs use `--transport cdp` by default, connect at `http://127.0.0.1:9333`, and never copy/read/export cookies or tokens. `--transport extension` and its `chrome` alias remain explicit compatibility options only.

Defaults: `target_posts=30`, `target_comments=1000`, `max_scroll_rounds_per_post=200`, randomized Instagram navigation delay `20–35s`, randomized comment/reply delay `3–6s`, a randomized `2–5min` rest after every 5 completed posts, and graded rate-limit cooldowns of `1h / 4h / 12h+`. Browser work is strictly serial.

For long-running VOC coverage, `--collection-goal maximize_relevant_comments` treats 1,000 as a soft target. It resumes up to five qualified `PARTIAL` media first, then creates bounded follow-up queries only from verified HIGH/MEDIUM captions, hashtags, and labeled comment evidence while retaining a brand/product-alias anchor. Two consecutive no-growth discovery rounds stop as `semantic_saturation`.

## Required flow

1. Build a product profile per ASIN from the rendered Amazon product page: brand, title/product name, category, functions, scenarios, pain points, ingredients/components, competitors when observed, and grounded keywords. Mark unavailable source facts; never invent them.
2. Generate a multi-family Instagram query plan. Discover `/p/` and `/reel/` candidates from rendered public-search and Instagram hashtag pages, canonicalize URLs, and deduplicate by shortcode while merging query/ASIN provenance.
3. Inspect rendered media metadata and score relevance as `HIGH`, `MEDIUM`, or `LOW`. Collect `HIGH` then `MEDIUM`, up to `target_posts`. Retain `LOW` as audit-only.
4. For each selected media URL, Playwright reuses one CDP-controlled tab, verifies navigation identity, executes the capture scripts, expands visible comment/reply controls, scrolls the comment container, and proceeds to the next media.
5. Preserve all captured comments/replies after technical deduplication. Add comment relevance labels only after raw acquisition; never delete a raw row because it is semantically low.

Read [collection guide](references/collection-guide.md) for setup/runtime behavior and [record schema](references/raw-record-schema.md) before consuming outputs.

## Direct URL debug mode

```powershell
python scripts/instagram_automation.py smoke --transport cdp --smoke-url "https://www.instagram.com/p/POST_CODE/" --target-posts 1 --target-comments 200 --out-dir .\outputs\instagram-smoke
```

A direct URL is smoke/debug input, not the formal discovery entry point. The legacy `instagram_collector.py plan|preflight|ingest` commands remain available for offline fixture import.

## Invariants

- Raw delivery contains comment rows only. Preserve exact visible text, stable comment IDs, reply lineage, author/username, visible timestamps/labels, likes, media URL/shortcode/type, capture time, ASIN/query provenance, and field warnings.
- Technical duplicate key is stable `comment_id`; without one, exact `(shortcode, username, comment_text)`. Never fuzzy-merge or semantic-merge.
- Before accepting any comment, verify that `location.pathname`, canonical/`og:url`, and every available comment permalink resolve to the candidate shortcode. Never replace the rendered shortcode with an expected value. On any mismatch, discard that capture, record `MEDIA_ID_MISMATCH`, retry the media, and fail the run quality gate when `shortcode_mismatch_count > 0`.
- Score media relevance only from rendered media evidence (caption, account, hashtags, and visible media alt text). Search queries and ASIN provenance are discovery context, never relevance evidence.
- Write `automation_checkpoint.json` every browser progress round and after each batch. Requeue unfinished jobs on restart; retry zero-row, `PARTIAL`, or platform-count-gap media instead of treating saved evidence as permanent success.
- Serialize discovery, inspection, and capture through one reused CDP tab. Randomize Instagram navigations over 20–35 seconds and comment/reply actions over 3–6 seconds; rest 2–5 minutes after each 5 completed posts.
- On HTTP 429, `Try again later`, `操作过于频繁`, or `rate_limited`, save the checkpoint and stop the whole run. Cool down for 1 hour after the first event, 4 hours after the second, and 12 hours after the third or later event.
- During cooldown, reject the run before Chrome starts. After cooldown, the next invocation performs exactly one read-only Instagram-home preflight and exits. A successful later resume is capped to one query/one post, then a five-post ramp, before normal limits resume.
- One media failure never aborts the run. Login walls, CAPTCHA, rate limits, private/unavailable content, timeouts, and selector gaps become `BLOCKED` or `PARTIAL` audit rows with a stop reason.
- CDP reads only rendered pages in the dedicated profile; do not read cookies/tokens or call Instagram hidden/API endpoints. The retained extension transport remains limited to rendered pages plus localhost.

## Output contract

The run directory contains `raw_comments.jsonl`, `raw_comments.csv`, `instagram_raw_comments.xlsx`, `manifest.json`, `checkpoint.json`, `automation_checkpoint.json`, `post_audit.csv`, `post_audit.json`, `query_plan.json`, `conversation_map.json`, `media_candidates.json`, and per-media evidence under `evidence/<shortcode>/`.

The Excel workbook has `Raw_Comments`, `Media_Audit`, `Query_Plan`, `Run_Summary`, and `Quality_Gate`. `PASS` requires verified complete coverage; bounded visible-DOM collections normally remain `PARTIAL` even when the requested target is reached.
