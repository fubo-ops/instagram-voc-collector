#!/usr/bin/env python3
"""API-free Instagram visible-comment capture importer and audit CLI."""

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from xlsx_writer import write_workbook

SCHEMA = 'instagram_comment_v1'
ASIN_RE = re.compile(r'^[A-Z0-9]{10}$')
MEDIA_RE = re.compile(r'^/(p|reels?)/([A-Za-z0-9_-]+)(?:/|$)')
FIELDS = [
    'schema_version', 'platform', 'record_type', 'amazon_asins', 'matched_query', 'matched_queries',
    'media_id', 'shortcode', 'media_type', 'media_url', 'post_url', 'media_owner', 'comment_id',
    'media_relevance_tier', 'media_relevance_score', 'media_evidence_terms',
    'parent_comment_id', 'source_parent_id', 'depth', 'author', 'username', 'comment_text',
    'comment_relevance_tier', 'comment_relevance_score', 'comment_evidence_terms',
    'published_at', 'timestamp', 'published_label', 'like_count', 'likes', 'comment_url', 'captured_at', 'field_warnings'
]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def parse_asins(args):
    values = []
    if getattr(args, 'asin', None):
        values.append(args.asin)
    if getattr(args, 'asins', None):
        values += re.split(r'[,\s]+', args.asins)
    if getattr(args, 'asin_file', None):
        source = Path(args.asin_file)
        if source.suffix.lower() == '.csv':
            with source.open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.reader(f))
            values += [cell for row in rows for cell in row]
        else:
            values += re.split(r'[,\s]+', source.read_text(encoding='utf-8-sig'))
    result = []
    for value in values:
        value = str(value).strip().upper()
        if value in ('ASIN', ''):
            continue
        if not ASIN_RE.match(value):
            raise ValueError('Invalid ASIN: %s' % value)
        if value not in result:
            result.append(value)
    return result


def media_identity(url):
    parsed = urlparse(str(url or ''))
    if parsed.scheme != 'https' or parsed.hostname not in ('instagram.com', 'www.instagram.com'):
        raise ValueError('Expected an HTTPS instagram.com media URL')
    match = MEDIA_RE.match(parsed.path)
    if not match:
        raise ValueError('Expected a /p/ or /reel/ media URL')
    kind, shortcode = match.groups()
    kind = 'p' if kind == 'p' else 'reel'
    return shortcode, kind, 'https://www.instagram.com/%s/%s/' % (kind, shortcode)


def validate_capture(value):
    if not isinstance(value, dict) or value.get('schema_version') != 'instagram_visible_capture_v1':
        raise ValueError('Expected instagram_visible_capture_v1 JSON')
    media = value.get('media') or {}
    shortcode, kind, canonical = media_identity(media.get('media_url'))
    comments = value.get('comments') or []
    if not isinstance(comments, list):
        raise ValueError('comments must be a list')
    if any(not isinstance(comment, dict) for comment in comments):
        raise ValueError('Each comment must be an object')
    return shortcode, kind, canonical


def shortcode_mismatch_count(capture):
    media_id, _, _ = validate_capture(capture)
    page = capture.get('page') or {}
    count = int(page.get('shortcode_mismatch_count') or 0)
    if page.get('identity_verified') is False: count = max(1, count)
    strict = page.get('identity_verified') is True
    for comment in capture.get('comments') or []:
        raw = comment.get('comment_url')
        if not raw:
            if strict: count += 1
            continue
        try: code, _, _ = media_identity(raw)
        except ValueError: count += 1; continue
        if code != media_id: count += 1
    return count


def accepted_comments(capture):
    return [] if shortcode_mismatch_count(capture) else list(capture.get('comments') or [])


