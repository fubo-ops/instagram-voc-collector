#!/usr/bin/env python3
"""Offline ASIN product semantics, Instagram query planning, and relevance labels."""

import re
from urllib.parse import urlparse

MEDIA_RE = re.compile(r'^/(?:([A-Za-z0-9._]+)/)?(p|reels?)/([A-Za-z0-9_-]+)(?:/|$)')
STOP = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'amazon', 'pack', 'count'}
RELEVANCE_MODEL_VERSION = 'grouped-semantic-gate-v2'


def clean(values):
    out, seen = [], set()
    for value in values or []:
        value = re.sub(r'\s+', ' ', str(value or '')).strip(' ,;|-.')
        key = value.casefold()
        if value and key not in seen:
            seen.add(key); out.append(value)
    return out


def words(value):
    return [x for x in re.findall(r'[A-Za-z0-9][A-Za-z0-9+.-]*', str(value or '').lower())
            if len(x) > 2 and x not in STOP]


def _contains(text, terms):
    folded = str(text or '').casefold()
    return [term for term in clean(terms) if term.casefold() in folded]


def build_product_profile(asin, product=None):
    product = product or {}
    title = product.get('title') or product.get('product_title') or ''
    bullets = clean(product.get('bullets') or product.get('core_attributes') or [])
    corpus = ' '.join([title] + bullets).lower()
    brand = product.get('brand') or (title.split()[0] if title else None)
    category = product.get('category') or product.get('product_type')
    if category and ('a+' in category.casefold() or '拼图' in category): category = None
    if not category:
        if re.search(r'(?:slow|slo|puzzle)\s+(?:feeder|feeding|bowl)', corpus): category = 'slow feeder dog bowl'
        elif 'dog bowl' in corpus: category = 'dog bowl'
        elif 'joint' in corpus and ('dog' in corpus or 'pet' in corpus): category = 'dog joint supplement'
    concise_title = re.split(r'\s+[–—-]\s+|,', title, maxsplit=1)[0].strip()
    product_name = product.get('product_name') or concise_title or title or asin
    functions = clean((product.get('functions') or product.get('primary_functions') or []) +
                      [x for x in ('joint mobility', 'joint health', 'pain relief', 'skin care', 'oral care',
                                  'slow down eating', 'slower eating', 'longer mealtime', 'puzzle enrichment',
                                  'non-slip feeding', 'easy to clean')
                       if x in corpus or all(word in corpus for word in x.split())])
    scenarios = clean((product.get('use_scenarios') or product.get('use_cases') or []) +
                      [x for x in ('senior dog', 'large breed dog', 'daily use', 'post surgery',
                                  'small to medium dogs', 'wet or dry dog food', 'daily mealtime')
                       if x in corpus or all(word in corpus for word in x.split())])
    pains = clean((product.get('pain_points') or product.get('problems') or []) +
                  [x for x in ('stiffness', 'arthritis', 'limping', 'struggling to get up', 'side effects',
                               'eating too fast', 'fast eating', 'gulping food')
                   if x in corpus or all(word in corpus for word in x.split())])
    ingredients = clean((product.get('ingredients') or product.get('ingredients_or_components') or []) +
                        [x for x in ('glucosamine', 'chondroitin', 'MSM', 'omega 3', 'food-safe', 'BPA-free')
                         if x.lower() in corpus])
    title_terms = words(title)
    generic_aliases = {'joint', 'mobility', 'health', 'support', 'supplement', 'supplements',
                       'product', 'products', 'senior', 'adult', 'dogs', 'dog', 'cats', 'cat',
                       'with', 'plus', 'advanced', 'formula', 'daily', 'care', 'medium', 'capacity',
                       'feeder', 'bowl', 'slow', 'feeding', 'small', 'notch', 'pattern'}
    brand_tokens = set(words(brand))
    phrase_aliases = [phrase for phrase in ('Fun Feeder Slo Bowl', 'Outward Hound Fun Feeder',
                      'Cosequin', 'Dasuquin') if phrase.casefold() in corpus]
    aliases = clean((product.get('aliases') or product.get('common_names') or []) + phrase_aliases +
                    [token for token in re.findall(r'[A-Za-z0-9+.-]+', title)
                     if len(token) >= 5 and token.casefold() not in
                     (brand_tokens | generic_aliases)])[:5]
    keywords = clean([brand, product_name, category] + functions + scenarios + pains + ingredients + title_terms[:10])
    return {
        'asin': asin, 'brand': brand, 'product_name': product_name, 'category': category,
        'product_aliases': aliases,
        'core_functions': functions, 'use_scenarios': scenarios, 'pain_points': pains,
        'ingredients': ingredients, 'competitors': clean(product.get('competitors') or product.get('competitor_products') or []),
        'keywords': keywords, 'source_status': product.get('source_status') or ('available' if title else 'asin_only'),
        'source_url': product.get('source_url') or ('https://www.amazon.com/dp/%s' % asin),
    }


