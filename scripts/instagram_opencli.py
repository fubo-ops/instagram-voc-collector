#!/usr/bin/env python3
"""Small OpenCLI helpers for a user-owned, logged-in Chrome Instagram tab."""

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


COMMENT_BUTTON_SELECTOR = 'div[role="button"][aria-haspopup="menu"][aria-expanded]'
MEDIA_RE = re.compile(r'/(?:[A-Za-z0-9._]+/)?(?:p|reels?)/([A-Za-z0-9_-]+)(?:/|$)')
RATE_LIMIT_RE = re.compile(
    r'http error 429|too many requests|try again later|rate[_ -]?limited|'
    r'操作过于频繁|稍后再试', re.I)
EXPANSION_RE = re.compile(
    r'^(?:load|show|view)\s+(?:all\s+)?(?:previous\s+)?(?:\d+\s+)?(?:more\s+)?comments?'
    r'(?:\s*\(\d+\))?$|^(?:view|show|load)\s+(?:all\s+)?(?:\d+\s+)?(?:more\s+)?'
    r'repl(?:y|ies)(?:\s*\(\d+\))?$|^(?:加载|显示|查看)(?:全部|所有|更多|之前的)?\s*\d*\s*条?'
    r'(?:更多)?评论$|^查看更多评论$|^(?:查看|显示|加载)(?:全部|所有|更多)?'
    r'\s*\d*\s*条?(?:更多)?回复$', re.I)


PROBE_JS = r'''(() => {
  const buttons = [...document.querySelectorAll('div[role="button"][aria-haspopup="menu"][aria-expanded]')]
    .map((element, index) => {
      const rect = element.getBoundingClientRect();
      return {
        index,
        expanded: element.getAttribute('aria-expanded') === 'true',
        in_viewport: rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight,
        distance: Math.abs((rect.top + rect.bottom) / 2 - innerHeight / 2),
        rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
        text: String(element.innerText || '').trim()
      };
    });
  const text = String(document.body?.innerText || '').slice(0, 7000);
  return {
    href: location.href,
    pathname: location.pathname,
    canonical: document.querySelector('link[rel="canonical"]')?.href || '',
    og_url: document.querySelector('meta[property="og:url"]')?.content || '',
    comment_dialog_open: [...document.querySelectorAll('[role="dialog"]')]
      .some(node => /(?:^|\n)(?:comments?|评论)(?:\n|$)/i.test(String(node.innerText || ''))),
    body_text: text,
    viewport: {
      screen_x: screenX, screen_y: screenY, outer_width: outerWidth, outer_height: outerHeight,
      inner_width: innerWidth, inner_height: innerHeight,
      has_been_active: navigator.userActivation?.hasBeenActive === true
    },
    buttons
  };
})()'''


