#!/usr/bin/env python3
"""Drive the open Unity editor through the MCP for Unity server (HTTP transport).

usage: python .claude/scripts/unity-mcp.py <step> [<step> ...]

  compile              refresh assets, compile, wait for the editor; prints CS compile errors
  test [edit|play]     run EditMode (default) or PlayMode tests; prints the counts and failures
  play [seconds]       enter play mode and let it run (default 10 s)
  stop                 exit play mode
  console [n]          print the last n errors and warnings (default 20)
  shot <path.png>      capture the Game view (play mode for a live frame) to an absolute path
  smoke [seconds]      compile, play for N s (default 10), stop; fails on any error logged in play
  -h, --help           this text

Steps run in order and stop at the first failure. Every step only compiles, runs, reads or
captures, so a project can allow the script without a prompt; for anything else, call the
MCP tools directly. Exit codes: 0 ok, 1 a step failed, 2 bad usage, 3 the editor or its
MCP server could not be reached, 4 not assessed (a test run found no tests: that is not a pass).

The server must use the HTTP transport. Its URL is $UNITY_MCP_URL, default
http://127.0.0.1:8080/mcp (MCP for Unity's default). The test runner's batch mode
(`Unity -batchmode -runTests`) cannot run while the editor has the project open;
this script is the route for a project that is open in the editor.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get('UNITY_MCP_URL', 'http://127.0.0.1:8080/mcp')
NOISE = ('MCP-FOR-UNITY', 'MCPForUnity')


class Unreachable(Exception):
    pass


def _post(payload, sid=None, timeout=600):
    req = urllib.request.Request(URL, data=json.dumps(payload).encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json, text/event-stream')
    if sid:
        req.add_header('mcp-session-id', sid)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            sid = r.headers.get('mcp-session-id', sid)
            body = r.read().decode('utf-8', 'replace')
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise Unreachable('%s: %s' % (URL, getattr(e, 'reason', e)))
    if body.lstrip().startswith('{'):
        return sid, [json.loads(body)]
    msgs = []
    for line in body.splitlines():
        if line.startswith('data:'):
            try:
                msgs.append(json.loads(line[5:].strip()))
            except ValueError:
                pass
    return sid, msgs


def call(tool, args):
    """Calls one tool; returns (parsed JSON reply or {'text': ...}, [image blocks])."""
    sid, _ = _post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                               'clientInfo': {'name': 'unity-mcp.py', 'version': '1'}}})
    _post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, sid)
    _, msgs = _post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                     'params': {'name': tool, 'arguments': args}}, sid)
    texts, images = [], []
    for m in msgs:
        res = m.get('result') or m.get('error') or {}
        for c in res.get('content', []) if isinstance(res, dict) else []:
            if c.get('type') == 'text':
                texts.append(c['text'])
            elif c.get('type') == 'image':
                images.append(c)
        if isinstance(res, dict) and 'content' not in res:
            texts.append(json.dumps(res))
    text = '\n'.join(texts)
    try:
        return json.loads(text), images
    except ValueError:
        return {'success': False, 'text': text}, images


def ok(reply):
    return isinstance(reply, dict) and reply.get('success') is not False


def console(types, count):
    reply, _ = call('read_console', {'action': 'get', 'types': types, 'count': str(count),
                                      'format': 'plain'})
    data = reply.get('data')
    if isinstance(data, dict):  # the paged reply
        data = data.get('items')
    rows = [d if isinstance(d, str) else (d.get('message') or json.dumps(d)) for d in (data or [])]
    return [r for r in rows if not any(n in r for n in NOISE)]


def wait_ready(timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if ok(call('read_console', {'action': 'get', 'types': ['error'], 'count': '1'})[0]):
                return True
        except Unreachable:
            pass  # a domain reload drops the bridge for a few seconds
        time.sleep(3)
    return False


def step_compile():
    call('manage_editor', {'action': 'stop'})
    call('read_console', {'action': 'clear'})
    try:
        call('refresh_unity', {'scope': 'all', 'mode': 'force', 'compile': 'request', 'wait_for_ready': True})
    except Unreachable:
        pass  # the domain reload can drop the connection; wait_ready() picks it up again
    if not wait_ready():
        print('compile: the editor did not answer again')
        return False
    errors = [e for e in console(['error'], 50) if 'error CS' in e]
    print('compile: %d error(s)' % len(errors))
    for e in errors:
        print('  ' + e.strip()[:300])
    return not errors


def step_test(mode):
    mode = 'PlayMode' if mode == 'play' else 'EditMode'
    args = {'mode': mode, 'include_failed_tests': True}
    if mode == 'PlayMode':
        args['init_timeout'] = 120000
    reply, _ = call('run_tests', args)
    job = (reply.get('data') or {}).get('job_id') if isinstance(reply, dict) else None
    if not job:
        print('test: could not start a %s run: %s' % (mode, json.dumps(reply)[:300]))
        return False
    end = time.time() + 1800
    while time.time() < end:
        reply, _ = call('get_test_job', {'job_id': job, 'wait_timeout': 30, 'include_failed_tests': True})
        data = reply.get('data') or {}
        status = str(data.get('status', '')).lower()
        if status and status != 'running':  # running | succeeded | failed
            result = data.get('result') or {}
            summary = result.get('summary') or {}
            print('test %s: %s  total %s, passed %s, failed %s, skipped %s%s' % (
                mode, status, summary.get('total', '?'), summary.get('passed', '?'),
                summary.get('failed', '?'), summary.get('skipped', '?'),
                ('  error: %s' % data['error']) if data.get('error') else ''))
            for r in [r for r in result.get('results') or [] if r.get('state') != 'Skipped'][:20]:
                print('  FAIL %s: %s' % (r.get('fullName') or r.get('name'), (r.get('message') or '').strip()[:240]))
            if status == 'succeeded' and not summary.get('total'):
                print('test %s: NOT ASSESSED, no tests ran' % mode)
                return None
            return status == 'succeeded' and not summary.get('failed')
        time.sleep(2)
    print('test %s: no result after 30 min' % mode)
    return False


def step_play(seconds):
    reply, _ = call('manage_editor', {'action': 'play'})
    if not ok(reply):
        print('play: %s' % json.dumps(reply)[:300])
        return False
    time.sleep(seconds)
    print('play: ran %g s' % seconds)
    return True


def step_shot(path):
    if not os.path.isabs(path) or not path.lower().endswith('.png'):
        print('shot: pass an absolute path to a .png (the editor resolves relative ones from the project)')
        return False
    reply, images = call('manage_camera', {'action': 'screenshot', 'include_image': True,
                                           'max_resolution': 1280, 'output_folder': 'Temp/unity-mcp-shots'})
    b64 = next((i.get('data') for i in images if i.get('data')), None)  # the server returns ImageContent
    if not b64:
        print('shot: no image in the reply: %s' % json.dumps(reply)[:300])
        return False
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'wb') as f:
        f.write(base64.b64decode(b64))
    print('shot: %s' % path)
    return True


def step_smoke(seconds):
    if not step_compile():
        return False
    call('read_console', {'action': 'clear'})
    if not step_play(seconds):
        return False
    errors = console(['error'], 50)
    call('manage_editor', {'action': 'stop'})
    print('smoke: %d error(s) in play' % len(errors))
    for e in errors:
        print('  ' + e.strip()[:300])
    return not errors


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__.strip())
        return 0 if argv else 2
    i = 0

    def arg(default=None):
        nonlocal i
        if i < len(argv) and argv[i] not in STEPS:
            i += 1
            return argv[i - 1]
        return default

    while i < len(argv):
        step = argv[i]
        i += 1
        if step == 'compile':
            good = step_compile()
        elif step == 'test':
            good = step_test(arg('edit'))
        elif step == 'play':
            good = step_play(float(arg(10)))
        elif step == 'stop':
            good = ok(call('manage_editor', {'action': 'stop'})[0])
            print('stop: %s' % ('ok' if good else 'failed'))
        elif step == 'console':
            for e in console(['error', 'warning'], int(arg(20))):
                print(e.strip()[:300])
            good = True
        elif step == 'shot':
            path = arg()
            if not path:
                print('shot: needs a path')
                return 2
            good = step_shot(path)
        elif step == 'smoke':
            good = step_smoke(float(arg(10)))
        else:
            print('unknown step: %s (see --help)' % step)
            return 2
        if good is None:
            return 4
        if not good:
            return 1
    return 0


STEPS = ('compile', 'test', 'play', 'stop', 'console', 'shot', 'smoke')

if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Unreachable as e:
        print('unity-mcp: cannot reach the editor (%s). Is Unity open with MCP for Unity on the HTTP '
              'transport? Set UNITY_MCP_URL if it is not on the default port.' % e)
        sys.exit(3)