def plan(args):
    asins = parse_asins(args)
    if not asins and not (args.smoke and args.media_urls):
        raise ValueError('Provide an ASIN, or --smoke with a direct media URL')
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    facts = {'product_title': args.product_title or None, 'brand': args.brand or None,
             'category': args.category or None, 'extra_keywords': args.keywords or []}
    terms = []
    for family, value in [('brand', args.brand), ('category', args.category), ('product_title', args.product_title)]:
        if value and value.strip():
            terms.append({'query': value.strip(), 'family': family, 'generation_source': 'user_product_fact'})
    for value in args.keywords or []:
        if value.strip():
            terms.append({'query': value.strip(), 'family': 'provided_keyword', 'generation_source': 'user_provided'})
    seen = set()
    terms = [term for term in terms if not (term['query'].casefold() in seen or seen.add(term['query'].casefold()))]
    media_urls = []
    for raw_url in args.media_urls or []:
        _, _, canonical = media_identity(raw_url)
        if canonical not in media_urls:
            media_urls.append(canonical)
    result = {'schema_version': 'instagram_query_plan_v1', 'created_at': utc_now(), 'amazon_asins': asins,
              'product_facts': facts, 'queries': terms,
              'seed_media_urls': media_urls,
              'discovery_note': 'Candidate terms only; Instagram UI search coverage is not exhaustive.',
              'missing_product_context': not bool(terms)}
    path = out / 'query_plan.json'
    write_json(path, result)
    print(json.dumps({'query_plan': str(path.resolve()), 'query_count': len(terms), 'seed_media_count': len(media_urls), 'missing_product_context': not bool(terms)}, ensure_ascii=False))


def capture_report(capture):
    media_id, media_type, canonical = validate_capture(capture)
    mismatch_count = shortcode_mismatch_count(capture)
    comments = accepted_comments(capture)
    page = capture.get('page') or {}
    text_count = sum(bool(str(c.get('comment_text') or '').strip()) for c in comments)
    id_count = sum(bool(c.get('comment_id')) for c in comments)
    reply_count = sum(bool(c.get('parent_comment_id')) or int(c.get('depth') or 0) > 0 for c in comments)
    remaining = page.get('remaining_expand_controls')
    visible_count = page.get('visible_comment_count')
    platform_count = page.get('platform_comment_count')
    status = 'MEDIA_ID_MISMATCH' if mismatch_count else (page.get('status') or 'unknown')
    continuous = capture.get('continuous_scroll') or {}
    discovery = (capture.get('media') or {}).get('discovery') or {}
    navigation = capture.get('navigation') or {}
    audit_mismatch_count = mismatch_count + int(discovery.get('shortcode_mismatch_count') or 0)
    restricted = status in ('login_required', 'captcha', 'rate_limited', 'private', 'unavailable', 'blocked', 'MEDIA_ID_MISMATCH')
    if restricted:
        audit = 'PARTIAL' if comments else 'BLOCKED'
    elif page.get('coverage') == 'complete' and remaining == 0 and (visible_count is None or visible_count <= len(comments)):
        audit = 'PASS'
    else:
        audit = 'PARTIAL'
    reasons = []
    if audit == 'BLOCKED':
        reasons.append(status)
    if audit == 'PARTIAL':
        if restricted:
            reasons.append('access_restricted_after_visible_capture:' + status)
        if page.get('coverage') != 'complete':
            reasons.append('visible_only_or_unknown_coverage')
        if remaining is None or remaining > 0:
            reasons.append('expand_frontier_unverified' if remaining is None else 'expand_controls_remaining')
        if isinstance(visible_count, int) and visible_count > len(comments):
            reasons.append('visible_count_gap')
        if isinstance(platform_count, int) and platform_count > len(comments):
            reasons.append('platform_count_gap')
    if text_count < len(comments):
        reasons.append('missing_comment_text')
    if id_count < len(comments):
        reasons.append('missing_comment_id')
    if mismatch_count: reasons.append('MEDIA_ID_MISMATCH')
    return {'media_id': media_id, 'media_type': media_type, 'media_url': canonical,
            'discovery_media_type': discovery.get('discovery_media_type'), 'discovery_url': discovery.get('discovery_url'),
            'requested_media_type': navigation.get('requested_media_type'), 'requested_url': navigation.get('requested_url'),
            'rendered_media_type': navigation.get('rendered_media_type'), 'rendered_url': navigation.get('rendered_url'),
            'working_media_url': navigation.get('working_media_url'),
            'route_fallback_attempt_count': int(navigation.get('route_fallback_attempt_count') or 0),
            'route_fallback_success_count': int(navigation.get('route_fallback_success_count') or 0),
            'media_owner': (capture.get('media') or {}).get('owner'), 'audit_status': audit,
            'relevance_tier': discovery.get('relevance_tier'), 'relevance_score': discovery.get('relevance_score'),
            'evidence_terms': discovery.get('evidence_terms') or [],
            'deliverable_eligible': bool(discovery.get('deliverable_eligible',
                discovery.get('relevance_tier') in ('HIGH', 'MEDIUM'))), 'collection_status': 'COLLECTED',
            'page_status': status, 'captured_rows': len(comments), 'text_rows': text_count,
            'shortcode_mismatch_count': audit_mismatch_count,
            'stable_id_rows': id_count, 'reply_rows': reply_count,
            'main_comment_rows': len(comments) - reply_count,
            'visible_comment_count': visible_count, 'remaining_expand_controls': remaining,
            'platform_comment_count': platform_count,
            'expand_action_count': continuous.get('expand_action_count'),
            'scroll_round_count': continuous.get('round_count'),
            'scroll_action_count': continuous.get('scroll_action_count'),
            'stop_reason': continuous.get('stop_reason') or status,
            'reason': ';'.join(reasons), 'captured_at': capture.get('captured_at')}