READ_COMMENTS_JS = r'''(() => {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = element => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0; };
  const commentId = raw => { try { return new URL(raw, location.href).pathname.match(/\/c\/(\d+)/)?.[1] || null; } catch (_) { return null; } };
  const profile = link => { try { return /^\/[A-Za-z0-9._]+\/$/.test(new URL(link.href).pathname); } catch (_) { return false; } };
  const hasReply = node => [...node.querySelectorAll('button,[role="button"]')].some(el => /^(reply|回复)$/i.test(clean(el.textContent)));
  const nodes = [];
  for (const time of document.querySelectorAll('time')) {
    const permalink = time.closest('a[href]');
    if (!permalink || !commentId(permalink.href)) continue;
    let node = time.parentElement;
    for (let depth = 0; node && node !== document.body && depth < 12; depth += 1, node = node.parentElement) {
      if (hasReply(node) && [...node.querySelectorAll('a[href]')].some(link => profile(link) && clean(link.textContent))) { nodes.push(node); break; }
    }
  }
  const unique = [...new Set(nodes)].filter(visible);
  const comments = [];
  for (const node of unique) {
    const authorLink = [...node.querySelectorAll('a[href]')].find(link => profile(link) && clean(link.textContent) && visible(link));
    const author = authorLink ? clean(authorLink.textContent).replace(/已验证|verified/ig, '').replace(/^@/, '').trim() : null;
    const link = [...node.querySelectorAll('a[href]')].find(anchor => commentId(anchor.href));
    const id = link ? commentId(link.href) : null;
    const time = node.querySelector('time');
    const ignored = new Set([...node.querySelectorAll('button,[role="button"]')].map(el => clean(el.textContent)).filter(Boolean));
    if (time) ignored.add(clean(time.textContent));
    const text = String(node.innerText || '').split(/\r?\n/).map(clean).filter(Boolean).filter(value => {
      const plain = value.replace(/已验证|verified/ig, '').trim();
      return plain !== author && !ignored.has(value) && !/^(reply|回复|like|赞|查看翻译|see translation)$/i.test(value) &&
        !/^\d[\d,.]*\s*(?:likes?|次赞|赞)$/i.test(value);
    }).join(' ').trim();
    if (!id || !author || !text || text === author) continue;
    let parent = null;
    const replyList = node.closest('ul');
    const parentCard = replyList?.parentElement?.closest('li');
    if (parentCard && !replyList.contains(parentCard)) {
      const parentTime = [...parentCard.querySelectorAll('time')].find(candidate => {
        const parentLink = candidate.closest('a[href]');
        return !replyList.contains(candidate) && parentLink && commentId(parentLink.href);
      });
      if (parentTime) parent = commentId(parentTime.closest('a[href]').href);
    }
    const likes = clean(node.innerText).match(/(?:^|\s)(\d[\d,.]*)\s*(?:likes?|次赞|赞)(?:\s|$)/i);
    comments.push({comment_id:id, parent_comment_id:parent, depth:parent ? 1 : 0, author,
      comment_text:text, published_at:time?.getAttribute('datetime') || null,
      published_label:time ? clean(time.textContent) : null, like_count:likes ? likes[1] : null,
      comment_url:link.href});
  }
  const body = String(document.body?.innerText || '');
  const buttons = [...document.querySelectorAll('div[role="button"][aria-haspopup="menu"][aria-expanded]')]
    .filter(visible).sort((a,b) => Math.abs(a.getBoundingClientRect().top-innerHeight/2)-Math.abs(b.getBoundingClientRect().top-innerHeight/2));
  const countText = clean(buttons[0]?.innerText || '').match(/[\d,.]+(?:\.\d+)?\s*(?:万|k|m)?/i)?.[0] || '';
  return {href:location.href, pathname:location.pathname,
    canonical:document.querySelector('link[rel="canonical"]')?.href || '',
    og_url:document.querySelector('meta[property="og:url"]')?.content || '', body_text:body.slice(0,7000),
    platform_comment_count_text:countText, comments};
})()'''


def media_shortcode(value):
    match = MEDIA_RE.search(str(value or ''))
    return match.group(1) if match else None


def validate_identity(probe, expected_shortcode=None):
    observed = [media_shortcode(probe.get(key)) for key in ('href', 'pathname', 'canonical', 'og_url')]
    observed = [value for value in observed if value]
    if not observed:
        raise RuntimeError('instagram_media_identity_missing')
    if len(set(observed)) != 1:
        raise RuntimeError('MEDIA_ID_MISMATCH')
    actual = observed[0]
    if expected_shortcode and actual != expected_shortcode:
        raise RuntimeError('MEDIA_ID_MISMATCH')
    return actual


def choose_comment_button(buttons):
    viewport = [row for row in buttons if row.get('in_viewport')]
    if not viewport:
        return None
    expanded = [row for row in viewport if row.get('expanded')]
    pool = expanded or viewport
    return min(pool, key=lambda row: (float(row.get('distance') or 0), int(row.get('index') or 0)))


def native_click_point(probe, button, window_rect):
    viewport, rect = probe.get('viewport') or {}, button.get('rect') or {}
    required = ('outer_width', 'outer_height', 'inner_width', 'inner_height')
    if any(float(viewport.get(key) or 0) <= 0 for key in required):
        raise RuntimeError('native_click_viewport_missing')
    left, top, right, bottom = map(float, window_rect)
    scale_x = (right - left) / float(viewport['outer_width'])
    scale_y = (bottom - top) / float(viewport['outer_height'])
    border_x = max(0.0, (float(viewport['outer_width']) - float(viewport['inner_width'])) / 2.0)
    content_y = max(0.0, float(viewport['outer_height']) - float(viewport['inner_height']) - border_x)
    x = left + (border_x + float(rect.get('x') or 0) + float(rect.get('width') or 0) / 2.0) * scale_x
    y = top + (content_y + float(rect.get('y') or 0) + float(rect.get('height') or 0) / 2.0) * scale_y
    return round(x), round(y)