def generate_instagram_queries(profiles):
    rows = []
    for profile in profiles:
        brand, name, category = profile.get('brand'), profile.get('product_name'), profile.get('category')
        function = (profile.get('core_functions') or ['product review'])[0]
        scenario = (profile.get('use_scenarios') or ['daily use'])[0]
        pain = (profile.get('pain_points') or ['problem solution'])[0]
        ingredient = (profile.get('ingredients') or [category or 'product'])[0]
        entity = name or profile['asin']
        if brand and brand.casefold() not in entity.casefold(): entity = f'{brand} {entity}'
        values = [
            (entity, 'product_entity', 100),
            (f'{name or profile["asin"]} review', 'decision_intent', 95),
            (f'{brand or ""} {category or name or profile["asin"]}'.strip(), 'brand_category', 90),
            (f'{category or ingredient} {function}', 'category_solution', 85),
            (f'{pain} {category or ingredient}', 'problem_pain', 82),
            (f'{scenario} {function}', 'use_scenario', 78),
            (f'{category or ingredient} review', 'category_review', 76),
        ]
        for query, family, priority in values:
            rows.append({'asin': profile['asin'], 'query': re.sub(r'\s+', ' ', query).strip(),
                         'query_family': family, 'generation_source': 'asin_product_profile',
                         'source_terms': clean([brand, name, category, function, scenario, pain, ingredient]),
                         'priority': priority, 'executed': False, 'posts_found': 0, 'qualified_posts': 0})
    out, seen = [], set()
    for row in sorted(rows, key=lambda x: -x['priority']):
        key = row['query'].casefold()
        if key and key not in seen:
            seen.add(key); out.append(row)
    return out


def generate_expansion_queries(profiles, existing_queries=None):
    existing = {str(row.get('query') or '').casefold() for row in existing_queries or []}
    rows = []
    for profile in profiles:
        brand = profile.get('brand') or ''
        aliases = [value for value in profile.get('product_aliases', []) if len(words(value)) >= 2]
        alias = aliases[0] if aliases else profile.get('product_name') or profile['asin']
        function = (profile.get('core_functions') or ['product experience'])[0]
        scenario = (profile.get('use_scenarios') or ['daily use'])[0]
        pain = (profile.get('pain_points') or ['eating too fast'])[0]
        values = [
            (f'{alias} dog review', 'decision_intent', 74),
            (f'{alias} {function}', 'category_solution', 72),
            (f'{brand} {alias} {scenario}', 'usage_context', 70),
            (f'{alias} {pain}', 'problem_pain', 68),
        ]
        for query, family, priority in values:
            query = re.sub(r'\s+', ' ', query).strip()
            if query.casefold() in existing: continue
            existing.add(query.casefold())
            rows.append({'asin': profile['asin'], 'query': query, 'query_family': family,
                         'generation_source': 'grouped_semantic_gate_v2_expansion',
                         'source_terms': clean([brand, alias, function, scenario, pain]),
                         'priority': priority, 'executed': False, 'posts_found': 0, 'qualified_posts': 0})
    return rows


