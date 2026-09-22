#!/usr/bin/env python3
"""ASIN-driven, API-free Instagram discovery and visible-comment automation controller."""

import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import instagram_discovery as discovery

DEFAULTS = {
    'target_posts': 30, 'target_comments': 1000, 'max_scroll_rounds_per_post': 200,
    'scroll_delay_min_seconds': 3.0, 'scroll_delay_max_seconds': 6.0,
    'navigation_delay_min_seconds': 20.0, 'navigation_delay_max_seconds': 35.0,
    'post_batch_size': 5, 'batch_rest_min_seconds': 120.0, 'batch_rest_max_seconds': 300.0,
    'rate_limit_cooldown_level1_seconds': 3600, 'rate_limit_cooldown_level2_seconds': 14400,
    'rate_limit_cooldown_level3_seconds': 43200,
    'no_growth_limit': 5, 'expand_replies': True,
    'auto_scroll': True, 'checkpoint_enabled': True, 'resume_enabled': True,
    'manual_start_required': False, 'retry_partial': True, 'force_reaudit': False,
    'max_retries_per_post': 1,
}

MAXIMIZE_RELEVANT_COMMENTS = 'maximize_relevant_comments'


def soft_target_mode(args):
    return getattr(args, 'collection_goal', None) == MAXIMIZE_RELEVANT_COMMENTS


def hard_target_reached(total, args):
    return bool(args.target_comments and total >= args.target_comments and not soft_target_mode(args))


def update_semantic_saturation(state, new_qualified_posts, new_valid_comments, round_name=None):
    semantic = state.setdefault('semantic_discovery', {})
    no_growth = not int(new_qualified_posts or 0) and not int(new_valid_comments or 0)
    semantic['consecutive_no_growth_rounds'] = (int(semantic.get('consecutive_no_growth_rounds') or 0) + 1) if no_growth else 0
    semantic.setdefault('history', []).append({
        'round': round_name or f"round_{len(semantic.get('history', [])) + 1}",
        'new_qualified_posts': int(new_qualified_posts or 0),
        'new_valid_comments': int(new_valid_comments or 0), 'no_growth': no_growth, 'at': utc_now()})
    semantic['status'] = 'semantic_saturation' if semantic['consecutive_no_growth_rounds'] >= 2 else 'ACTIVE'
    return semantic['status']


def load_comment_evidence(path):
    if not Path(path).exists(): return []
    rows = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if line.strip():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError: pass
    return rows


def utc_now(): return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def parse_asins(args):
    values = []
    if args.asin: values += re.split(r'[,\s]+', args.asin)
    if args.asins: values += re.split(r'[,\s]+', args.asins)
    if args.asin_file:
        source = Path(args.asin_file)
        if source.suffix.lower() == '.csv':
            with source.open(encoding='utf-8-sig', newline='') as stream:
                values += [cell for row in csv.reader(stream) for cell in row if cell.casefold() != 'asin']
        else: values += re.split(r'[,\s]+', source.read_text(encoding='utf-8-sig'))
    out = []
    for value in values:
        value = value.strip().upper()
        if not value: continue
        if not re.fullmatch(r'[A-Z0-9]{10}', value): raise ValueError('Invalid ASIN: ' + value)
        if value not in out: out.append(value)
    return out


class JobBroker:
    def __init__(self, checkpoint_path, initial=None):
        self.path = Path(checkpoint_path)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        if self.path.exists(): self.state = json.loads(self.path.read_text(encoding='utf-8-sig'))
        else: self.state = dict(initial or {})
        self.state.setdefault('schema_version', 'instagram_automation_checkpoint_v2')
        self.state.setdefault('pending_jobs', [])
        self.state.setdefault('active_jobs', {})
        self.state.setdefault('completed_jobs', {})
        self.state.setdefault('failed_jobs', {})
        self.extension_generation = 0
        # A controller restart requeues unfinished browser work. Completed media remain skipped.
        for job in list(self.state['active_jobs'].values()):
            job['status'] = 'pending'; job['resumed_at'] = utc_now(); self.state['pending_jobs'].insert(0, job)
        self.state['active_jobs'] = {}
        self.save()

    def save(self):
        self.state['updated_at'] = utc_now(); atomic_json(self.path, self.state)

    def enqueue(self, kind, payload):
        with self.changed:
            job = {'job_id': uuid.uuid4().hex, 'kind': kind, 'payload': payload,
                   'created_at': utc_now(), 'status': 'pending'}
            self.state['pending_jobs'].append(job); self.save(); self.changed.notify_all(); return job

    def claim_next(self):
        with self.changed:
            if not self.state['pending_jobs']: return None
            job = self.state['pending_jobs'].pop(0); job['status'] = 'active'; job['claimed_at'] = utc_now()
            self.state['active_jobs'][job['job_id']] = job; self.save(); return job

    def record_progress(self, message):
        with self.changed:
            job_id = message.get('job_id'); job = self.state['active_jobs'].get(job_id)
            if job:
                progress = message.get('progress') or {}
                job.update(progress); job['last_progress_at'] = utc_now()
                job.setdefault('progress_history', []).append({'at': job['last_progress_at'], **progress})
                job['progress_history'] = job['progress_history'][-250:]
                self.save(); self.changed.notify_all()
            return bool(job)

    def complete(self, message):
        with self.changed:
            job_id = message.get('job_id'); job = self.state['active_jobs'].pop(job_id, None)
            if not job: return False
            job.update({'status': 'completed', 'completed_at': utc_now(), 'result': message.get('result')})
            self.state['completed_jobs'][job_id] = job; self.save(); self.changed.notify_all(); return True

    def fail(self, message):
        with self.changed:
            job_id = message.get('job_id'); job = self.state['active_jobs'].pop(job_id, None) or {'job_id': job_id}
            job.update({'status': 'failed', 'failed_at': utc_now(), 'error': message.get('error') or 'extension_job_failed'})
            if message.get('navigation'): job['navigation'] = message['navigation']
            self.state['failed_jobs'][job_id] = job; self.save(); self.changed.notify_all(); return True

    def abandon(self, job_id, error='controller_stopped'):
        with self.changed:
            job = self.state['active_jobs'].pop(job_id, None)
            if not job:
                pending = [item for item in self.state['pending_jobs'] if item.get('job_id') == job_id]
                self.state['pending_jobs'] = [item for item in self.state['pending_jobs'] if item.get('job_id') != job_id]
                job = pending[0] if pending else None
            if not job: return False
            job.update({'status': 'failed', 'failed_at': utc_now(), 'error': error})
            self.state['failed_jobs'][job_id] = job; self.save(); self.changed.notify_all(); return True

    def record_extension_health(self, message):
        with self.changed:
            self.extension_generation += 1
            self.state['extension_health'] = {**message, 'seen_at': utc_now()}
            self.save(); self.changed.notify_all(); return True

    def wait_for_extension(self, after_generation, timeout=45):
        deadline = time.monotonic() + timeout
        with self.changed:
            while time.monotonic() < deadline:
                if self.extension_generation > after_generation:
                    return dict(self.state.get('extension_health') or {})
                self.changed.wait(min(1, deadline - time.monotonic()))
        raise TimeoutError('extension_health_timeout')

    def wait(self, job_id, timeout=900):
        deadline = time.monotonic() + timeout
        with self.changed:
            while time.monotonic() < deadline:
                if job_id in self.state['completed_jobs']:
                    job = self.state['completed_jobs'][job_id]
                    result = job.get('result') or {}
                    if isinstance(result, dict) and 'comments' in result:
                        job['result_summary'] = {
                            'comments': len(result.get('comments') or []),
                            'stop_reason': (result.get('continuous_scroll') or {}).get('stop_reason'),
                        }
                        job.pop('result', None)
                        self.save()
                    return result
                if job_id in self.state['failed_jobs']: raise RuntimeError(self.state['failed_jobs'][job_id]['error'])
                self.changed.wait(min(1, deadline - time.monotonic()))
        raise TimeoutError('Timed out waiting for Chrome extension job ' + job_id)