def native_windows_click(probe, button):
    if os.name != 'nt':
        raise RuntimeError('native_click_windows_only')
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    candidates = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        value = title.value.lower()
        if 'instagram' in value and ('chrome' in value or '谷歌' in value):
            candidates.append(hwnd)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    foreground = user32.GetForegroundWindow()
    if foreground in candidates:
        hwnd = foreground
    elif candidates:
        viewport = probe.get('viewport') or {}
        expected_width = float(viewport.get('inner_width') or 0) + 16
        expected_height = float(viewport.get('inner_height') or 0) + 185
        def score(candidate):
            rect = wintypes.RECT()
            user32.GetWindowRect(candidate, ctypes.byref(rect))
            return abs((rect.right - rect.left) - expected_width) + abs((rect.bottom - rect.top) - expected_height)
        hwnd = min(candidates, key=score)
    else:
        hwnd = None
    if not hwnd:
        raise RuntimeError('instagram_chrome_window_not_found')
    bounds = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(bounds)):
        raise RuntimeError('instagram_chrome_window_bounds_unavailable')
    effective = dict(probe)
    viewport = dict(probe.get('viewport') or {})
    width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
    viewport['outer_width'] = float(viewport.get('outer_width') or width)
    viewport['outer_height'] = float(viewport.get('outer_height') or height)
    viewport['inner_width'] = float(viewport.get('inner_width') or max(1, width - 16))
    viewport['inner_height'] = float(viewport.get('inner_height') or max(1, height - 185))
    effective['viewport'] = viewport
    x, y = native_click_point(effective, button, (bounds.left, bounds.top, bounds.right, bounds.bottom))
    original = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(original))
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    user32.SetCursorPos(x, y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.15)
    user32.SetCursorPos(original.x, original.y)
    return {'x': x, 'y': y, 'window_rect': [bounds.left, bounds.top, bounds.right, bounds.bottom]}


class OpenCli:
    def __init__(self, session, executable=None):
        self.session = session
        self.executable = executable or shutil.which('opencli')
        if not self.executable:
            raise RuntimeError('opencli_not_found')
        self.command_prefix = opencli_command_prefix(self.executable)

    def run(self, *args, parse_json=False):
        command = [*self.command_prefix, 'browser', self.session, *map(str, args)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or 'opencli_command_failed').strip())
        output = result.stdout.strip()
        if not parse_json:
            return output
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError(f'opencli_invalid_json:{error}') from error

    def wait(self, seconds):
        time.sleep(float(seconds))

    def probe(self):
        return self.run('eval', PROBE_JS, parse_json=True)

    def activate_session_tab(self):
        tabs = self.run('tab', 'list', parse_json=True)
        if len(tabs) == 1 and not tabs[0].get('active'):
            self.run('tab', 'select', tabs[0]['page'], parse_json=True)
            self.wait(1)
        return tabs[0] if len(tabs) == 1 else None

    def find(self, selector, limit=500, text_max=160):
        try:
            return self.run('find', '--css', selector, '--limit', str(limit), '--text-max', str(text_max), parse_json=True)
        except RuntimeError as error:
            if 'selector_not_found' in str(error):
                return {'entries': [], 'matches_n': 0}
            raise


def opencli_command_prefix(executable, platform_name=None):
    """Bypass npm .cmd on Windows so JS operators are not parsed by cmd.exe."""
    platform_name = platform_name or os.name
    path = Path(executable)
    if platform_name == 'nt' and path.suffix.lower() in ('.cmd', '.ps1'):
        entry = path.parent / 'node_modules' / '@jackwener' / 'opencli' / 'dist' / 'src' / 'main.js'
        node = path.parent / 'node.exe'
        node = node if node.exists() else Path(shutil.which('node') or 'node')
        if entry.exists():
            return [str(node), str(entry)]
    return [str(path)]


def random_wait(cli, low, high, rng):
    seconds = rng.uniform(low, high)
    cli.wait(seconds)
    return round(seconds, 3)


def is_expansion_label(value):
    return bool(EXPANSION_RE.fullmatch(re.sub(r'\s+', ' ', str(value or '')).strip()))


def merge_comment_rows(existing, incoming):
    rows = {str(row['comment_id']): row for row in existing if row.get('comment_id')}
    for row in incoming:
        if row.get('comment_id'):
            rows.setdefault(str(row['comment_id']), row)
    return list(rows.values())