def generate_evidence_queries(profiles, verified_media, valid_comments=None, existing_queries=None,
                              round_number=1, limit=4):
    """Build bounded follow-up queries only from verified HIGH/MEDIUM rendered evidence."""
    existing = {str(row.get('query') or '').casefold() for row in existing_queries or []}
    signatures = {tuple(sorted(words(row.get('query')))) for row in existing_queries or []}
    comments = valid_comments or []
    rows = []
    for profile in profiles:
        aliases = [value for value in profile.get('product_aliases', []) if len(words(value)) >= 2]
        anchor = aliases[0] if aliases else profile.get('brand') or profile.get('product_name') or profile['asin']
        anchor_folded = anchor.casefold()
        asin = profile['asin']
        media = [row for row in verified_media or []
                 if row.get('relevance_tier') in ('HIGH', 'MEDIUM') and row.get('deliverable_eligible')
                 and (not row.get('matched_asins') or asin in row.get('matched_asins', []))]
        codes = {row.get('shortcode') for row in media if row.get('shortcode')}
        caption_signals, term_signals, hashtag_signals = [], [], []
        product_tokens = set(words(' '.join(clean([profile.get('brand'), profile.get('product_name'),
            profile.get('category')] + profile.get('product_aliases', []) + profile.get('core_functions', [])))))
        product_tokens -= {'dogs', 'dog', 'pets', 'pet', 'review', 'daily'}
        for row in media:
            caption = str(row.get('caption') or '').casefold()
            for phrase in ('slow feeding', 'eat fast', 'eating too fast', 'longer mealtime', 'improve digestion',
                           'mental stimulation', 'puzzle enrichment', 'wet food', 'dry food', 'small snout', 'gobbles'):
                if phrase in caption:
                    caption_signals.append((phrase, row.get('shortcode'), None, 'rendered_caption'))
            term_signals.extend((term, row.get('shortcode'), None, 'rendered_media')
                                for term in row.get('evidence_terms', []))
            hashtag_signals.extend((str(tag).lstrip('#'), row.get('shortcode'), None, 'rendered_hashtag')
                                   for tag in row.get('hashtags', [])
                                   if any(token in re.sub(r'[^a-z0-9]+', '', str(tag).casefold()) for token in product_tokens))
        signals = caption_signals + term_signals + hashtag_signals
        for comment in comments:
            if comment.get('media_id') not in codes or comment.get('comment_relevance_tier') not in ('HIGH', 'MEDIUM'):
                continue
            signals.extend((term, comment.get('media_id'), comment.get('comment_id'), 'verified_comment')
                           for term in comment.get('comment_evidence_terms', []))
        seen_signals = set()
        for signal, shortcode, comment_id, evidence_kind in signals:
            signal = re.sub(r'[_#]+', ' ', str(signal or '')).strip()
            key = signal.casefold()
            if len(signal) < 4 or key in seen_signals or key in anchor_folded or anchor_folded in key:
                continue
            seen_signals.add(key)
            query = re.sub(r'\s+', ' ', f'{anchor} {signal}').strip()
            signature = tuple(sorted(words(query)))
            if query.casefold() in existing or signature in signatures:
                continue
            existing.add(query.casefold())
            signatures.add(signature)
            rows.append({'asin': asin, 'query': query, 'query_family': 'verified_evidence_expansion',
                         'generation_source': f'verified_high_media_evidence_round_{round_number}',
                         'strong_anchor': anchor, 'source_terms': clean([anchor, signal]),
                         'evidence_kind': evidence_kind, 'evidence_shortcodes': clean([shortcode]),
                         'evidence_comment_ids': clean([comment_id]),
                         'priority': 66 - len(rows), 'executed': False,
                         'posts_found': 0, 'qualified_posts': 0})
            if len(rows) >= limit:
                return rows
    return rows


