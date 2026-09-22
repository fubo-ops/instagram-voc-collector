# Automated collection guide

## Formal CDP setup

1. Start Chrome CDP with `powershell -File scripts/start_instagram_cdp.ps1`.
2. Log into Instagram once in that persistent Chrome profile; never copy/read/export cookies or tokens.
3. Start `scripts/instagram_automation.py --transport cdp`; no collector extension or popup click is used.

The formal CDP transport does not call Instagram hidden API/GraphQL endpoints. The existing extension remains available only through explicit `--transport extension` or `--transport chrome`.

## Formal ASIN run

```powershell
python scripts/instagram_automation.py collect `
  --asins B003ULL1NQ,B0XXXXXXXX `
  --transport cdp `
  --target-posts 30 `
  --target-comments 1000 `
  --max-scroll-rounds-per-post 200 `
  --scroll-delay-min-seconds 3 --scroll-delay-max-seconds 6 `
  --navigation-delay-min-seconds 20 --navigation-delay-max-seconds 35 `
  --post-batch-size 5 --batch-rest-min-seconds 120 --batch-rest-max-seconds 300 `
  --no-growth-limit 5 `
  --out-dir .\outputs\instagram
```

The controller performs five bounded stages: product profile, query plan, discovery/shortcode dedupe, semantic media inspection, then sequential collection. All browser work is serialized through one reused CDP tab. Instagram navigation waits are randomized over 20–35 seconds, comment/reply waits over 3–6 seconds, and every five completed posts trigger a randomized 2–5 minute rest. Product and media facts come only from rendered pages.

## Collection behavior

For each qualified Post/Reel the injected worker:

1. briefly activates the media tab, waits for dynamic rendering, and opens the Reel comment panel before capture;
2. captures rendered comment cards and stable visible permalinks, recognizing Post and Reel-dialog DOM;
3. expands every visible localized comment/reply control, including labels carried by aria/title/SVG, then scrolls the real comment container;
4. emits per-round IDs, expanded thread signatures, scroll frontier, retry count, and readiness to the checkpoint;
5. stops on target, max rounds/actions/runtime, repeated no growth, explicit user stop, or a visible access restriction.

Each media writes `evidence/<shortcode>/capture.json` and `action_ledger.json`. The controller then advances automatically. A failed media is recorded and the next candidate continues.

## Resume and audits

- Default checkpoint: `<out-dir>/automation_checkpoint.json`; unfinished active jobs are requeued after restart.
- HTTP 429, `Try again later`, `操作过于频繁`, or `rate_limited` immediately saves the checkpoint and stops the whole run. Cooldowns are 1 hour for the first event, 4 hours for the second, and 12 hours for the third or later event.
- A cooldown-period launch is rejected before Chrome starts. The first post-cooldown launch performs one read-only Instagram-home preflight and exits. After a successful preflight, resume uses one query/one post, then a five-post ramp, before returning to normal limits.
- Zero-row, `PARTIAL`, and visible platform-count-gap media are retried by default. Use `--force-reaudit true` to revisit saved results and `--max-retries-per-post` to bound retries.
- Import checkpoint: `<out-dir>/checkpoint.json`; capture hashes prevent duplicate ingestion.
- `PASS`: complete frontier independently verified, no count gap, and no remaining expansion controls.
- `PARTIAL`: visible slice retained, target/bound reached, replies/count uncertain, or restriction appeared after rows were read.
- `BLOCKED`: restriction/error before any readable row.

`target_comments` is a global desired unique-comment count. Reaching it stops the planned batch but is not a completeness claim.

## Debug and offline commands

```powershell
python scripts/instagram_automation.py smoke --transport cdp --smoke-url "https://www.instagram.com/p/POST_CODE/" --target-comments 200 --out-dir .\outputs\instagram-smoke
python scripts/instagram_collector.py preflight --capture .\outputs\instagram-smoke\evidence\POST_CODE\capture.json
python scripts/instagram_collector.py ingest --smoke --captures .\capture.json --out-dir .\outputs\fixture
```

If rendered comments are visible but extraction is zero, update the offline DOM fixture/selectors and tests before claiming coverage. Do not refresh past restrictions or automate login/CAPTCHA.
