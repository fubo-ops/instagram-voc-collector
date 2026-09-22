# Raw record contract

`raw_comments.jsonl`, `raw_comments.csv`, and Excel `Raw_Comments` contain only `platform=instagram`, `record_type=comment` rows. Posts, Reels, captions, profiles, and search candidates remain provenance/audit data.

| Field group | Fields |
|---|---|
| Product/query provenance | `amazon_asins`, `matched_query`, `matched_queries` |
| Media source | `media_id`, `shortcode`, `media_type`, `media_url`, `post_url`, `media_owner` |
| Identity/thread | `comment_id`, `parent_comment_id`, `source_parent_id`, `depth` |
| Visible comment | `author`, `username`, `comment_text`, `published_at`, `timestamp`, `published_label`, `like_count`, `likes`, `comment_url` |
| Media semantics | `media_relevance_tier`, `media_relevance_score`, `media_evidence_terms` |
| Comment semantics | `comment_relevance_tier`, `comment_relevance_score`, `comment_evidence_terms` |
| Capture diagnostics | `captured_at`, `field_warnings` |

Aliases (`shortcode/media_id`, `post_url/media_url`, `username/author`, `timestamp/published_at`, `likes/like_count`) are intentionally both exported for downstream compatibility. Missing data remains null; relative labels are preserved without inventing absolute time.

## Technical deduplication

Use stable `comment_id`. If absent, use the exact tuple `(media_id, author, comment_text)`. Identical text with distinct comment IDs stays separate. Confirmed duplicates may merge ASIN/query provenance only. Semantic levels are labels, never delivery filters.

## Capture and audit objects

`instagram_visible_capture_v1` contains `media`, `page`, and `comments`. Continuous captures add `continuous_scroll` with start/end times, round/expand/scroll/idle counts, action ledger, stop reason, raw observation count, technical duplicate count, final unique count, and applied limits.

`post_audit.csv`/`.json` and Excel `Media_Audit` record displayed platform total when readable, main/reply counts, expansion/scroll rounds, unique count, relevance level, collection decision, stop reason, and `PASS/PARTIAL/BLOCKED` status. LOW candidates remain audit-only.