class BridgeHandler(BaseHTTPRequestHandler):
    broker = None

    def _headers(self, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'content-type')
        self.end_headers()

    def _reply(self, value, status=200):
        self._headers(status); self.wfile.write(json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self): self._headers(204)

    def do_GET(self):
        if self.path.startswith('/v1/health'): return self._reply({'ready': True, 'time': utc_now()})
        if self.path.startswith('/v1/next'):
            return self._reply({'job': self.broker.claim_next()})
        self._reply({'error': 'not_found'}, 404)

    def do_POST(self):
        size = int(self.headers.get('Content-Length') or 0)
        try: message = json.loads(self.rfile.read(size).decode('utf-8'))
        except Exception: return self._reply({'error': 'invalid_json'}, 400)
        if self.path == '/v1/extension/hello': ok = self.broker.record_extension_health(message)
        elif self.path == '/v1/progress': ok = self.broker.record_progress(message)
        elif self.path == '/v1/result': ok = self.broker.complete(message)
        elif self.path == '/v1/error': ok = self.broker.fail(message)
        else: return self._reply({'error': 'not_found'}, 404)
        self._reply({'ok': ok})

    def log_message(self, *_): pass


class BridgeServer:
    def __init__(self, broker, host='127.0.0.1', port=8765):
        handler = type('BoundBridgeHandler', (BridgeHandler,), {'broker': broker})
        self.server = ThreadingHTTPServer((host, port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self): self.thread.start(); return self
    def __exit__(self, *_): self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)