def preflight(args):
    report = capture_report(read_json(Path(args.capture)))
    print(json.dumps(report, ensure_ascii=False, indent=2))


def normalize_comment(comment, capture, asins, matched_query):
    media_id, media_type, canonical = validate_capture(capture)
    comment_id = str(comment.get('comment_id') or '').strip() or None
    parent = str(comment.get('parent_comment_id') or '').strip() or None
    author = str(comment.get('author') or '').strip() or None
    body = comment.get('comment_text')
    body = str(body) if body is not None else None
    warnings = []
    for key, value in [('comment_id', comment_id), ('author', author), ('comment_text', body),
                       ('published_at', comment.get('published_at'))]:
        if value is None or value == '':
            warnings.append('missing_' + key)
    depth = int(comment.get('depth') or (1 if parent else 0))
    if depth > 0 and not parent:
        warnings.append('reply_parent_unresolved')
    discovery = (capture.get('media') or {}).get('discovery') or {}
    semantic = comment.get('semantic') or {}
    matched_asins = sorted(set(list(asins) + list(discovery.get('matched_asins') or [])))
    matched_queries = list(discovery.get('matched_queries') or [])
    if matched_query and matched_query not in matched_queries: matched_queries.append(matched_query)
    row = {
        'schema_version': SCHEMA, 'platform': 'instagram', 'record_type': 'comment',
        'amazon_asins': matched_asins, 'matched_query': matched_query or (matched_queries[0] if matched_queries else None),
        'matched_queries': matched_queries,
        'media_id': media_id, 'shortcode': media_id, 'media_type': media_type, 'media_url': canonical,
        'post_url': canonical,
        'media_owner': (capture.get('media') or {}).get('owner'), 'comment_id': comment_id,
        'media_relevance_tier': discovery.get('relevance_tier'),
        'media_relevance_score': discovery.get('relevance_score'),
        'media_evidence_terms': discovery.get('evidence_terms') or [],
        'parent_comment_id': parent, 'source_parent_id': ('comment:' + parent) if parent else ('media:' + media_id),
        'depth': depth, 'author': author, 'username': author, 'comment_text': body,
        'comment_relevance_tier': semantic.get('tier'), 'comment_relevance_score': semantic.get('score'),
        'comment_evidence_terms': semantic.get('evidence_terms') or [],
        'published_at': comment.get('published_at'), 'timestamp': comment.get('published_at'),
        'published_label': comment.get('published_label'),
        'like_count': comment.get('like_count'), 'likes': comment.get('like_count'),
        'comment_url': comment.get('comment_url'), 'captured_at': capture.get('captured_at') or utc_now(),
        'field_warnings': warnings
    }
    return row


def technical_key(row):
    if row['comment_id']:
        return ('id', row['comment_id'])
    return ('exact', row['media_id'], row['author'], row['comment_text'])