def collect_current_comments(cli, expected_shortcode=None, target_comments=1000, max_rounds=200,
                             no_growth_limit=5, wait_min=3.0, wait_max=6.0,
                             progress_out=None, rng=random):
    opened = open_comments(cli, expected_shortcode, wait_min, wait_max, rng)
    progress_path = Path(progress_out) if progress_out else None
    saved = []
    if progress_path and progress_path.exists():
        saved = json.loads(progress_path.read_text(encoding='utf-8')).get('comments', [])
    rows = merge_comment_rows([], saved)
    idle = expand_count = scroll_count = 0
    control_attempts = {}
    last_control_key = None
    waits = list(opened.get('wait_seconds') or [])
    last = None
    stop_reason = 'max_scroll_rounds'
    for round_number in range(1, max_rounds + 1):
        state = cli.run('eval', READ_COMMENTS_JS, parse_json=True)
        if RATE_LIMIT_RE.search(state.get('body_text') or ''):
            stop_reason = 'rate_limited'
            break
        validate_identity(state, expected_shortcode or opened['shortcode'])
        before = len(rows)
        rows = merge_comment_rows(rows, state.get('comments') or [])
        if len(rows) > before and last_control_key:
            control_attempts[last_control_key] = 0
        if len(rows) >= target_comments:
            stop_reason = 'target_comments'
        controls = cli.find('button,[role="button"]')
        expandable = [entry for entry in controls.get('entries', []) if entry.get('visible') and
                      is_expansion_label(entry.get('text')) and
                      control_attempts.get(f"{entry.get('ref')}|{entry.get('text')}", 0) < 2]
        action = 'collect'
        if stop_reason != 'target_comments' and expandable:
            control = expandable[0]
            last_control_key = f"{control.get('ref')}|{control.get('text')}"
            cli.run('click', str(control['ref']), parse_json=True)
            control_attempts[last_control_key] = control_attempts.get(last_control_key, 0) + 1
            expand_count += 1
            action = 'expand'
        elif stop_reason != 'target_comments':
            last_control_key = None
            links = cli.find('a[href*="/c/"]', text_max=80)
            visible_links = [entry for entry in links.get('entries', []) if entry.get('visible')]
            candidates = visible_links or links.get('entries', [])
            if candidates:
                current = candidates[-1]
                last = current.get('ref')
                cli.run('focus', str(last), parse_json=True)
                scroll_count += 1
                action = 'focus_frontier'
            else:
                stop_reason = 'comment_container_not_ready'
        if len(rows) == before and action != 'expand':
            idle += 1
        else:
            idle = 0
        wait_seconds = random_wait(cli, wait_min, wait_max, rng)
        waits.append(wait_seconds)
        payload = {'schema_version':'instagram_opencli_progress_v1', 'shortcode':opened['shortcode'],
                   'comments':rows, 'round_count':round_number, 'expand_action_count':expand_count,
                   'scroll_action_count':scroll_count, 'idle_round_count':idle,
                   'last_frontier_ref':last, 'stop_reason':stop_reason, 'updated_at':time.time()}
        if progress_path:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            temp = progress_path.with_suffix(progress_path.suffix + '.tmp')
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temp.replace(progress_path)
        print(json.dumps({'round':round_number, 'comments':len(rows), 'new':len(rows)-before,
                          'action':action, 'idle':idle}, ensure_ascii=False), flush=True)
        if stop_reason in ('target_comments', 'rate_limited', 'comment_container_not_ready'):
            break
        if idle >= no_growth_limit:
            stop_reason = 'visible_comments_exhausted'
            break
    final_state = cli.run('eval', READ_COMMENTS_JS, parse_json=True)
    rows = merge_comment_rows(rows, final_state.get('comments') or [])
    return {'status':'PARTIAL' if stop_reason != 'target_comments' else 'PASS',
            'shortcode':opened['shortcode'], 'platform_comment_count_text':final_state.get('platform_comment_count_text'),
            'comments':rows, 'round_count':round_number, 'expand_action_count':expand_count,
            'scroll_action_count':scroll_count, 'stop_reason':stop_reason, 'wait_seconds':waits,
            'comment_panel':opened}


