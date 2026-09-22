/* Bounded loop over visible DOM in the explicitly selected media tab. */
(async () => {
  'use strict';
  const core = globalThis.InstagramVocCore;
  const captureApi = globalThis.InstagramVocCapture;
  if (!core || !captureApi) throw new Error('持续采集组件未加载');
  const options = globalThis.__IG_VOC_OPTIONS__ || {};
  options.max_expand_passes_per_round = Number(options.max_expand_passes_per_round || 3);
  options.expand_replies = options.expand_replies !== false;
  options.auto_scroll = options.auto_scroll !== false;
  options.delay_min_ms = Math.max(3000, Number(options.delay_min_ms || 3000));
  options.delay_max_ms = Math.max(options.delay_min_ms, Number(options.delay_max_ms || 6000));
  const randomDelayMs = () => Math.floor(options.delay_min_ms + Math.random() * (options.delay_max_ms - options.delay_min_ms + 1));
  const started = Date.now();
  const startedAt = new Date(started).toISOString();
  const rows = new Map();
  const expandedThreadSignatures = new Set(options.expanded_thread_signatures || []);
  let rawObserved = 0;
  let duplicateCount = 0;
  let roundCount = 0;
  let expandActionCount = 0;
  let scrollActionCount = 0;
  let idleRoundCount = 0;
  let containerNotReadyRounds = 0;
  let stopReason = null;
  const actionLedger = [];
  if (!captureApi.commentState().ready) {
    await captureApi.prepareComments(Number(options.prepare_timeout_ms || 20000));
  }
  let latest = captureApi.collect();
  if (latest.page?.shortcode_mismatch_count) stopReason = 'MEDIA_ID_MISMATCH';

  const keyFor = row => row.comment_id ? `id:${row.comment_id}` :
    `fallback:${latest.media.media_id}|${row.author || ''}|${row.comment_text || ''}`;
  const merge = capture => {
    let added = 0;
    for (const row of capture.comments || []) {
      rawObserved += 1;
      const key = keyFor(row);
      if (rows.has(key)) duplicateCount += 1;
      else { rows.set(key, row); added += 1; }
    }
    return added;
  };
  if (!stopReason) merge(latest);
  while (!stopReason) {
    if (globalThis.__IG_VOC_STOP_REQUESTED__) { stopReason = 'manual_stop'; break; }
    const restriction = core.restriction(document.body.innerText.slice(0, 3000));
    if (restriction !== 'ready') { stopReason = restriction; break; }
    stopReason = core.continuousStopReason({comment_count: rows.size, round_count: roundCount,
      expand_action_count: expandActionCount, idle_round_count: idleRoundCount,
      elapsed_ms: Date.now() - started}, options);
    if (stopReason) break;
    const surfaceBeforeRound = captureApi.commentState();
    if (!surfaceBeforeRound.ready && containerNotReadyRounds > 0 && containerNotReadyRounds % 3 === 0) {
      await captureApi.prepareComments(Number(options.prepare_retry_timeout_ms || 6000));
    }
    let actions = 0;
    if (options.expand_replies) {
      const clickedThisRound = new Map();
      for (let pass = 0; pass < options.max_expand_passes_per_round; pass += 1) {
        const controls = captureApi.expansionControls().filter(control => {
          const stateKey = captureApi.controlSignature(control);
          return clickedThisRound.get(control) !== stateKey;
        });
        if (!controls.length) break;
        for (const control of controls) {
          if (actions >= options.clicks_per_round || expandActionCount >= options.max_expand_actions) break;
          const label = captureApi.controlLabel(control);
          const signature = captureApi.controlSignature(control);
          clickedThisRound.set(control, signature); expandedThreadSignatures.add(signature);
          control.scrollIntoView({block: 'center', behavior: 'auto'}); control.click();
          actions += 1; expandActionCount += 1;
          const waitMs = randomDelayMs();
          actionLedger.push({round: roundCount + 1, pass: pass + 1, label, action: 'expand_visible_control',
            wait_ms: waitMs, at: new Date().toISOString()});
          await new Promise(resolve => setTimeout(resolve, waitMs));
        }
        if (actions >= options.clicks_per_round || expandActionCount >= options.max_expand_actions) break;
      }
    }
    const beforeState = captureApi.commentState();
    const scroll = options.auto_scroll ? captureApi.scrollNext() : {attempted: false, moved: false, method: 'disabled', frontier: beforeState.frontier};
    if (scroll.moved) scrollActionCount += 1;
    const scrollWaitMs = randomDelayMs();
    actionLedger.push({round: roundCount + 1, action: 'scroll_wait', wait_ms: scrollWaitMs, at: new Date().toISOString()});
    await new Promise(resolve => setTimeout(resolve, scrollWaitMs));
    latest = captureApi.collect();
    if (latest.page?.shortcode_mismatch_count) {
      rows.clear(); stopReason = 'MEDIA_ID_MISMATCH';
      roundCount += 1; break;
    }
    const added = merge(latest);
    roundCount += 1;
    const afterState = captureApi.commentState();
    const frontierChanged = JSON.stringify(beforeState.frontier) !== JSON.stringify(afterState.frontier);
    containerNotReadyRounds = afterState.ready ? 0 : containerNotReadyRounds + 1;
    if (containerNotReadyRounds >= Number(options.container_ready_rounds || 12)) { stopReason = 'comment_container_not_ready'; }
    idleRoundCount = core.shouldCountIdle({ready: afterState.ready, added, actions,
      frontier_changed: frontierChanged || scroll.moved, pending_controls: afterState.pending_controls,
      loading: afterState.loading}) ? idleRoundCount + 1 : 0;
    try {
      await chrome.runtime.sendMessage({action: 'continuous_progress', job_id: globalThis.__IG_VOC_JOB_ID__ || null, progress: {
        comment_count: rows.size, round_count: roundCount, expand_action_count: expandActionCount,
        scroll_action_count: scrollActionCount, idle_round_count: idleRoundCount,
        collected_comment_ids: [...rows.values()].map(row => row.comment_id).filter(Boolean), started_at: startedAt
        , expanded_thread_signatures: [...expandedThreadSignatures], scroll_frontier: afterState.frontier,
        comment_container_ready: afterState.ready, pending_expand_controls: afterState.pending_controls,
        retry_count: Number(options.retry_count || 0)
      }});
    } catch (_) {}
  }
  if (stopReason === 'idle_rounds' && core.visibleExhausted(captureApi.commentState())) {
    stopReason = 'visible_comments_exhausted';
  }
  latest = captureApi.collect();
  if (latest.page?.shortcode_mismatch_count) { rows.clear(); stopReason = 'MEDIA_ID_MISMATCH'; }
  else if (!latest.page?.identity_verified && !rows.size) stopReason = stopReason || 'identity_evidence_not_ready';
  else merge(latest);
  const finishedAt = new Date().toISOString();
  latest.capture_method = 'chrome_extension_continuous_scroll_v1';
  latest.captured_at = finishedAt;
  latest.comments = [...rows.values()];
  latest.page.visible_comment_count = rows.size;
  latest.page.coverage = 'visible_only';
  latest.page.extraction_note = 'Bounded continuous scroll over rendered DOM; not an exhaustive platform/API result.';
  latest.continuous_scroll = {
    started_at: startedAt, finished_at: finishedAt, round_count: roundCount,
    expand_action_count: expandActionCount, scroll_action_count: scrollActionCount,
    idle_rounds: idleRoundCount, stop_reason: stopReason || 'unknown',
    raw_observed_count: rawObserved, technical_duplicate_count: duplicateCount,
    final_visible_unique_count: rows.size, action_ledger: actionLedger, limits: options
    , expanded_thread_signatures: [...expandedThreadSignatures], scroll_frontier: captureApi.commentState().frontier,
    comment_prepare: globalThis.__IG_VOC_PREPARE_RESULT__ || null, retry_count: Number(options.retry_count || 0)
  };
  try { await chrome.runtime.sendMessage({action: 'continuous_complete', job_id: globalThis.__IG_VOC_JOB_ID__ || null, capture: latest}); }
  catch (_) {}
  return latest;
})()