def _load_rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _write_rows(path, rows):
    with path.open('w', encoding='utf-8', newline='') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def _csv_value(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return '' if value is None else value


def ingest(args):
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    asins = parse_asins(args)
    existing_plan = out / 'query_plan.json'
    if not asins and existing_plan.exists():
        asins = read_json(existing_plan).get('amazon_asins') or []
    if not asins and not args.smoke:
        raise ValueError('Provide ASIN input, an existing ASIN query plan, or --smoke')
    raw_path = out / 'raw_comments.jsonl'
    rows = _load_rows(raw_path)
    candidate_rows = read_json(Path(args.candidate_audit)) if args.candidate_audit else []
    candidate_review = {row.get('shortcode'): row for row in candidate_rows if row.get('shortcode')}
    eligibility = {row.get('shortcode'): bool(row.get('deliverable_eligible'))
                   for row in candidate_rows if row.get('shortcode')}
    seen = {technical_key(row): row for row in rows}
    checkpoint_path = out / 'checkpoint.json'
    checkpoint = read_json(checkpoint_path) if checkpoint_path.exists() else {
        'schema_version': 'instagram_checkpoint_v1', 'processed_capture_hashes': [],
        'raw_captured_count': 0, 'technical_duplicate_count': 0, 'invalid_row_count': 0,
        'media_audit': {}, 'amazon_asins': asins
    }
    checkpoint.pop('stop_reason', None)
    processed = set(checkpoint['processed_capture_hashes'])
    for item in args.captures:
        path = Path(item)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in processed:
            continue
        capture = read_json(path)
        audit = capture_report(capture)
        if audit['audit_status'] == 'BLOCKED':
            checkpoint['media_audit'][audit['media_id']] = audit
            checkpoint['stop_reason'] = audit['page_status']
        else:
            if audit['page_status'] in ('login_required', 'captcha', 'rate_limited', 'private', 'unavailable', 'blocked'):
                checkpoint['stop_reason'] = audit['page_status']
            for comment in accepted_comments(capture):
                checkpoint['raw_captured_count'] += 1
                row = normalize_comment(comment, capture, asins, args.matched_query)
                if not row['comment_id'] and not (row['author'] and row['comment_text']):
                    checkpoint['invalid_row_count'] += 1
                    continue
                key = technical_key(row)
                if key in seen:
                    checkpoint['technical_duplicate_count'] += 1
                    prior = seen[key]
                    prior['amazon_asins'] = sorted(set(prior['amazon_asins'] + row['amazon_asins']))
                    queries = prior.setdefault('matched_queries', [prior['matched_query']] if prior.get('matched_query') else [])
                    if row['matched_query'] and row['matched_query'] not in queries:
                        queries.append(row['matched_query'])
                    continue
                seen[key] = row
                rows.append(row)
            checkpoint['media_audit'][audit['media_id']] = audit
        checkpoint['processed_capture_hashes'].append(digest)
        processed.add(digest)
        checkpoint['updated_at'] = utc_now()
        _write_rows(raw_path, rows)
        write_json(checkpoint_path, checkpoint)
        if args.target_comments and len(rows) >= args.target_comments and not args.soft_target:
            break
    with (out / 'raw_comments.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in FIELDS})
    audits = list(checkpoint['media_audit'].values())
    if args.candidate_audit:
        captured_ids = {audit['media_id'] for audit in audits}
        for candidate in candidate_rows:
            media_id = candidate.get('shortcode')
            if not media_id or media_id in captured_ids: continue
            decision = candidate.get('decision') or 'AUDIT_ONLY'
            audits.append({'media_id': media_id, 'media_type': candidate.get('media_type'),
                'media_url': candidate.get('url'), 'media_owner': candidate.get('owner'),
                'discovery_media_type': candidate.get('discovery_media_type'), 'discovery_url': candidate.get('discovery_url'),
                'requested_media_type': candidate.get('requested_media_type'), 'requested_url': candidate.get('requested_url'),
                'rendered_media_type': candidate.get('rendered_media_type'), 'rendered_url': candidate.get('rendered_url'),
                'working_media_url': candidate.get('working_media_url'),
                'route_fallback_attempt_count': int(candidate.get('route_fallback_attempt_count') or 0),
                'route_fallback_success_count': int(candidate.get('route_fallback_success_count') or 0),
                'audit_status': 'PARTIAL', 'page_status': 'not_collected', 'captured_rows': 0,
                'text_rows': 0, 'stable_id_rows': 0, 'main_comment_rows': 0, 'reply_rows': 0,
                'shortcode_mismatch_count': int(candidate.get('shortcode_mismatch_count') or 0),
                'visible_comment_count': None, 'remaining_expand_controls': None, 'platform_comment_count': None,
                'expand_action_count': 0, 'scroll_round_count': 0, 'scroll_action_count': 0,
                'stop_reason': candidate.get('stop_reason') or ('semantic_low_audit_only' if candidate.get('relevance_tier') == 'LOW' else 'target_posts_budget'),
                'reason': decision.lower(), 'captured_at': None, 'collection_status': decision,
                'relevance_tier': candidate.get('relevance_tier'), 'relevance_score': candidate.get('relevance_score'),
                'evidence_terms': candidate.get('evidence_terms') or [],
                'deliverable_eligible': bool(candidate.get('deliverable_eligible'))})
    delivery_rows = [row for row in rows if eligibility.get(row.get('media_id'), True)]
    for audit in audits:
        review = candidate_review.get(audit['media_id']) or {}
        for key in ('relevance_tier', 'relevance_score', 'evidence_terms', 'evidence_sources', 'matched_groups', 'group_hits',
                    'relevance_reason', 'relevance_model_version'):
            if key in review: audit[key] = review[key]
        audit['deliverable_eligible'] = eligibility.get(audit['media_id'],
            bool(audit.get('deliverable_eligible', audit.get('relevance_tier') in ('HIGH', 'MEDIUM'))))
        audit['final_unique_comment_count'] = sum(row['media_id'] == audit['media_id'] for row in delivery_rows)
        if not audit['deliverable_eligible'] and audit.get('captured_rows'):
            audit['collection_status'] = 'EXCLUDED_FROM_DELIVERABLE'
    observed_stops = [audit.get('stop_reason') for audit in audits if audit.get('stop_reason')]
    automation_path = out / 'automation_checkpoint.json'
    automation = read_json(automation_path) if automation_path.exists() else {}
    automation_stop = automation.get('stop_reason')
    final_stop_reason = ('target_comments_reached' if args.target_comments and len(delivery_rows) >= args.target_comments and not args.soft_target else
        automation_stop if automation_stop in ('rate_limited', 'semantic_saturation') else checkpoint.get('stop_reason') or next((reason for reason in
            ('login_required', 'rate_limited', 'comment_container_not_ready', 'unavailable',
             'max_rounds', 'visible_comments_exhausted', 'target_posts_budget', 'semantic_low_audit_only', 'semantic_saturation')
            if reason in observed_stops), 'captures_exhausted'))
    access = automation.get('access_control') or {}
    collection_goal = automation.get('collection_goal') or automation.get('settings', {}).get('collection_goal') or 'target'
    target_reached = bool(args.target_comments and len(delivery_rows) >= args.target_comments)
    workflow_status = ('STOPPED_RATE_LIMIT' if final_stop_reason == 'rate_limited' else
                       'COMPLETED_SEMANTIC_SATURATION' if final_stop_reason == 'semantic_saturation' else
                       'COMPLETED_BATCH')
    coverage_status = ('SOFT_TARGET_MET' if target_reached and args.soft_target else
                       'TARGET_MET' if target_reached else
                       'SEMANTIC_SATURATION' if final_stop_reason == 'semantic_saturation' else 'PARTIAL')
    target_shortfall_reason = None if target_reached else final_stop_reason
    manifest = {
        'schema_version': 'instagram_raw_manifest_v1', 'generated_at': utc_now(),
        'run_mode': 'smoke' if args.smoke else 'asin_research',
        'amazon_asins': sorted(set(checkpoint['amazon_asins'] + asins)),
        'raw_captured_count': checkpoint['raw_captured_count'],
        'technical_duplicate_count': checkpoint['technical_duplicate_count'],
        'invalid_row_count': checkpoint['invalid_row_count'], 'internal_raw_unique_count': len(rows),
        'deliverable_excluded_count': len(rows) - len(delivery_rows), 'final_collected_count': len(delivery_rows),
        'main_comment_count': sum(not row['parent_comment_id'] and row['depth'] == 0 for row in delivery_rows),
        'reply_count': sum(bool(row['parent_comment_id']) or row['depth'] > 0 for row in delivery_rows),
        'media_count': len(audits), 'pass_media_count': sum(a['audit_status'] == 'PASS' for a in audits),
        'partial_media_count': sum(a['audit_status'] == 'PARTIAL' for a in audits),
        'blocked_media_count': sum(a['audit_status'] == 'BLOCKED' for a in audits),
        'candidate_media_count': len(audits),
        'qualified_candidate_count': sum(a.get('relevance_tier') in ('HIGH', 'MEDIUM') for a in audits),
        'low_audit_only_count': sum(a.get('stop_reason') == 'semantic_low_audit_only' for a in audits),
        'shortcode_mismatch_count': sum(int(a.get('shortcode_mismatch_count') or 0) for a in audits),
        'route_fallback_attempt_count': sum(int(a.get('route_fallback_attempt_count') or 0) for a in audits),
        'route_fallback_success_count': sum(int(a.get('route_fallback_success_count') or 0) for a in audits),
        'standalone_media_records': 0, 'target_comments': args.target_comments,
        'collection_goal': collection_goal, 'soft_target': bool(args.soft_target),
        'Workflow_Status': workflow_status, 'Coverage_Status': coverage_status,
        'Valid_Comment_Total': len(delivery_rows), 'target_shortfall_reason': target_shortfall_reason,
        'navigation_delay_min_seconds': access.get('navigation_delay_min_seconds', 20.0),
        'navigation_delay_max_seconds': access.get('navigation_delay_max_seconds', 35.0),
        'scroll_delay_min_seconds': access.get('scroll_delay_min_seconds', 3.0),
        'scroll_delay_max_seconds': access.get('scroll_delay_max_seconds', 6.0),
        'post_batch_size': access.get('post_batch_size', 5),
        'batch_rest_min_seconds': access.get('batch_rest_min_seconds', 120.0),
        'batch_rest_max_seconds': access.get('batch_rest_max_seconds', 300.0),
        'rate_limit_cooldown_level1_seconds': access.get('rate_limit_cooldown_level1_seconds', 3600),
        'rate_limit_cooldown_level2_seconds': access.get('rate_limit_cooldown_level2_seconds', 14400),
        'rate_limit_cooldown_level3_seconds': access.get('rate_limit_cooldown_level3_seconds', 43200),
        'rate_limit_count': int(access.get('rate_limit_count') or 0),
        'rate_limit_level': int(access.get('rate_limit_level') or 0),
        'cooldown_started_at': access.get('cooldown_started_at'),
        'cooldown_until': access.get('cooldown_until'),
        'cooldown_ended_at': access.get('cooldown_ended_at'),
        'readonly_preflight_status': access.get('readonly_preflight_status'),
        'readonly_preflight_at': access.get('readonly_preflight_at'),
        'recovery_phase': access.get('recovery_phase'),
        'target_reached': target_reached,
        'stop_reason': final_stop_reason,
        'quality_gate': ('FAIL' if any(int(a.get('shortcode_mismatch_count') or 0) for a in audits) else
                         'PASS' if audits and all(a['audit_status'] == 'PASS' for a in audits) and not checkpoint['invalid_row_count'] else 'WARNING')
    }
    write_json(out / 'manifest.json', manifest)
    query_plan = read_json(existing_plan) if existing_plan.exists() else {'queries': [], 'amazon_asins': asins}
    write_json(existing_plan, query_plan)
    query_rows = [(q.get('query'), q.get('family') or q.get('query_family'), q.get('generation_source'),
                   bool(q.get('executed')), int(q.get('posts_found') or 0), int(q.get('qualified_posts') or 0))
                  for q in query_plan.get('queries', [])]
    query_rows += [(url, 'direct_media_url', 'user_provided', True, 1, 1)
                   for url in query_plan.get('seed_media_urls', [])]
    audit_headers = ['media_id', 'media_type', 'media_url', 'discovery_media_type', 'discovery_url',
                     'requested_media_type', 'requested_url', 'rendered_media_type', 'rendered_url', 'working_media_url',
                     'route_fallback_attempt_count', 'route_fallback_success_count',
                     'media_owner', 'relevance_tier', 'relevance_score', 'evidence_terms', 'evidence_sources', 'matched_groups', 'group_hits',
                     'relevance_reason', 'relevance_model_version', 'deliverable_eligible',
                     'collection_status', 'audit_status', 'page_status', 'platform_comment_count',
                     'captured_rows', 'main_comment_rows', 'reply_rows', 'final_unique_comment_count',
                     'shortcode_mismatch_count',
                     'expand_action_count', 'scroll_round_count', 'scroll_action_count',
                     'remaining_expand_controls', 'stop_reason', 'reason']
    with (out / 'post_audit.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=audit_headers, extrasaction='ignore')
        writer.writeheader()
        for audit in audits:
            writer.writerow({key: _csv_value(audit.get(key)) for key in audit_headers})
    write_json(out / 'post_audit.json', audits)
    summary = [(key, _csv_value(value)) for key, value in manifest.items() if key != 'amazon_asins']
    gate = [('no_standalone_media_rows', manifest['standalone_media_records'] == 0),
            ('raw_rows_equal_csv_rows', True), ('invalid_row_count', manifest['invalid_row_count']),
            ('shortcode_mismatch_count', manifest['shortcode_mismatch_count']),
            ('partial_media_count', manifest['partial_media_count']),
            ('blocked_media_count', manifest['blocked_media_count']), ('decision', manifest['quality_gate'])]
    write_workbook(out / 'instagram_raw_comments.xlsx', [
        ('Raw_Comments', FIELDS, [[_csv_value(row.get(key)) for key in FIELDS] for row in delivery_rows]),
        ('Media_Audit', audit_headers, [[_csv_value(a.get(key)) for key in audit_headers] for a in audits]),
        ('Query_Plan', ['query', 'family', 'generation_source', 'executed', 'posts_found', 'qualified_posts'], query_rows),
        ('Run_Summary', ['metric', 'value'], summary),
        ('Quality_Gate', ['check', 'value'], gate)
    ])
    print(json.dumps({'output_dir': str(out.resolve()), 'manifest': manifest}, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='Create an ASIN-grounded Instagram discovery plan')
    p.add_argument('--asin'); p.add_argument('--asins'); p.add_argument('--asin-file')
    p.add_argument('--product-title'); p.add_argument('--brand'); p.add_argument('--category')
    p.add_argument('--keywords', nargs='*'); p.add_argument('--media-urls', nargs='*')
    p.add_argument('--smoke', action='store_true', help='Allow a direct-media smoke without an ASIN')
    p.add_argument('--out-dir', default='outputs/instagram')
    p = sub.add_parser('preflight', help='Audit one browser-visible capture JSON')
    p.add_argument('--capture', required=True)
    p = sub.add_parser('ingest', help='Resume ingestion and export comments-only artifacts')
    p.add_argument('--asin'); p.add_argument('--asins'); p.add_argument('--asin-file')
    p.add_argument('--captures', nargs='+', required=True); p.add_argument('--matched-query')
    p.add_argument('--smoke', action='store_true', help='Label an unassociated media smoke; do not infer an ASIN')
    p.add_argument('--target-comments', type=int); p.add_argument('--out-dir', default='outputs/instagram')
    p.add_argument('--soft-target', type=lambda value: str(value).lower() not in ('0', 'false', 'no', 'off'), default=False)
    p.add_argument('--candidate-audit', help='Optional discovery and LOW-tier audit JSON')
    args = parser.parse_args(argv)
    if args.command == 'plan': plan(args)
    elif args.command == 'preflight': preflight(args)
    else: ingest(args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print('ERROR: %s' % exc, file=sys.stderr)
        sys.exit(2)