def open_comments(cli, expected_shortcode=None, wait_min=3.0, wait_max=6.0, rng=random):
    cli.activate_session_tab()
    probe = cli.probe()
    if RATE_LIMIT_RE.search(probe.get('body_text') or ''):
        raise RuntimeError('rate_limited')
    actual_shortcode = validate_identity(probe, expected_shortcode)
    button = choose_comment_button(probe.get('buttons') or [])
    if probe.get('comment_dialog_open') or (button and button.get('expanded')):
        return {'status': 'ready', 'shortcode': actual_shortcode, 'already_open': True,
                'attempts': 0, 'wait_seconds': []}
    if not button:
        raise RuntimeError('comment_trigger_not_found')

    waits = []
    selector_index = int(button['index'])
    waits.append(random_wait(cli, wait_min, wait_max, rng))
    cli.run('hover', COMMENT_BUTTON_SELECTOR, '--nth', selector_index, parse_json=True)
    waits.append(random_wait(cli, wait_min, wait_max, rng))
    cli.run('click', COMMENT_BUTTON_SELECTOR, '--nth', selector_index, parse_json=True)
    waits.append(random_wait(cli, wait_min, wait_max, rng))
    after = cli.probe()
    validate_identity(after, actual_shortcode)
    current = choose_comment_button(after.get('buttons') or [])
    if after.get('comment_dialog_open') or (current and current.get('expanded')):
        return {'status': 'ready', 'shortcode': actual_shortcode, 'already_open': False,
                'attempts': 1, 'wait_seconds': waits}

    waits.append(random_wait(cli, wait_min, wait_max, rng))
    cli.run('focus', COMMENT_BUTTON_SELECTOR, '--nth', selector_index, parse_json=True)
    cli.run('keys', 'Enter')
    waits.append(random_wait(cli, wait_min, wait_max, rng))
    after = cli.probe()
    validate_identity(after, actual_shortcode)
    current = choose_comment_button(after.get('buttons') or [])
    if after.get('comment_dialog_open') or (current and current.get('expanded')):
        return {'status': 'ready', 'shortcode': actual_shortcode, 'already_open': False,
                'attempts': 2, 'activation_method': 'keyboard', 'wait_seconds': waits}

    current = current or button
    waits.append(random_wait(cli, wait_min, wait_max, rng))
    native_result = native_windows_click(after, current)
    waits.append(random_wait(cli, wait_min, wait_max, rng))
    after = cli.probe()
    validate_identity(after, actual_shortcode)
    current = choose_comment_button(after.get('buttons') or [])
    if after.get('comment_dialog_open') or (current and current.get('expanded')):
        return {'status': 'ready', 'shortcode': actual_shortcode, 'already_open': False,
                'attempts': 3, 'activation_method': 'native_windows_click',
                'native_click': native_result, 'wait_seconds': waits}
    current = current or button
    waits.append(random_wait(cli, wait_min, wait_max, rng))
    native_result = native_windows_click(after, current)
    waits.append(random_wait(cli, wait_min, wait_max, rng))
    after = cli.probe()
    validate_identity(after, actual_shortcode)
    current = choose_comment_button(after.get('buttons') or [])
    if after.get('comment_dialog_open') or (current and current.get('expanded')):
        return {'status': 'ready', 'shortcode': actual_shortcode, 'already_open': False,
                'attempts': 4, 'activation_method': 'native_windows_click_retry',
                'native_click': native_result, 'wait_seconds': waits}
    raise RuntimeError('comment_panel_not_open')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['open-comments', 'collect-current'])
    parser.add_argument('--session', default='instagram-voc')
    parser.add_argument('--shortcode')
    parser.add_argument('--wait-min', type=float, default=3.0)
    parser.add_argument('--wait-max', type=float, default=6.0)
    parser.add_argument('--keep-bound', action='store_true')
    parser.add_argument('--target-comments', type=int, default=1000)
    parser.add_argument('--max-rounds', type=int, default=200)
    parser.add_argument('--no-growth-limit', type=int, default=5)
    parser.add_argument('--progress-out')
    parser.add_argument('--result-out')
    args = parser.parse_args(argv)
    if args.wait_min < 3 or args.wait_max < args.wait_min:
        parser.error('wait range must be at least 3 seconds and max >= min')
    cli = OpenCli(args.session)
    cli.run('bind', parse_json=True)
    try:
        if args.command == 'open-comments':
            result = open_comments(cli, args.shortcode, args.wait_min, args.wait_max)
        else:
            result = collect_current_comments(cli, args.shortcode, args.target_comments, args.max_rounds,
                                              args.no_growth_limit, args.wait_min, args.wait_max,
                                              args.progress_out)
        if args.result_out:
            output = Path(args.result_out); output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result if args.command == 'open-comments' else
                         {key:value for key,value in result.items() if key != 'comments'}, ensure_ascii=False))
    finally:
        if not args.keep_bound:
            cli.run('unbind')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError) as error:
        print(json.dumps({'status': 'error', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
