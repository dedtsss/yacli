#!/usr/bin/env python3
"""One host action: verify the artifact, atomically install, check, or roll back.

Requires an existing privileged host route. Does not create services or tunnels.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request

REPO = Path(__file__).resolve().parents[2]
BASE = Path('/opt/yacli-devcat')
SERVICES = ['yacli-devcat.service', 'yandex-devcat-tunnel.service']
TUNNEL = '/opt/catvps-control/bin/tunnel-client'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, text=True,
                          timeout=kwargs.pop('timeout', 30), **kwargs).stdout.strip()


def git(*args):
    return run(['git', '-c', f'safe.directory={REPO}', '-C', str(REPO), *args])


def atomic_link(target, link):
    temporary = link.with_name(link.name + f'.swap-{os.getpid()}')
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def repo_file(relative, label):
    require(isinstance(relative, str) and relative, f'invalid {label} path')
    path = (REPO / relative).resolve(strict=True)
    require(path.is_relative_to(REPO), f'{label} path escapes repository')
    require(path.is_file(), f'{label} is not a regular file')
    return path


def tunnel_ready():
    # Probe only sockets owned by the existing tunnel service's cgroup. Do not
    # read its profile, credentials or journal, and do not expose raw output.
    group = run(['systemctl', 'show', SERVICES[1], '-p', 'ControlGroup', '--value'])
    root = Path('/sys/fs/cgroup') / group.lstrip('/')
    pids = set()
    for file in root.rglob('cgroup.procs'):
        pids.update(file.read_text().split())
    require(bool(pids), 'existing tunnel service has no cgroup processes')
    for line in run(['ss', '-lntpH']).splitlines():
        if not pids.intersection(re.findall(r'pid=(\d+)', line)):
            continue
        address = line.split()[3]
        if not address.startswith('127.0.0.1:'):
            continue
        port = address.rsplit(':', 1)[1]
        probe = subprocess.run([TUNNEL, 'health', '--port', port,
                                '--require-control-plane-poll', '--json'],
                               capture_output=True, timeout=12)
        if probe.returncode == 0:
            return
    raise RuntimeError('existing tunnel service readiness/control-plane poll failed')


def health():
    for _ in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8797/healthz', timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(.5)
    raise RuntimeError('MCP health failed')


def verify_manifest(manifest):
    source = manifest['source_head']
    require(re.fullmatch('[0-9a-f]{40}', source), 'invalid source SHA')
    require(not git('status', '--porcelain', '--untracked-files=no'), 'tracked tree changed')
    require(git('cat-file', '-e', f'{source}^{{commit}}') == '', 'source commit unavailable')
    require(git('merge-base', '--is-ancestor', source, 'HEAD') == '',
            'source commit is not an ancestor of repository HEAD')
    binary = repo_file(manifest['artifact'], 'artifact')
    require(re.fullmatch('[0-9a-f]{64}', manifest['binary_sha256']),
            'invalid artifact SHA-256')
    require(sha256(binary) == manifest['binary_sha256'], 'artifact hash mismatch')
    report = repo_file(manifest['verification_report'], 'verification report')
    require(re.fullmatch('[0-9a-f]{64}', manifest['verification_sha256']),
            'invalid verification report SHA-256')
    require(sha256(report) == manifest['verification_sha256'], 'verification report changed')
    evidence = json.loads(report.read_text())
    require(evidence['state'] == 'PASS', 'candidate verification did not pass')
    require(evidence['scope'] == 'candidate_build', 'verification report scope mismatch')
    require(evidence['source_head'] == source, 'verification source mismatch')
    require(evidence['binary_sha256'] == manifest['binary_sha256'], 'verified binary mismatch')
    checks = evidence['checks']
    required_checks = {'fmt', 'clippy', 'locked_tests', 'release_build', 'smoke'}
    require(isinstance(checks, dict) and set(checks) == required_checks
            and all(status == 'PASS' for status in checks.values()),
            'candidate verification checks did not pass')
    expected = manifest['expected_previous_target']
    release_root = re.escape(str(BASE / 'releases'))
    require(re.fullmatch(release_root + r'/[0-9a-f]{40}', expected),
            'invalid deployment baseline target')
    require(re.fullmatch('[0-9a-f]{64}', manifest['expected_previous_sha256']),
            'invalid deployment baseline SHA-256')
    current = BASE / 'current'
    require(str(current.resolve()) == expected, 'deployed HEAD mismatch; no install attempted')
    require(sha256(current / 'yacli') == manifest['expected_previous_sha256'],
            'deployed binary hash mismatch; no install attempted')
    return source, binary, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    source, binary, previous = verify_manifest(manifest)
    if not args.apply:
        print(json.dumps({'state': 'PREPARED', 'source_head': source,
                          'binary_sha256': manifest['binary_sha256'],
                          'requires': 'privileged host route with --apply'}))
        return
    require(os.geteuid() == 0, 'privileged host route required; no install attempted')
    with open('/run/lock/yacli-devcat-deploy.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_manifest(manifest)
        run(['systemctl', 'is-active', *SERVICES])
        health()
        tunnel_ready()
        release = BASE / 'releases' / source
        require(not release.exists(), 'release directory already exists; inspect it first')
        staged = Path(tempfile.mkdtemp(prefix='.551-', dir=BASE / 'releases'))
        try:
            shutil.copyfile(binary, staged / 'yacli')
            shutil.copyfile(REPO / 'LICENSE', staged / 'LICENSE')
            os.chmod(staged / 'yacli', 0o755)
            os.chmod(staged / 'LICENSE', 0o644)
            shutil.chown(staged, user='root', group='codex')
            os.chmod(staged, 0o750)
            require(sha256(staged / 'yacli') == manifest['binary_sha256'], 'copied artifact mismatch')
            os.rename(staged, release)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
        # Keep the previous binary and a separate atomic rollback pointer.
        verify_manifest(manifest)
        atomic_link(previous, BASE / f'rollback-before-{source}')
        result = {'source_head': source, 'binary_sha256': manifest['binary_sha256'],
                  'previous': previous, 'ui_acceptance': False}
        try:
            atomic_link(str(release), BASE / 'current')
            run(['systemctl', 'restart', SERVICES[0]])
            health()
            run(['systemctl', 'is-active', *SERVICES])
            pid = run(['systemctl', 'show', SERVICES[0], '-p', 'MainPID', '--value'])
            require(sha256(Path('/proc') / pid / 'exe') == manifest['binary_sha256'],
                    'running process hash mismatch')
            readonly = REPO / f'target/devcat-delivery/postdeploy-{source}.json'
            run(['runuser', '-u', 'codex', '--', 'python3',
                 str(REPO / 'ops/devcat-yandex/verify-readonly.py'),
                 '--binary', str(release / 'yacli'), '--endpoint', 'http://127.0.0.1:8797',
                 '--output', str(readonly)], timeout=900)
            require(json.loads(readonly.read_text())['state'] == 'PASS', 'postdeploy read-only check failed')
            tunnel_ready()
            result['state'] = 'DEPLOYED_LOOPBACK_VERIFIED'
        except BaseException as error:
            result.update(state='ROLLBACK_FAILED', failure_type=type(error).__name__)
            atomic_link(previous, BASE / 'current')
            run(['systemctl', 'restart', SERVICES[0]])
            health()
            run(['systemctl', 'is-active', *SERVICES])
            require(sha256(BASE / 'current/yacli') == manifest['expected_previous_sha256'],
                    'rollback hash mismatch')
            result['state'] = 'ROLLED_BACK'
            raise
        finally:
            record = REPO / f'target/devcat-delivery/host-result-{source}.json'
            record.write_text(json.dumps(result, indent=2) + '\n')
            shutil.chown(record, user='root', group='codex')
            os.chmod(record, 0o640)
            print(json.dumps(result))


if __name__ == '__main__':
    main()
