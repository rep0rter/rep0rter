#!/usr/bin/env python3
"""CI-gated native deployment on sky-server, independent of the retired Singa stack."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request


def run(*args, capture=False, timeout=120, env=None):
    r = subprocess.run(list(map(str, args)), check=True, timeout=timeout,
                       stdout=subprocess.PIPE if capture else None, text=True, env=env)
    return r.stdout.strip() if capture else None


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as f:
        os.chmod(temporary, 0o600)
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def tested_main(sha):
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('A full commit ID is required')
    remote = run('git', 'ls-remote', 'https://github.com/rep0rter/rep0rter.git',
                 'refs/heads/main', capture=True).split()[0]
    if remote != sha:
        raise ValueError('Commit must be current remote main')
    data = json.loads(run('gh', 'api',
        f'repos/rep0rter/rep0rter/actions/workflows/tests.yml/runs?event=push&branch=main&head_sha={sha}', capture=True))
    runs = [r for r in data['workflow_runs'] if r['head_sha'] == sha and r['event'] == 'push'
            and r['head_branch'] == 'main' and r.get('head_repository', {}).get('full_name') == 'rep0rter/rep0rter']
    latest = max(runs, key=lambda r: r['id'], default={})
    if latest.get('status') != 'completed' or latest.get('conclusion') != 'success':
        raise ValueError('This exact main commit must pass Offline tests')


def prepare(root, sha):
    tested_main(sha)
    repo = root / 'repository'
    if not repo.exists():
        run('git', 'clone', '--no-checkout', 'https://github.com/rep0rter/rep0rter.git', repo)
    run('git', '-C', repo, 'fetch', 'origin', 'main')
    release = root / 'releases' / sha
    if not release.exists():
        run('git', '-C', repo, 'worktree', 'add', '--detach', release, sha)
    if run('git', '-C', release, 'status', '--porcelain', capture=True):
        raise ValueError('Release checkout is not clean')
    if not (release / '.venv').exists():
        run('uv', 'venv', '--python', '3.12', release / '.venv', timeout=300)
    python = release / '.venv/bin/python'
    run('uv', 'pip', 'install', '--python', python, '-r', release / 'requirements.txt', timeout=600)
    run(python, '-m', 'playwright', 'install', 'chromium', timeout=600)
    print(f'Prepared tested release {sha}; services unchanged', flush=True)


def unit_files(root):
    base = f'''[Unit]
Description=rep0rter {{description}} on sky-server
After=network-online.target
Wants=network-online.target

[Service]
Environment=REP0RTER_HOST_ROOT={root}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PATH={Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin
WorkingDirectory={root}/current
ExecStart={root}/current/.venv/bin/python {root}/current/scripts/host-runtime.py {{command}}
UMask=0077
NoNewPrivileges=true
CPUQuota=150%
MemoryMax=2G
TimeoutStopSec=120
{{extra}}
'''
    return {
        'rep0rter-web.service': base.format(description='website and accounts', command='web', extra='Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target'),
        'rep0rter-report.service': base.format(description='reporting cycle', command='report', extra='Type=oneshot\nTimeoutStartSec=45min'),
        'rep0rter-maintenance.service': base.format(description='backup and health maintenance', command='maintenance', extra='Type=oneshot\nTimeoutStartSec=10min'),
        'rep0rter-report.timer': '[Unit]\nDescription=Hourly rep0rter reporting\n[Timer]\nOnCalendar=*-*-* *:07:00\nRandomizedDelaySec=30\nPersistent=true\n[Install]\nWantedBy=timers.target\n',
        'rep0rter-maintenance.timer': '[Unit]\nDescription=Hourly rep0rter backup checks\n[Timer]\nOnCalendar=hourly\nRandomizedDelaySec=120\nPersistent=true\n[Install]\nWantedBy=timers.target\n',
    }


def activate(root, sha):
    tested_main(sha)
    release = root / 'releases' / sha
    if not (release / '.venv/bin/python').is_file():
        raise ValueError('Prepare the release first')
    if run('git', '-C', release, 'status', '--porcelain', capture=True):
        raise ValueError('Release checkout is not clean')
    if not (root / 'data/rep0rter.sqlite').is_file() or not (root / 'data/site/index.html').is_file():
        raise ValueError('Import and verify the authoritative data before activation')
    current = root / 'current'
    previous = current.resolve() if current.is_symlink() else None
    state_file = root / 'deployment.json'
    atomic_json(state_file, {'candidate': sha, 'previous': str(previous) if previous else None, 'phase': 'switching'})
    units = Path.home() / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    for name, content in unit_files(root).items():
        (units / name).write_text(content)
    run('systemctl', '--user', 'daemon-reload')
    # No reporting timer is enabled until final data transfer and public cutover.
    run('systemctl', '--user', 'stop', 'rep0rter-web.service')
    try:
        pointer = root / '.current-next'
        pointer.unlink(missing_ok=True)
        pointer.symlink_to(release)
        pointer.replace(current)
        run('systemctl', '--user', 'start', 'rep0rter-web.service')
        import time
        for attempt in range(20):
            try:
                with urllib.request.urlopen('http://127.0.0.1:18191/healthz', timeout=3) as r:
                    if json.load(r).get('source_commit') == sha:
                        break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('New website failed its commit health check')
        run('systemctl', '--user', 'enable', 'rep0rter-web.service')
        atomic_json(state_file, {'deployed_sha': sha, 'previous': str(previous) if previous else None, 'phase': 'active'})
    except BaseException:
        run('systemctl', '--user', 'stop', 'rep0rter-web.service')
        if previous:
            current.unlink()
            current.symlink_to(previous)
            run('systemctl', '--user', 'start', 'rep0rter-web.service')
        atomic_json(state_file, {'candidate': sha, 'previous': str(previous) if previous else None, 'phase': 'failed'})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'activate'))
    p.add_argument('--commit', required=True)
    p.add_argument('--root', type=Path, default=Path('/srv/rep0rter'))
    args = p.parse_args()
    os.umask(0o077)
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / 'releases').mkdir(exist_ok=True)
    with (args.root / 'control.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another deployment or reporting operation is active; retry later')
            return 75
        (prepare if args.action == 'prepare' else activate)(args.root, args.commit)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