def extension_runtime_spec():
    extension_dir = Path.home() / '.codex' / 'skills' / 'instagram-voc-collector' / 'extension'
    manifest_path = extension_dir / 'manifest.json'
    if not manifest_path.exists():
        raise RuntimeError(f'extension_configuration_error:{extension_dir}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    digest = hashlib.sha256()
    for path in sorted(extension_dir.glob('*')):
        if path.is_file(): digest.update(path.name.encode()); digest.update(path.read_bytes())
    return {'extension_dir': str(extension_dir.resolve()), 'version': manifest['version'],
            'source_fingerprint': digest.hexdigest()}


def verify_extension_health(health, config_path, spec):
    loaded_version = str(health.get('version') or '')
    extension_id = str(health.get('extension_id') or '')
    if not extension_id or loaded_version != spec['version']:
        raise RuntimeError(f'extension_reload_required:loaded={loaded_version or "missing"},source={spec["version"]}')
    config_path = Path(config_path)
    saved = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    if saved.get('extension_id') and saved['extension_id'] != extension_id:
        raise RuntimeError(f'extension_id_mismatch:expected={saved["extension_id"]},actual={extension_id}')
    if saved.get('extension_dir') and Path(saved['extension_dir']).resolve() != Path(spec['extension_dir']).resolve():
        raise RuntimeError('extension_configuration_directory_mismatch')
    current = {**spec, 'extension_id': extension_id, 'verified_at': utc_now()}
    atomic_json(config_path, current)
    return current


def is_instagram_payload(payload):
    urls = [str((payload or {}).get(key) or '') for key in
            ('url', 'requested_url', 'discovery_url', 'navigation_source_url')]
    return any('instagram.com/' in url.lower() for url in urls)


def instagram_job_delay(payload, last_started, now, interval_seconds=20.0):
    return max(0.0, interval_seconds - (now - last_started)) if is_instagram_payload(payload) else 0.0


def random_seconds(low, high, randomizer=random.uniform):
    low, high = float(low), float(high)
    return float(randomizer(min(low, high), max(low, high)))


RATE_LIMIT_PATTERN = re.compile(
    r'http\s*(?:error\s*)?429|too many requests|try again later|rate[_ -]?limited|操作过于频繁', re.I)


class RateLimitDetected(RuntimeError):
    pass


def contains_rate_limit(value):
    if isinstance(value, dict):
        return any(contains_rate_limit(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(contains_rate_limit(item) for item in value)
    return bool(RATE_LIMIT_PATTERN.search(str(value or '')))


def _access_control(state, args=None):
    access = state.setdefault('access_control', {})
    def configured(key, default):
        return getattr(args, key, access.get(key, default)) if args is not None else access.get(key, default)
    access['navigation_delay_min_seconds'] = max(20.0, float(configured('navigation_delay_min_seconds', 20.0)))
    access['navigation_delay_max_seconds'] = max(access['navigation_delay_min_seconds'],
        float(configured('navigation_delay_max_seconds', 35.0)))
    access['scroll_delay_min_seconds'] = max(3.0, float(configured('scroll_delay_min_seconds', 3.0)))
    access['scroll_delay_max_seconds'] = max(access['scroll_delay_min_seconds'],
        float(configured('scroll_delay_max_seconds', 6.0)))
    access['post_batch_size'] = max(1, int(configured('post_batch_size', 5)))
    access['batch_rest_min_seconds'] = max(120.0, float(configured('batch_rest_min_seconds', 120.0)))
    access['batch_rest_max_seconds'] = max(access['batch_rest_min_seconds'],
        float(configured('batch_rest_max_seconds', 300.0)))
    access['rate_limit_cooldown_level1_seconds'] = max(3600, int(configured('rate_limit_cooldown_level1_seconds', 3600)))
    access['rate_limit_cooldown_level2_seconds'] = max(14400, int(configured('rate_limit_cooldown_level2_seconds', 14400)))
    access['rate_limit_cooldown_level3_seconds'] = max(43200, int(configured('rate_limit_cooldown_level3_seconds', 43200)))
    access.setdefault('rate_limit_count', 0)
    access['rate_limit_level'] = min(3, int(access['rate_limit_count']))
    access.setdefault('cooldown_started_at', None)
    access.setdefault('cooldown_until', None)
    access.setdefault('cooldown_ended_at', None)
    access.setdefault('readonly_preflight_required', bool(access['rate_limit_count']))
    access.setdefault('readonly_preflight_attempt_count', 0)
    access.setdefault('posts_completed_since_rest', 0)
    if access['rate_limit_count'] and access.get('cooldown_started_at'):
        started = _parse_time(access['cooldown_started_at'])
        required = started.timestamp() + cooldown_seconds(access['rate_limit_count'], access)
        current_until = _parse_time(access.get('cooldown_until'))
        if not current_until or current_until.timestamp() < required:
            access['cooldown_until'] = datetime.fromtimestamp(required, timezone.utc).isoformat()
    return access


def cooldown_seconds(rate_limit_count, access):
    count = int(rate_limit_count or 0)
    if count <= 1: return int(access['rate_limit_cooldown_level1_seconds'])
    if count == 2: return int(access['rate_limit_cooldown_level2_seconds'])
    return int(access['rate_limit_cooldown_level3_seconds'])


def _parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def ensure_cooldown_ready(broker, args, now=None):
    access = _access_control(broker.state, args)
    current = now or datetime.now(timezone.utc)
    until = _parse_time(access.get('cooldown_until'))
    if until and current < until:
        broker.save()
        raise RateLimitDetected('rate_limit_cooldown_active_until:' + until.isoformat())
    if until and access.get('cooldown_started_at') and not access.get('cooldown_ended_at'):
        access['cooldown_ended_at'] = current.isoformat()
        access['readonly_preflight_required'] = True
        broker.save()
    return access


def complete_readonly_preflight(broker, out_dir, result):
    access = _access_control(broker.state)
    access['readonly_preflight_attempt_count'] = int(access.get('readonly_preflight_attempt_count') or 0) + 1
    access['readonly_preflight_at'] = utc_now()
    access['readonly_preflight_status'] = result.get('status') or 'unknown'
    ready = result.get('status') == 'ready'
    if ready:
        access['readonly_preflight_required'] = False
        access['recovery_phase'] = 'small_batch'
        broker.state['stop_reason'] = 'readonly_preflight_passed'
    broker.save(); persist_access_manifest(out_dir, broker.state)
    return ready


def recovery_limits(access, args):
    phase = access.get('recovery_phase')
    if phase == 'small_batch': return {'query_budget': 1, 'media_budget': 1}
    if phase == 'ramp': return {'query_budget': 3, 'media_budget': 5}
    return {'query_budget': None, 'media_budget': int(args.target_posts)}


def persist_access_manifest(out_dir, state):
    path = Path(out_dir) / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {
        'schema_version': 'instagram_raw_manifest_v1', 'generated_at': utc_now()
    }
    access = _access_control(state)
    manifest.update({key: access.get(key) for key in (
        'navigation_delay_min_seconds', 'navigation_delay_max_seconds',
        'scroll_delay_min_seconds', 'scroll_delay_max_seconds', 'post_batch_size',
        'batch_rest_min_seconds', 'batch_rest_max_seconds', 'rate_limit_count', 'rate_limit_level',
        'rate_limit_cooldown_level1_seconds', 'rate_limit_cooldown_level2_seconds',
        'rate_limit_cooldown_level3_seconds', 'cooldown_started_at', 'cooldown_until', 'cooldown_ended_at',
        'readonly_preflight_required', 'readonly_preflight_attempt_count', 'readonly_preflight_at',
        'readonly_preflight_status', 'recovery_phase', 'posts_completed_since_rest',
        'last_navigation_delay_seconds', 'last_batch_rest_seconds')})
    if state.get('stop_reason') == 'rate_limited': manifest['stop_reason'] = 'rate_limited'
    atomic_json(path, manifest)


class InstagramAccessGovernor:
    """One process-wide serial gate for every browser job and every rate-limit stop."""
    def __init__(self, runner, broker, args, out_dir, clock=time.monotonic, sleeper=time.sleep,
                 randomizer=random.uniform):
        self.runner, self.broker, self.args = runner, broker, args
        self.out_dir, self.clock, self.sleeper, self.randomizer = Path(out_dir), clock, sleeper, randomizer
        self.lock, self.last_instagram_access = threading.Lock(), 0.0

    def _mark_rate_limited(self):
        access = _access_control(self.broker.state, self.args)
        now = datetime.now(timezone.utc)
        count = int(access.get('rate_limit_count') or 0) + 1
        cooldown = cooldown_seconds(count, access)
        access.update({'rate_limit_count': count, 'rate_limit_level': min(3, count),
                       'cooldown_started_at': now.isoformat(),
                       'cooldown_until': datetime.fromtimestamp(now.timestamp() + cooldown, timezone.utc).isoformat(),
                       'cooldown_ended_at': None, 'readonly_preflight_required': True,
                       'readonly_preflight_status': 'rate_limited'})
        self.broker.state['stop_reason'] = 'rate_limited'
        self.broker.save()
        persist_access_manifest(self.out_dir, self.broker.state)

    def run(self, kind, payload, timeout=900):
        # ponytail: one global lock intentionally serializes all browser work; split only if platform policy changes.
        with self.lock:
            access = _access_control(self.broker.state, self.args)
            interval = random_seconds(access['navigation_delay_min_seconds'],
                                      access['navigation_delay_max_seconds'], self.randomizer)
            delay = instagram_job_delay(payload, self.last_instagram_access, self.clock(), interval)
            if delay: self.sleeper(delay)
            if is_instagram_payload(payload):
                self.last_instagram_access = self.clock()
                access.update({'last_instagram_access_at': utc_now(),
                               'last_navigation_delay_seconds': interval,
                               'last_navigation_sleep_seconds': delay})
                access.setdefault('navigation_delay_history', []).append({
                    'at': access['last_instagram_access_at'], 'selected_seconds': interval,
                    'slept_seconds': delay, 'kind': kind})
                access['navigation_delay_history'] = access['navigation_delay_history'][-100:]
                self.broker.save()
            try:
                result = run_browser_job(self.runner, kind, payload, timeout)
            except BaseException as error:
                if contains_rate_limit(error):
                    self._mark_rate_limited()
                    raise RateLimitDetected('rate_limited') from error
                raise
            if contains_rate_limit(result):
                self._mark_rate_limited()
                raise RateLimitDetected('rate_limited')
            return result

    def rest_if_due(self):
        access = _access_control(self.broker.state, self.args)
        if int(access.get('posts_completed_since_rest') or 0) < int(access['post_batch_size']): return 0.0
        seconds = random_seconds(access['batch_rest_min_seconds'], access['batch_rest_max_seconds'], self.randomizer)
        access.update({'last_batch_rest_seconds': seconds, 'batch_rest_started_at': utc_now()})
        self.broker.save(); self.sleeper(seconds)
        access.update({'posts_completed_since_rest': 0, 'batch_rest_completed_at': utc_now()})
        self.broker.save(); return seconds

    def record_post_completed(self):
        access = _access_control(self.broker.state, self.args)
        access['posts_completed_since_rest'] = int(access.get('posts_completed_since_rest') or 0) + 1
        self.broker.save()


def run_job(broker, kind, payload, timeout=900):
    job = broker.enqueue(kind, payload)
    try: return broker.wait(job['job_id'], timeout)
    except BaseException as error:
        broker.abandon(job['job_id'], str(error) or error.__class__.__name__)
        raise


class CdpTransport:
    def __init__(self, cdp_url, profile_dir, navigation_delay_min_seconds=20.0,
                 navigation_delay_max_seconds=35.0):
        self.cdp_url, self.profile_dir, self.process = cdp_url, Path(profile_dir).resolve(), None
        self.navigation_delay_min_seconds = float(navigation_delay_min_seconds)
        self.navigation_delay_max_seconds = float(navigation_delay_max_seconds)

    def __enter__(self):
        port = urlparse(self.cdp_url).port or 9333
        starter = Path(__file__).with_name('start_instagram_cdp.ps1')
        started = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(starter),
            '-Port', str(port), '-ProfileDir', str(self.profile_dir)], capture_output=True, text=True, encoding='utf-8')
        lines = [line for line in started.stdout.splitlines() if line.strip()]
        status = json.loads(lines[-1]) if lines else {'status': 'start_failed', 'reason': started.stderr.strip()}
        if status.get('status') != 'ready': raise RuntimeError(status.get('reason') or status.get('action') or status['status'])
        worker = Path(__file__).with_name('instagram_playwright_collector.cjs')
        env = dict(os.environ)
        env.setdefault('CODEX_NODE_MODULES', str(Path.home() / '.cache' / 'codex-runtimes' / 'codex-primary-runtime' / 'dependencies' / 'node' / 'node_modules'))
        self.process = subprocess.Popen(['node', str(worker), 'worker', '--cdp-url', self.cdp_url,
            '--navigation-delay-min-ms', str(int(self.navigation_delay_min_seconds * 1000)),
            '--navigation-delay-max-ms', str(int(self.navigation_delay_max_seconds * 1000))], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1, env=env)
        ready = json.loads(self.process.stdout.readline() or '{}')
        if not ready.get('ok'): raise RuntimeError(ready.get('stop_reason') or 'cdp_worker_start_failed')
        self.runtime = {**ready, 'profile_dir': str(self.profile_dir), 'extension_id': None}
        return self

    def run(self, kind, payload, timeout=900):
        request_id = uuid.uuid4().hex
        self.process.stdin.write(json.dumps({'id': request_id, 'kind': kind, 'payload': payload}, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                error = self.process.stderr.read().strip()
                raise RuntimeError(error or 'cdp_worker_disconnected')
            message = json.loads(line)
            if message.get('id') != request_id: continue
            if not message.get('ok'):
                error = RuntimeError(message.get('error') or 'cdp_job_failed')
                error.navigation = message.get('navigation')
                raise error
            return message.get('result') or {}

    def __exit__(self, *_):
        if self.process and self.process.poll() is None:
            self.process.stdin.close()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.process.terminate()


def run_browser_job(runner, kind, payload, timeout=900):
    return runner.run(kind, payload, timeout) if isinstance(runner, CdpTransport) else run_job(runner, kind, payload, timeout)


def query_plan_json(profiles, queries, asins, target_comments, target_posts):
    return {'schema_version': 'instagram_query_plan_v2', 'created_at': utc_now(), 'amazon_asins': asins,
            'target_posts': target_posts, 'target_comments': target_comments,
            'product_profiles': profiles, 'queries': queries,
            'discovery_note': 'Instagram/public-search visible results; not exhaustive platform coverage.'}


def refresh_query_stats(queries, candidates):
    for query in queries:
        name = query.get('query')
        matched = [row for row in candidates if name and
                   (name == row.get('source_query') or name in (row.get('matched_queries') or []))]
        query.setdefault('executed', False)
        query.setdefault('posts_found', len(matched) if query['executed'] else 0)
        query['qualified_posts'] = sum(row.get('decision') == 'COLLECT' for row in matched)
    return queries


def write_query_plan(path, profiles, queries, candidates, asins, target_comments, target_posts):
    refresh_query_stats(queries, candidates)
    atomic_json(path, query_plan_json(profiles, queries, asins, target_comments, target_posts))


def apply_media_gate(candidate, score):
    candidate.update({'relevance_tier': score['tier'], 'relevance_score': score['score'],
        'evidence_terms': score['evidence_terms'], 'evidence_sources': score.get('evidence_sources', []),
        'matched_groups': score.get('matched_groups', []), 'group_hits': score.get('group_hits', {}),
        'relevance_reason': score['reason'],
        'relevance_model_version': score.get('relevance_model_version', discovery.RELEVANCE_MODEL_VERSION),
        'deliverable_eligible': bool(score.get('deliverable_eligible')),
        'decision': 'COLLECT' if score.get('deliverable_eligible') else 'AUDIT_ONLY'})
    return candidate['deliverable_eligible']


def google_search_url(query):
    return 'https://www.google.com/search?q=' + quote('site:instagram.com/p/ OR site:instagram.com/reel/ ' + query)


def instagram_tag_url(query):
    slug = ''.join(re.findall(r'[A-Za-z0-9]+', query)).lower()[:80]
    return f'https://www.instagram.com/explore/tags/{slug}/' if slug else None


def collection_url(candidate):
    """Use the last verified route without rewriting discovery evidence."""
    if candidate.get('working_media_url'): return candidate['working_media_url']
    shortcode = candidate.get('shortcode') or candidate.get('media_id') or discovery.canonical_media(candidate['url'])['shortcode']
    media_type = 'reel' if (candidate.get('requested_media_type') or candidate.get('discovery_media_type') or candidate.get('media_type')) == 'reel' else 'p'
    return f"https://www.instagram.com/{media_type}/{shortcode}/"


def extension_navigation_url(candidate):
    """Use the canonical route; extension v2.5.1 validates it after direct navigation."""
    return collection_url(candidate)


def merge_route_inspection(candidate, metadata, requested_url):
    """Merge rendered evidence without allowing it to rewrite discovery identity."""
    requested = discovery.canonical_media(requested_url) or {}
    rendered_url = metadata.get('rendered_url') or metadata.get('media_url')
    rendered = discovery.canonical_media(rendered_url) or {}
    candidate.setdefault('discovery_url', candidate.get('url'))
    candidate.setdefault('discovery_media_type', candidate.get('media_type'))
    candidate.update({key: metadata.get(key) for key in ('owner','caption','hashtags','visible_text','visible_image_alts',
        'identity_sources','identity_verified','observed_shortcodes','status','inspected_at','shortcode_mismatch_count')
        if key in metadata})
    navigation = metadata.get('navigation') or {}
    navigation_ok = bool((navigation.get('attempts') or [{}])[-1].get('ok'))
    candidate.update({'requested_url': metadata.get('requested_url') or requested_url,
        'requested_media_type': metadata.get('requested_media_type') or requested.get('media_type'),
        'rendered_url': rendered_url, 'rendered_media_type': metadata.get('rendered_media_type') or rendered.get('media_type'),
        'actual_shortcode': metadata.get('media_id') or rendered.get('shortcode'),
        'working_media_url': metadata.get('working_media_url'),
        'route_fallback_attempt_count': int(metadata.get('route_fallback_attempt_count') or 0),
        'route_fallback_success_count': int(metadata.get('route_fallback_success_count') or 0),
        'navigation': navigation, 'navigation_identity_verified': navigation_ok})
    return candidate


def candidate_audit(candidates):
    return [{key: row.get(key) for key in ('shortcode','media_type','url','discovery_media_type','discovery_url',
            'requested_media_type','requested_url','rendered_media_type','rendered_url','working_media_url',
            'route_fallback_attempt_count','route_fallback_success_count','owner','caption','hashtags','matched_queries','matched_asins',
            'relevance_tier','relevance_score','evidence_terms','evidence_sources','relevance_model_version',
            'matched_groups','group_hits','deliverable_eligible','decision','audit_status','page_status','platform_comment_count',
            'main_comment_rows','reply_rows','expand_action_count','scroll_round_count','scroll_action_count','retry_count',
            'stop_reason','last_retry_error','actual_shortcode','shortcode_mismatch_count')}
            for row in candidates]


def media_result_needs_retry(result, retry_partial=True, force_reaudit=False, target_comments=1000):
    """A saved row is resumable evidence, not a permanent success marker."""
    if force_reaudit: return True
    if not result: return True
    actual = int(result.get('comment_count') or 0)
    if actual == 0: return True
    if not retry_partial: return False
    if result.get('audit_status') in ('PARTIAL', 'BLOCKED'): return True
    platform = result.get('platform_comment_count')
    if platform is not None:
        try:
            if actual < min(int(platform), int(target_comments)): return True
        except (TypeError, ValueError): pass
    return False


def _comment_key(row, shortcode=''):
    return ('id', str(row.get('comment_id'))) if row.get('comment_id') else (
        'fallback', shortcode, row.get('author') or row.get('username'), row.get('comment_text'))


def merge_captures(previous, current):
    """Merge retry attempts with exact technical dedupe while preserving reply lineage."""
    if not previous: return current
    merged = dict(current or {})
    media = merged.get('media') or previous.get('media') or {}
    shortcode = media.get('media_id') or media.get('shortcode') or ''
    rows = {}
    for capture in (previous, current or {}):
        for row in capture.get('comments') or []: rows[_comment_key(row, shortcode)] = row
    merged['comments'] = list(rows.values()); merged['media'] = media
    old_page, new_page = previous.get('page') or {}, merged.get('page') or {}
    counts = [value for value in (old_page.get('platform_comment_count'), new_page.get('platform_comment_count'))
              if isinstance(value, (int, float))]
    if counts: new_page['platform_comment_count'] = max(counts)
    new_page['visible_comment_count'] = len(merged['comments']); merged['page'] = new_page
    old_scroll, new_scroll = previous.get('continuous_scroll') or {}, merged.get('continuous_scroll') or {}
    new_scroll['action_ledger'] = (old_scroll.get('action_ledger') or []) + (new_scroll.get('action_ledger') or [])
    new_scroll['attempt_count'] = int(old_scroll.get('attempt_count') or 1) + 1
    new_scroll['final_visible_unique_count'] = len(merged['comments'])
    merged['continuous_scroll'] = new_scroll
    return merged


def capture_identity_check(candidate, capture):
    expected = str(candidate.get('shortcode') or '')
    media = (capture or {}).get('media') or {}
    page = (capture or {}).get('page') or {}
    actual = str(media.get('media_id') or '')
    if not actual:
        identity = discovery.canonical_media(media.get('media_url'))
        actual = str((identity or {}).get('shortcode') or '')
    mismatches = int(page.get('shortcode_mismatch_count') or 0)
    if page.get('identity_verified') is not True: mismatches += 1
    if not actual or actual != expected: mismatches += 1
    observed = set(page.get('observed_shortcodes') or [])
    if actual: observed.add(actual)
    for comment in (capture or {}).get('comments') or []:
        identity = discovery.canonical_media(comment.get('comment_url'))
        code = str((identity or {}).get('shortcode') or '')
        if code: observed.add(code)
        if code and (code != expected or code != actual): mismatches += 1
    return {'ok': mismatches == 0, 'expected_shortcode': expected, 'actual_shortcode': actual or None,
            'observed_shortcodes': sorted(observed), 'shortcode_mismatch_count': mismatches}


def result_from_capture(existing, capture, capture_path, retry_count):
    """Keep the last valid capture authoritative when a later browser retry fails."""
    result = dict(existing or {})
    comments = capture.get('comments') or []
    page = capture.get('page') or {}
    scroll = capture.get('continuous_scroll') or {}
    platform = page.get('platform_comment_count')
    restricted = page.get('status') not in (None, 'ready', 'unknown')
    audit_status = ('BLOCKED' if restricted and not comments else
                    'PASS' if platform is not None and len(comments) >= int(platform) else 'PARTIAL')
    result.update({'capture_path': str(capture_path), 'comment_count': len(comments),
        'stop_reason': scroll.get('stop_reason') or page.get('status'), 'audit_status': audit_status,
        'platform_comment_count': platform, 'retry_count': retry_count,
        'actual_shortcode': page.get('actual_shortcode') or (capture.get('media') or {}).get('media_id'),
        'shortcode_mismatch_count': int(page.get('shortcode_mismatch_count') or 0),
        'collected_comment_ids': [row.get('comment_id') for row in comments if row.get('comment_id')],
        'expanded_thread_signatures': scroll.get('expanded_thread_signatures') or [],
        'scroll_frontier': scroll.get('scroll_frontier') or {}})
    return result


def controller(args):
    args.navigation_delay_min_seconds = max(20.0, float(args.navigation_delay_min_seconds))
    args.navigation_delay_max_seconds = max(args.navigation_delay_min_seconds, float(args.navigation_delay_max_seconds))
    args.scroll_delay_min_seconds = max(3.0, float(args.scroll_delay_min_seconds))
    args.scroll_delay_max_seconds = max(args.scroll_delay_min_seconds, float(args.scroll_delay_max_seconds))
    args.post_batch_size = max(1, int(args.post_batch_size))
    args.batch_rest_min_seconds = max(120.0, float(args.batch_rest_min_seconds))
    args.batch_rest_max_seconds = max(args.batch_rest_min_seconds, float(args.batch_rest_max_seconds))
    args.rate_limit_cooldown_level1_seconds = max(3600, int(args.rate_limit_cooldown_level1_seconds))
    args.rate_limit_cooldown_level2_seconds = max(14400, int(args.rate_limit_cooldown_level2_seconds))
    args.rate_limit_cooldown_level3_seconds = max(43200, int(args.rate_limit_cooldown_level3_seconds))
    out = Path(args.out_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
    evidence = out / 'evidence'; evidence.mkdir(exist_ok=True)
    checkpoint_path = Path(args.resume).resolve() if args.resume else out / 'automation_checkpoint.json'
    asins = parse_asins(args)
    if args.command != 'smoke' and not asins: raise ValueError('Provide --asin, --asins, or --asin-file')
    initial = {'schema_version': 'instagram_automation_checkpoint_v2', 'collector_version': '2.0.0',
               'input_asins': asins, 'settings': {key: getattr(args, key) for key in DEFAULTS},
               'transport': args.transport,
               'product_profiles': [], 'query_plan': [], 'media_candidates': [], 'media_results': {},
               'final_collected_count': 0, 'stop_reason': None}
    broker = JobBroker(checkpoint_path, initial)
    args.collection_goal = (args.collection_goal or broker.state.get('settings', {}).get('collection_goal') or 'target')
    broker.state.setdefault('settings', {})['collection_goal'] = args.collection_goal
    broker.state['collection_goal'] = args.collection_goal
    broker.save()
    access_state = ensure_cooldown_ready(broker, args)
    readonly_preflight_required = bool(access_state.get('readonly_preflight_required'))
    manager = CdpTransport(args.cdp_url, args.cdp_profile_dir, args.navigation_delay_min_seconds,
        args.navigation_delay_max_seconds) if args.transport == 'cdp' else BridgeServer(broker, args.bridge_host, args.bridge_port)
    with manager as active_transport:
        if args.transport == 'cdp':
            runner = active_transport
            runtime = runner.runtime
            access = InstagramAccessGovernor(runner, broker, args, out)
            recovery_phase = _access_control(broker.state, args).get('recovery_phase')
            preflight = None if recovery_phase in ('small_batch', 'ramp') and not readonly_preflight_required else \
                access.run('preflight', {'url': 'https://www.instagram.com/'}, 60)
            if readonly_preflight_required:
                ready = complete_readonly_preflight(broker, out, preflight or {})
                broker.state['cdp_runtime'] = {**runtime, 'readonly_preflight': preflight}; broker.save()
                if not ready: raise RuntimeError((preflight or {}).get('stop_reason') or 'instagram_cdp_login_required')
                print(json.dumps({'output_dir': str(out), 'checkpoint': str(checkpoint_path),
                    'status': 'readonly_preflight_passed', 'next_phase': 'small_batch'}, ensure_ascii=False, indent=2))
                return
            if preflight is not None and preflight.get('status') != 'ready':
                raise RuntimeError(preflight.get('stop_reason') or 'instagram_cdp_login_required')
            broker.state['cdp_runtime'] = {**runtime, 'preflight': preflight}
        else:
            runner = broker
            spec = extension_runtime_spec()
            health_generation = broker.extension_generation
            health = broker.wait_for_extension(health_generation, args.extension_health_timeout)
            runtime = verify_extension_health(health, args.extension_config, spec)
            broker.state['extension_runtime'] = runtime
            access = InstagramAccessGovernor(runner, broker, args, out)
        broker.save()
        queries_executed_this_run = 0
        qualified_before = {row.get('shortcode') for row in broker.state.get('media_candidates', [])
                            if row.get('deliverable_eligible')}
        valid_comments_before = int(broker.state.get('final_collected_count') or 0)
        if args.command == 'smoke':
            identity = discovery.canonical_media(args.smoke_url)
            candidates = [{**identity, 'matched_queries': ['direct_smoke_url'], 'matched_asins': [],
                           'discovery_url': identity['url'], 'discovery_media_type': identity['media_type'],
                           'navigation_source_url': args.smoke_navigation_source_url,
                           'relevance_tier': 'HIGH', 'relevance_score': 1.0, 'evidence_terms': ['direct_smoke_url'], 'decision': 'COLLECT'}]
            profiles, queries = [], []
        else:
            profiles = broker.state.get('product_profiles') or []
            known = {x.get('asin') for x in profiles}
            for asin in asins:
                if asin in known: continue
                try:
                    product = access.run('inspect_product', {'asin': asin, 'url': f'https://www.amazon.com/dp/{asin}'}, 120)
                except RateLimitDetected:
                    raise
                except Exception as error:
                    product = {'asin': asin, 'source_status': 'unavailable', 'source_error': str(error)}
                profiles.append(discovery.build_product_profile(asin, product))
                broker.state['product_profiles'] = profiles; broker.save()
            queries = broker.state.get('query_plan') or discovery.generate_instagram_queries(profiles)
            semantic = broker.state.setdefault('semantic_discovery', {})
            priority_codes = semantic.get('resume_priority_codes') or []
            resume_priority = bool(soft_target_mode(args) and priority_codes and not semantic.get('resume_priority_completed'))
            if not resume_priority and queries and all(row.get('executed') for row in queries):
                if not any(row.get('generation_source') == 'grouped_semantic_gate_v2_expansion' for row in queries):
                    queries += discovery.generate_expansion_queries(profiles, queries)
                elif soft_target_mode(args) and int(semantic.get('consecutive_no_growth_rounds') or 0) < 2:
                    round_number = int(semantic.get('next_round') or 3)
                    generated = discovery.generate_evidence_queries(
                        profiles, broker.state.get('media_candidates') or [], load_comment_evidence(out / 'raw_comments.jsonl'),
                        queries, round_number=round_number)
                    if generated:
                        queries += generated
                        semantic['next_round'] = round_number + 1
            broker.state['query_plan'] = queries; broker.save()
            run_limits = recovery_limits(_access_control(broker.state, args), args)
            atomic_json(out / 'conversation_map.json', {'schema_version': 'instagram_conversation_map_v1', 'product_profiles': profiles})
            candidates = broker.state.get('media_candidates') or []
            write_query_plan(out / 'query_plan.json', profiles, queries, candidates, asins,
                             args.target_comments, args.target_posts)
            for query in ([] if resume_priority else queries):
                if query.get('executed'): continue
                if run_limits['query_budget'] is not None and queries_executed_this_run >= run_limits['query_budget']: break
                sources = [('public_search', google_search_url(query['query']))]
                profile = next((p for p in profiles if p.get('asin') == query.get('asin')), {})
                brand_slug = re.sub(r'[^a-z0-9._]+', '', str(profile.get('brand') or '').lower())
                if query.get('query_family') == 'product_entity' and brand_slug:
                    sources.insert(0, ('instagram_account', f'https://www.instagram.com/{brand_slug}/'))
                tag_url = instagram_tag_url(query['query'])
                if tag_url: sources.append(('instagram_hashtag', tag_url))
                found = []
                for source_kind, search_url in sources:
                    try:
                        result = access.run('discover_query', {'url': search_url, 'query': query['query'],
                            'query_family': query['query_family'], 'source_kind': source_kind, 'max_results': args.posts_per_query}, 150)
                        for row in result.get('candidates', []):
                            found.append({**row, 'source_query': query['query'], 'matched_asins': [query['asin']],
                                'discovery_source': source_kind, 'navigation_source_url': result.get('page_url')})
                    except RateLimitDetected:
                        raise
                    except Exception as error: query.setdefault('errors', []).append(f'{source_kind}:{error}')
                query['executed'] = True; query['posts_found'] = len(found)
                queries_executed_this_run += 1
                candidates = discovery.dedupe_media_candidates(candidates + found)
                broker.state['query_plan'] = queries; broker.state['media_candidates'] = candidates; broker.save()
                write_query_plan(out / 'query_plan.json', profiles, queries, candidates, asins,
                                 args.target_comments, args.target_posts)
                if len(candidates) >= args.target_posts * 3: break
            inspected = []
            media_inspected_this_run = 0
            for row in candidates:
                row.setdefault('discovery_url', row.get('url'))
                row.setdefault('discovery_media_type', row.get('media_type'))
                if row.get('relevance_model_version') == discovery.RELEVANCE_MODEL_VERSION:
                    inspected.append(row); continue
                if media_inspected_this_run >= run_limits['media_budget']:
                    inspected.append(row); continue
                metadata = {}
                requested_url = extension_navigation_url(row)
                try: metadata = access.run('inspect_media', {'url': requested_url, 'shortcode': row['shortcode'],
                    'discovery_url': row.get('discovery_url'), 'discovery_media_type': row.get('discovery_media_type'),
                    'navigation_source_url': row.get('navigation_source_url')}, 150) or {}
                except RateLimitDetected:
                    raise
                except Exception as error: metadata = {'stop_reason': str(error), 'requested_url': requested_url}
                merge_route_inspection(row, metadata, requested_url)
                media_inspected_this_run += 1
                actual = metadata.get('media_id') or row.get('actual_shortcode')
                mismatch = int(metadata.get('shortcode_mismatch_count') or 0) or bool(actual and actual != row['shortcode'])
                inspection_verified = bool(actual == row['shortcode'] and not mismatch and
                    (metadata.get('identity_verified') is True or row.get('navigation_identity_verified')))
                if mismatch:
                    row.update({'stop_reason': 'MEDIA_ID_MISMATCH', 'shortcode_mismatch_count':
                        max(1, int(metadata.get('shortcode_mismatch_count') or 0))})
                elif not inspection_verified:
                    row['stop_reason'] = metadata.get('stop_reason') or 'instagram_media_route_error_page'
                else:
                    row.pop('stop_reason', None)
                    row['inspection_identity_verified'] = True
                score = ({'tier': 'LOW', 'score': 0.0, 'evidence_terms': [], 'reason': 'MEDIA_ID_MISMATCH'}
                         if int(row.get('shortcode_mismatch_count') or 0) else
                         {'tier': 'LOW', 'score': 0.0, 'evidence_terms': [], 'reason': row['stop_reason']}
                         if not inspection_verified else discovery.score_media_relevance(row, profiles))
                apply_media_gate(row, score)
                row['audit_status'] = 'DISCOVERED'
                inspected.append(row); broker.state['media_candidates'] = inspected + candidates[len(inspected):]; broker.save()
            candidates = inspected
            refresh_query_stats(queries, candidates)
            broker.state['query_plan'] = queries; broker.save()
            write_query_plan(out / 'query_plan.json', profiles, queries, candidates, asins,
                             args.target_comments, args.target_posts)
        candidates = discovery.dedupe_media_candidates(candidates)
        run_limits = recovery_limits(_access_control(broker.state, args), args)
        candidates.sort(key=lambda x: ({'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}.get(x.get('relevance_tier'), 3), -float(x.get('relevance_score') or 0)))
        captures = []
        posts_attempted_this_run = 0
        eligible_codes = {row['shortcode'] for row in candidates if row.get('deliverable_eligible')}
        total = sum(int(row.get('comment_count') or 0) for code, row in broker.state.get('media_results', {}).items()
                    if code in eligible_codes)
        collect_candidates = [x for x in candidates if x.get('decision') == 'COLLECT']
        if soft_target_mode(args) and broker.state.get('semantic_discovery', {}).get('resume_priority_codes') and \
                not broker.state.get('semantic_discovery', {}).get('resume_priority_completed'):
            priority = set(broker.state['semantic_discovery']['resume_priority_codes'])
            collect_candidates = [x for x in collect_candidates if x.get('shortcode') in priority][:5]
        for candidate in collect_candidates[:args.target_posts]:
            if profiles and not apply_media_gate(candidate, discovery.score_media_relevance(candidate, profiles)):
                eligible_codes.discard(candidate['shortcode'])
                candidate.update({'audit_status': 'PARTIAL', 'stop_reason': 'semantic_gate_recheck_failed'})
                broker.state['media_candidates'] = candidates; broker.save()
                continue
            existing = broker.state.get('media_results', {}).get(candidate['shortcode'])
            capture_path = Path(existing['capture_path']) if existing and existing.get('capture_path') else evidence / candidate['shortcode'] / 'capture.json'
            previous_capture = json.loads(capture_path.read_text(encoding='utf-8-sig')) if capture_path.exists() else None
            retry_count = int((existing or {}).get('retry_count') or 0)
            if existing and previous_capture:
                existing = result_from_capture(existing, previous_capture, capture_path, retry_count)
                broker.state.setdefault('media_results', {})[candidate['shortcode']] = existing
            should_run = media_result_needs_retry(existing, args.retry_partial, args.force_reaudit, args.target_comments)
            retry_limit = max(args.max_retries_per_post, 2) if soft_target_mode(args) else args.max_retries_per_post
            if existing and (not should_run or retry_count >= retry_limit):
                if capture_path.exists(): captures.append(str(capture_path))
                continue
            if posts_attempted_this_run >= run_limits['media_budget']: break
            access.rest_if_due()
            post_was_attempted = False
            max_attempts = 1 if existing else 1 + args.max_retries_per_post
            mismatch_total = int((existing or {}).get('shortcode_mismatch_count') or candidate.get('shortcode_mismatch_count') or 0)
            for _ in range(max_attempts):
                if hard_target_reached(total, args): break
                post_was_attempted = True
                if previous_capture: retry_count += 1
                remaining = args.target_comments if soft_target_mode(args) else max(1, args.target_comments - total)
                prior_scroll = (previous_capture or {}).get('continuous_scroll') or {}
                payload = {'url': extension_navigation_url(candidate), 'shortcode': candidate['shortcode'],
                    'discovery_url': candidate.get('discovery_url'), 'discovery_media_type': candidate.get('discovery_media_type'),
                    'requested_url': extension_navigation_url(candidate),
                    'navigation_source_url': candidate.get('navigation_source_url'), 'options': {
                    'target_comments': remaining, 'max_rounds': args.max_scroll_rounds_per_post,
                    'max_expand_actions': args.max_expand_actions_per_post, 'idle_rounds': args.no_growth_limit,
                    'delay_min_ms': int(args.scroll_delay_min_seconds * 1000),
                    'delay_max_ms': int(args.scroll_delay_max_seconds * 1000),
                    'max_runtime_ms': args.post_timeout_seconds * 1000,
                    'clicks_per_round': 50, 'max_expand_passes_per_round': 8, 'container_ready_rounds': 12,
                    'expand_replies': args.expand_replies, 'auto_scroll': args.auto_scroll,
                    'retry_count': retry_count,
                    'expected_shortcode': candidate['shortcode'], 'expected_media_type': candidate.get('media_type'),
                    'expected_media_url': candidate.get('working_media_url') or candidate.get('discovery_url') or candidate.get('url'),
                    'existing_comment_ids': [row.get('comment_id') for row in (previous_capture or {}).get('comments', []) if row.get('comment_id')],
                    'expanded_thread_signatures': prior_scroll.get('expanded_thread_signatures') or [],
                    'scroll_frontier': prior_scroll.get('scroll_frontier') or {},
                }}
                try: current = access.run('collect_media', payload, args.post_timeout_seconds + 120) or {}
                except RateLimitDetected:
                    raise
                except Exception as error:
                    if existing and previous_capture:
                        result = result_from_capture(existing, previous_capture, capture_path, retry_count)
                        result.update({'last_retry_error': str(error), 'last_retry_at': utc_now()})
                        candidate.update({'audit_status': result['audit_status'], 'stop_reason': result['stop_reason'],
                            'retry_count': retry_count, 'last_retry_error': str(error)})
                    else:
                        candidate.update({'audit_status': 'BLOCKED', 'stop_reason': str(error), 'retry_count': retry_count,
                            'last_retry_error': str(error)})
                        result = {'capture_path': str(capture_path), 'comment_count': 0,
                                  'stop_reason': str(error), 'audit_status': 'BLOCKED', 'retry_count': retry_count,
                                  'last_retry_error': str(error), 'last_retry_at': utc_now()}
                    broker.state.setdefault('media_results', {})[candidate['shortcode']] = result; broker.save(); break
                identity = capture_identity_check(candidate, current)
                if not identity['ok']:
                    mismatch_total += identity['shortcode_mismatch_count']
                    current['comments'] = []
                    current.setdefault('media', {})['identity_verified'] = False
                    current.setdefault('page', {}).update({'status': 'MEDIA_ID_MISMATCH', 'identity_verified': False,
                        'actual_shortcode': identity['actual_shortcode'], 'expected_shortcode': identity['expected_shortcode'],
                        'observed_shortcodes': identity['observed_shortcodes'],
                        'shortcode_mismatch_count': identity['shortcode_mismatch_count']})
                    current.setdefault('continuous_scroll', {})['stop_reason'] = 'MEDIA_ID_MISMATCH'
                    previous_capture = None
                    retry_count += 1
                capture = merge_captures(previous_capture, current)
                capture.setdefault('media', {}).setdefault('discovery', {})
                capture['media']['discovery'].update({'matched_asins': candidate.get('matched_asins', asins),
                    'matched_queries': candidate.get('matched_queries', []), 'relevance_tier': candidate.get('relevance_tier'),
                    'relevance_score': candidate.get('relevance_score'), 'evidence_terms': candidate.get('evidence_terms', []),
                    'evidence_sources': candidate.get('evidence_sources', []),
                    'matched_groups': candidate.get('matched_groups', []), 'group_hits': candidate.get('group_hits', {}),
                    'relevance_model_version': candidate.get('relevance_model_version'),
                    'deliverable_eligible': bool(candidate.get('deliverable_eligible')),
                    'discovery_url': candidate.get('discovery_url'),
                    'discovery_media_type': candidate.get('discovery_media_type'),
                    'shortcode_mismatch_count': mismatch_total})
                for comment in capture.get('comments') or []:
                    if profiles: comment['semantic'] = discovery.classify_comment_relevance(comment.get('comment_text'), profiles)
                    else: comment['semantic'] = {'tier': 'UNSCORED', 'score': None, 'evidence_terms': [], 'reason': 'smoke_without_asin'}
                media_dir = evidence / candidate['shortcode']; media_dir.mkdir(parents=True, exist_ok=True)
                capture_path = media_dir / 'capture.json'; atomic_json(capture_path, capture)
                continuous = capture.get('continuous_scroll') or {}; page = capture.get('page') or {}
                atomic_json(media_dir / 'action_ledger.json', continuous.get('action_ledger', []))
                unique_count = len(capture.get('comments') or [])
                replies = sum(bool(row.get('parent_comment_id')) or int(row.get('depth') or 0) > 0 for row in capture.get('comments') or [])
                restricted = page.get('status') not in (None, 'ready', 'unknown')
                platform = page.get('platform_comment_count')
                audit_status = ('BLOCKED' if restricted and not unique_count else
                                'PASS' if platform is not None and unique_count >= int(platform) else 'PARTIAL')
                candidate.update({'audit_status': audit_status, 'page_status': page.get('status'),
                    'actual_shortcode': page.get('actual_shortcode') or (capture.get('media') or {}).get('media_id'),
                    'shortcode_mismatch_count': mismatch_total,
                    'platform_comment_count': platform, 'main_comment_rows': unique_count - replies, 'reply_rows': replies,
                    'expand_action_count': continuous.get('expand_action_count'), 'scroll_round_count': continuous.get('round_count'),
                    'scroll_action_count': continuous.get('scroll_action_count'),
                    'stop_reason': continuous.get('stop_reason') or page.get('status'), 'retry_count': retry_count})
                result = {'capture_path': str(capture_path), 'comment_count': unique_count,
                    'stop_reason': candidate['stop_reason'], 'audit_status': audit_status,
                    'platform_comment_count': platform, 'retry_count': retry_count,
                    'actual_shortcode': candidate.get('actual_shortcode'),
                    'shortcode_mismatch_count': candidate.get('shortcode_mismatch_count', 0),
                    'working_media_url': (capture.get('navigation') or {}).get('working_media_url') or candidate.get('working_media_url'),
                    'route_fallback_attempt_count': int((capture.get('navigation') or {}).get('route_fallback_attempt_count') or 0),
                    'route_fallback_success_count': int((capture.get('navigation') or {}).get('route_fallback_success_count') or 0),
                    'collected_comment_ids': [row.get('comment_id') for row in capture.get('comments') or [] if row.get('comment_id')],
                    'expanded_thread_signatures': continuous.get('expanded_thread_signatures') or [],
                    'scroll_frontier': continuous.get('scroll_frontier') or {}}
                broker.state.setdefault('media_results', {})[candidate['shortcode']] = result
                total = sum(int(row.get('comment_count') or 0) for code, row in broker.state['media_results'].items()
                            if code in eligible_codes)
                broker.state['final_collected_count'] = total; broker.state['media_candidates'] = candidates; broker.save()
                previous_capture = capture
                if not media_result_needs_retry(result, args.retry_partial, False, args.target_comments): break
            if capture_path.exists() and str(capture_path) not in captures: captures.append(str(capture_path))
            if post_was_attempted:
                posts_attempted_this_run += 1
                access.record_post_completed()
            if hard_target_reached(total, args): break
        semantic = broker.state.setdefault('semantic_discovery', {})
        if soft_target_mode(args) and semantic.get('resume_priority_codes') and not semantic.get('resume_priority_completed'):
            attempted_codes = {row.get('shortcode') for row in collect_candidates[:args.target_posts]}
            if attempted_codes.issuperset(set(semantic['resume_priority_codes'])):
                semantic['resume_priority_completed'] = True
        for candidate in candidates:
            if not candidate.get('audit_status') or candidate.get('audit_status') == 'DISCOVERED':
                candidate['audit_status'] = 'PARTIAL'
                candidate['stop_reason'] = ('semantic_low_audit_only' if candidate.get('relevance_tier') == 'LOW'
                                            else 'target_comments_reached' if hard_target_reached(total, args)
                                            else 'target_posts_budget')
        atomic_json(out / 'media_candidates.json', candidates)
        atomic_json(out / 'post_audit.json', candidate_audit(candidates))
        if soft_target_mode(args) and queries_executed_this_run:
            status = update_semantic_saturation(
                broker.state, len(eligible_codes - qualified_before), max(0, total - valid_comments_before),
                f"evidence_discovery_round_{len(broker.state.get('semantic_discovery', {}).get('history', [])) + 1}")
            if status == 'semantic_saturation': broker.state['stop_reason'] = 'semantic_saturation'
            broker.save()
        if captures:
            command = [sys.executable, str(Path(__file__).with_name('instagram_collector.py')), 'ingest']
            if asins: command += ['--asins', ','.join(asins)]
            else: command += ['--smoke']
            command += ['--captures', *captures, '--target-comments', str(args.target_comments),
                        '--out-dir', str(out), '--candidate-audit', str(out / 'post_audit.json')]
            if soft_target_mode(args): command += ['--soft-target', 'true']
            run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            if run.returncode: raise RuntimeError(run.stderr.strip())
            result = json.loads(run.stdout)
        else: result = {'output_dir': str(out), 'manifest': {'final_collected_count': 0, 'stop_reason': 'no_qualified_media'}}
        current_access = _access_control(broker.state, args)
        if current_access.get('recovery_phase') == 'small_batch' and queries_executed_this_run >= 1 and posts_attempted_this_run >= 1:
            current_access['recovery_phase'] = 'ramp'
        elif current_access.get('recovery_phase') == 'ramp' and posts_attempted_this_run >= 1:
            current_access['recovery_phase'] = 'normal'
        broker.state['stop_reason'] = result['manifest'].get('stop_reason'); broker.save()
        persist_access_manifest(out, broker.state)
        if args.transport == 'cdp':
            broker.state['pending_jobs'] = []
            broker.state['active_jobs'] = {}
            broker.save()
        print(json.dumps({'output_dir': str(out), 'checkpoint': str(checkpoint_path),
                          'candidates': len(candidates), 'captures': len(captures), **result}, ensure_ascii=False, indent=2))


def boolean(value):
    if isinstance(value, bool): return value
    return str(value).lower() not in ('0','false','no','off')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', nargs='?', choices=['collect','smoke'], default='collect')
    p.add_argument('--asin'); p.add_argument('--asins'); p.add_argument('--asin-file'); p.add_argument('--smoke-url')
    p.add_argument('--smoke-navigation-source-url')
    p.add_argument('--collection-goal', choices=['target', MAXIMIZE_RELEVANT_COMMENTS])
    p.add_argument('--target-posts', type=int, default=30); p.add_argument('--target-comments', type=int, default=1000)
    p.add_argument('--posts-per-query', type=int, default=10)
    p.add_argument('--max-scroll-rounds-per-post', type=int, default=200)
    p.add_argument('--max-expand-actions-per-post', type=int, default=600)
    p.add_argument('--scroll-delay-min-seconds', type=float, default=3.0)
    p.add_argument('--scroll-delay-max-seconds', type=float, default=6.0)
    p.add_argument('--navigation-delay-min-seconds', type=float, default=20.0)
    p.add_argument('--navigation-delay-max-seconds', type=float, default=35.0)
    p.add_argument('--post-batch-size', type=int, default=5)
    p.add_argument('--batch-rest-min-seconds', type=float, default=120.0)
    p.add_argument('--batch-rest-max-seconds', type=float, default=300.0)
    p.add_argument('--rate-limit-cooldown-level1-seconds', type=int, default=3600)
    p.add_argument('--rate-limit-cooldown-level2-seconds', type=int, default=14400)
    p.add_argument('--rate-limit-cooldown-level3-seconds', type=int, default=43200)
    p.add_argument('--no-growth-limit', type=int, default=5)
    p.add_argument('--expand-replies', type=boolean, default=True); p.add_argument('--auto-scroll', type=boolean, default=True)
    p.add_argument('--checkpoint-enabled', type=boolean, default=True); p.add_argument('--resume-enabled', type=boolean, default=True)
    p.add_argument('--manual-start-required', type=boolean, default=False)
    p.add_argument('--retry-partial', type=boolean, default=True)
    p.add_argument('--force-reaudit', type=boolean, default=False)
    p.add_argument('--max-retries-per-post', type=int, default=1)
    p.add_argument('--post-timeout-seconds', type=int, default=900)
    p.add_argument('--resume'); p.add_argument('--out-dir', default='outputs/instagram')
    p.add_argument('--transport', choices=['cdp', 'extension', 'chrome'], default='cdp')
    p.add_argument('--cdp-url', default='http://127.0.0.1:9333')
    p.add_argument('--cdp-profile-dir', default=str(Path(__file__).resolve().parents[1] / 'outputs' / 'instagram-cdp-profile'))
    p.add_argument('--bridge-host', default='127.0.0.1'); p.add_argument('--bridge-port', type=int, default=8765)
    p.add_argument('--extension-health-timeout', type=int, default=45)
    p.add_argument('--extension-config', default=str(Path.home() / '.codex' / 'skills' / 'instagram-voc-collector' / 'extension_runtime.json'))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == 'smoke' and not args.smoke_url: parser().error('--smoke-url is required for smoke')
    return controller(args)


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, RuntimeError, TimeoutError) as error:
        print('ERROR: ' + str(error), file=sys.stderr); raise SystemExit(2)