def canonical_media(raw):
    parsed = urlparse(str(raw or ''))
    if parsed.scheme != 'https' or parsed.hostname not in ('instagram.com', 'www.instagram.com'):
        return None
    match = MEDIA_RE.match(parsed.path)
    if not match: return None
    owner, kind, shortcode = match.groups()
    kind = 'p' if kind == 'p' else 'reel'
    return {'shortcode': shortcode, 'media_type': kind, 'owner': owner,
            'url': f'https://www.instagram.com/{kind}/{shortcode}/'}


def dedupe_media_candidates(rows):
    out, by_code = [], {}
    for source in rows or []:
        identity = canonical_media(source.get('url'))
        if not identity: continue
        row = by_code.get(identity['shortcode'])
        if row:
            row['matched_queries'] = clean(row.get('matched_queries', []) + [source.get('source_query')] + source.get('matched_queries', []))
            row['matched_asins'] = clean(row.get('matched_asins', []) + source.get('matched_asins', []))
            continue
        row = {**source, **identity,
               'discovery_url': source.get('discovery_url') or identity['url'],
               'discovery_media_type': source.get('discovery_media_type') or identity['media_type'],
               'matched_queries': clean([source.get('source_query')] + source.get('matched_queries', [])),
               'matched_asins': clean(source.get('matched_asins', []))}
        by_code[identity['shortcode']] = row; out.append(row)
    return out


def semantic_terms(profiles):
    direct = clean(x for p in profiles for x in [p.get('brand'), p.get('product_name'), *p.get('product_aliases', [])] if x)
    medium = clean(x for p in profiles for x in ([p.get('category')] + p.get('core_functions', []) +
                  p.get('use_scenarios', []) + p.get('pain_points', []) + p.get('ingredients', [])) if x)
    return direct, medium


def _term_sources(term, evidence):
    needle = str(term or '').casefold()
    compact = re.sub(r'[^a-z0-9]+', '', needle)
    return [source for source, value in evidence.items()
            if needle and (needle in value.casefold() or
               (len(compact) >= 6 and compact in re.sub(r'[^a-z0-9]+', '', value.casefold())))]


def rendered_evidence(candidate):
    """Return only identity-bound page evidence; discovery queries/provenance are excluded."""
    caption = str(candidate.get('caption') or '').strip()
    alt = str(candidate.get('media_alt_text') or candidate.get('alt_text') or '').strip()
    visible = ' '.join(str(candidate.get('visible_text') or '').split())
    if not caption and visible:
        correlated = [' '.join(str(value or '').split()) for value in candidate.get('visible_image_alts') or []]
        correlated = [value for value in correlated if len(value) >= 20 and value.casefold() in visible.casefold()]
        if correlated:
            alt = max(correlated, key=len)
    return {key: value for key, value in {
        'caption': caption,
        'account': ' '.join(clean((candidate.get('accounts') or []) + [candidate.get('owner')])),
        'hashtags': ' '.join(candidate.get('hashtags') or []),
        'alt_text': alt,
    }.items() if value and value not in ('帖子', 'Reels', '轮播')}


