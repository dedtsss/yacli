#!/usr/bin/env python3
# Bounded read-only checks; responses stay in memory, only fixed fields are saved.
import os, shlex, subprocess, socket, urllib.request, json, time, hashlib, base64, re, pathlib, sys, argparse
args = argparse.ArgumentParser()
args.add_argument('--binary')
args.add_argument('--endpoint')
args.add_argument('--output', required=True)
opt = args.parse_args()
repo = pathlib.Path(__file__).resolve().parents[2]
out = pathlib.Path(opt.output)
state = {'date': '2026-10-22', 'route': 'existing service loopback' if opt.endpoint else 'isolated candidate loopback', 'cases': [], 'ui_acceptance': False}

def save(row):
    state['cases'].append(row)
    out.write_text(json.dumps(state, indent=2) + '\n')
    print(json.dumps(row), flush=True)
env = os.environ.copy()
for item in shlex.split(subprocess.check_output(['systemctl', 'show', 'yacli-devcat.service', '-p', 'Environment', '--value'], text=True)):
    k, v = item.split('=', 1)
    if k.startswith('YACLI_DEVCAT_') or k == 'YACLI_CONFIG_DIR':
        env[k] = v
env['CREDENTIALS_DIRECTORY'] = '/run/credentials/yacli-devcat.service'
s = socket.socket()
s.bind(('127.0.0.1', 0))
port = s.getsockname()[1]
s.close()
binary = pathlib.Path(opt.binary) if opt.binary else repo / 'target/release/yacli'
state['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
endpoint = opt.endpoint or f'http://127.0.0.1:{port}'
if not re.fullmatch('http://127\\.0\\.0\\.1:[0-9]+', endpoint):
    raise ValueError('loopback endpoint required')
p = None if opt.endpoint else subprocess.Popen([str(binary), 'mcp', '--transport', 'http', '--listen', f'127.0.0.1:{port}'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
session = None
try:
    for _ in range(100):
        try:
            with urllib.request.urlopen(endpoint + '/healthz', timeout=0.2) as r:
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.05)

    def rpc(method, params):
        global session
        h = {'Content-Type': 'application/json'}
        if session:
            h['Mcp-Session-Id'] = session
        with urllib.request.urlopen(urllib.request.Request(endpoint + '/mcp', json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params)).encode(), h), timeout=45) as r:
            session = r.headers.get('Mcp-Session-Id', session)
            return json.load(r)
    rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': '551-recovery', 'version': '1'}})
    tools = rpc('tools/list', {})['result']['tools']
    save({'check': 'catalog', 'count': len(tools), 'status': 'PASS' if len(tools) == 11 else 'BLOCKED'})

    def call(name, args, phase):
        start = time.monotonic()
        row = {'account': args.get('account', 'dedts'), 'tool': name, 'phase': phase, 'status': 'BLOCKED', 'code': 'INTERRUPTED'}
        try:
            x = rpc('tools/call', {'name': 'yacli.' + name, 'arguments': args})
            if 'error' in x:
                err = x['error']
                row.update(status='BLOCKED', code=err.get('data', {}).get('code', 'RPC_ERROR'))
                if row['code'] == 'NETWORK_ERROR':
                    detail = re.sub(r'https?://[^\s)]+', '<url-redacted>', err.get('message', ''))
                    row['network_detail'] = detail[:400]
                    row['network_stage'] = next((stage for marker, stage in [('DNS resolution', 'dns'), ('connect to IMAP', 'tcp'), ('TLS session', 'tls'), ('vault helper', 'vault'), ('request for url', 'http')] if marker in detail), 'unknown')
                return None
            x = x['result']
            v = x.get('structuredContent')
            if v is None:
                v = json.loads(x['content'][0]['text'])
            if x.get('isError') or v.get('ok') is False:
                row.update(status='BLOCKED', code=v.get('code', 'TOOL_ERROR'))
                return None
            row.pop('code', None)
            row.update(status='PASS' if v.get('account') == args.get('account', 'dedts') else 'BLOCKED')
            if name == 'mail.read' and v.get('item', {}).get('uid') != args['uid']:
                row.update(status='BLOCKED', code='UID_MISMATCH')
            for key in ('items',):
                if key in v:
                    row['count'] = len(v[key])
            return v
        except Exception as e:
            row.update(status='BLOCKED', code=type(e).__name__)
            return None
        finally:
            row['seconds'] = round(time.monotonic() - start, 3)
            save(row)
    aliases = ['dedts', 'ykkareliadom', 'dvabobra2014']
    schema_ok = all((t['inputSchema']['properties']['account'].get('enum') == sorted(aliases) and t['inputSchema']['properties']['account'].get('default') == 'dedts' for t in tools))
    save({'check': 'exact aliases and default in every schema', 'status': 'PASS' if schema_ok else 'BLOCKED'})
    state['catalog_sha256'] = hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()
    read_names = {'yacli.mail.folders', 'yacli.mail.list', 'yacli.mail.search', 'yacli.mail.read', 'yacli.disk.info', 'yacli.disk.list', 'yacli.disk.read'}
    annotations_ok = all((t.get('annotations', {}).get('readOnlyHint') == (t['name'] in read_names) for t in tools))
    save({'check': 'read annotations', 'status': 'PASS' if annotations_ok else 'BLOCKED'})

    def deny(name, arguments, expected):
        response = rpc('tools/call', {'name': 'yacli.' + name, 'arguments': arguments})
        denied = 'error' in response or response.get('result', {}).get('isError', False)
        save({'check': expected, 'status': 'EXPECTED_DENY' if denied and expected in json.dumps(response) else 'BLOCKED'})
    deny('disk.list', {'path': 'disk:/outside'}, 'outside')
    deny('mail.folders', {'account': 'unknown-551'}, 'account')
    deny('mail.folders', {'account': 123}, 'account')
    for alias in ('ykkareliadom', 'dvabobra2014'):
        deny('disk.info', {'account': alias}, 'no credential configured for disk')
    deny('disk.publish', {'account': 'dedts', 'path': 'disk:/DevCat'}, 'capability denied')
    for alias in aliases:
        a = {'account': alias, 'folder': 'INBOX'}
        v = call('mail.list', dict(a, limit=1), 'current list')
        call('mail.folders', {'account': alias}, 'current folders')
        if v:
            state.setdefault('response_shapes', {})['mail.list'] = list(v)
            msgs = v.get('items', [])
            if msgs:
                uid = msgs[0]['uid']
                call('mail.read', dict(a, uid=uid, max_bytes=15 * 1024 * 1024), 'known listed UID')
        v = call('mail.search', dict(a, query='yandex', limit=1), 'positive indexed control')
        if v:
            msgs = v.get('items', [])
            if msgs:
                u = msgs[0]['uid']
                read = call('mail.read', dict(a, uid=u, max_bytes=15 * 1024 * 1024), 'read search UID')
                again = call('mail.search', dict(a, query='yandex', limit=10), 'known readable UID positive search')
                matched = bool(again and any((m['uid'] == u for m in again['items'])))
                save({'account': alias, 'check': 'known readable UID matched search', 'status': 'PASS' if matched else 'BLOCKED'})
                save({'account': alias, 'check': 'positive search UID readable', 'status': 'PASS' if read else 'BLOCKED'})
            else:
                save({'account': alias, 'check': 'positive search', 'status': 'BLOCKED', 'reason': 'zero matches'})
    audit = json.loads((repo / 'docs/devcat-account-audit-2026-10-19.json').read_text())
    v = call('disk.list', {'path': audit['disk_test_directory']}, 'existing dedicated test directory')
    if v:
        state.setdefault('response_shapes', {})['disk.list'] = list(v)
        resource = v.get('resource', {})
        items = (resource.get('children') or {}).get('items', [])
        if not items:
            items = v.get('items', [])
        state['disk_item_count'] = len(items)
        for item in items[:1]:
            if item.get('type', item.get('resource_type')) == 'file':
                v = call('disk.read', {'account': 'dedts', 'path': item['path']}, 'existing uploaded checksum control')
                if v:
                    data = base64.b64decode(v['data'])
                    h = hashlib.sha256(data).hexdigest()
                    save({'account': 'dedts', 'check': 'upload/read checksum', 'status': 'PASS' if h == audit['upload_sha256'] else 'BLOCKED', 'bytes': len(data), 'sha256': h, 'expected_sha256': audit['upload_sha256']})
finally:
    if p is not None:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    state['candidate_stopped'] = p is not None
    required = all((any((r.get('account') == alias and r.get('phase') == 'known listed UID' and (r.get('status') == 'PASS') for r in state['cases'])) and any((r.get('account') == alias and r.get('check') == 'known readable UID matched search' and (r.get('status') == 'PASS') for r in state['cases'])) for alias in ('dedts', 'ykkareliadom', 'dvabobra2014')))
    state['state'] = 'PASS' if required and (not any((r.get('status') == 'BLOCKED' for r in state['cases']))) and any((r.get('check') == 'upload/read checksum' and r['status'] == 'PASS' for r in state['cases'])) else 'BLOCKED'
    out.write_text(json.dumps(state, indent=2) + '\n')
sys.exit(0 if state['state'] == 'PASS' else 1)