def score_media_relevance(candidate, profiles):
    evidence = rendered_evidence(candidate)
    page_text = ' '.join(evidence.values()).casefold()
    best = {'tier': 'LOW', 'score': -1.0, 'evidence_terms': [], 'evidence_sources': [], 'group_hits': {},
            'matched_groups': [], 'reason': 'insufficient rendered semantic groups'}
    for profile in profiles:
        brand = clean([profile.get('brand')])
        aliases = [value for value in clean([profile.get('product_name')] + profile.get('product_aliases', []))
                   if (len(words(value)) >= 2 or len(re.sub(r'[^a-z0-9]+', '', value.casefold())) >= 6)
                   and value.casefold() not in {'non-slip', 'slow feeder', 'slow feeders', 'dog bowl'}]
        category = clean([profile.get('category')] + profile.get('core_functions', []) + profile.get('ingredients', []))
        category += [value[:-1] for value in category if value.casefold().endswith('s')]
        groups = {
            'entity': clean(brand + aliases),
            'category_function': clean(category),
            'problem': clean(profile.get('pain_points', [])),
            'usage_context': clean(profile.get('use_scenarios', [])),
            'competitor': clean(profile.get('competitors', [])),
        }
        group_hits = {name: [{'term': term, 'sources': _term_sources(term, evidence)}
                             for term in terms if _term_sources(term, evidence)]
                      for name, terms in groups.items()}
        matched = [name for name, hits in group_hits.items() if hits]
        brand_hits = [hit for hit in group_hits['entity'] if hit['term'] in brand]
        alias_hits = [hit for hit in group_hits['entity'] if hit['term'] in aliases]
        score = min(1.0, (0.35 if group_hits['entity'] else 0) +
                    (0.20 if group_hits['category_function'] else 0) +
                    (0.15 if group_hits['problem'] else 0) +
                    (0.12 if group_hits['usage_context'] else 0) +
                    (0.35 if group_hits['competitor'] else 0))
        target_is_dog = any('dog' in str(value).casefold() for value in
                            [profile.get('product_name'), profile.get('category')] +
                            profile.get('use_scenarios', []) + profile.get('keywords', []))
        species_conflict = target_is_dog and bool(re.search(r'\b(?:cat|cats|feline|horse|equine|human|men|women)\b', page_text)) \
            and not bool(re.search(r'\b(?:dog|dogs|puppy|puppies|pup|pups|canine)\b', page_text))
        slow_feeder = 'feed' in str(profile.get('category') or '').casefold() or 'bowl' in str(profile.get('category') or '').casefold()
        wrong_product = slow_feeder and bool(re.search(r'\b(?:toy|treats?|dispenser|shelter|rescue)\b', page_text)) \
            and not bool(re.search(r'\b(?:slow\s*feed|feeder|bowl|eating|mealtime)\b', page_text))
        conflict = species_conflict or wrong_product
        if conflict:
            tier, score, reason = 'LOW', 0.0, 'species_or_category_conflict'
        elif alias_hits:
            tier, score, reason = 'HIGH', max(score, .85), 'specific product alias/model match'
        elif brand_hits and group_hits['category_function'] and len(matched) >= 2:
            tier, score, reason = 'HIGH', max(score, .70), 'brand plus category/function groups'
        elif len(matched) >= 2 and score >= .50 and (group_hits['entity'] or group_hits['competitor']):
            tier, reason = 'MEDIUM', 'two semantic groups with entity/competitor anchor'
        else:
            tier, score, reason = 'LOW', min(score, .49), 'insufficient grouped semantic gate'
        terms = clean(hit['term'] for hits in group_hits.values() for hit in hits)
        sources = sorted({source for hits in group_hits.values() for hit in hits for source in hit['sources']})
        if score > best['score']:
            best = {'tier': tier, 'score': round(score, 3), 'evidence_terms': terms,
                    'evidence_sources': sources, 'group_hits': group_hits,
                    'matched_groups': matched, 'reason': reason}
    best.update({'deliverable_eligible': best['tier'] in ('HIGH', 'MEDIUM'),
                 'relevance_model_version': RELEVANCE_MODEL_VERSION})
    return best


def classify_comment_relevance(text, profiles):
    direct, medium = semantic_terms(profiles)
    direct_hits, medium_hits = _contains(text, direct), _contains(text, medium)
    tier = 'HIGH' if direct_hits else 'MEDIUM' if medium_hits else 'LOW'
    score = min(1.0, .72 + .06 * len(direct_hits)) if direct_hits else min(.79, .46 + .08 * len(medium_hits)) if medium_hits else 0.0
    return {'tier': tier, 'score': round(score, 3), 'evidence_terms': clean(direct_hits + medium_hits),
            'reason': 'comment semantic label; never a delivery filter'}
