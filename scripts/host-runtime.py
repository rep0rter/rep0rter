#!/usr/bin/env python3
"""Native host entrypoints; private JSON configuration never enters argv or Git."""
import fcntl
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(os.environ.get('REP0RTER_HOST_ROOT', '/srv/rep0rter'))


def configure():
    values = json.loads((ROOT / 'service.json').read_text())
    if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
        raise ValueError('Host configuration must contain string values')
    os.environ.update({k: v for k, v in values.items() if k.startswith(('REP0RTER_', 'AI_', 'TELEGRAM_'))})
    os.environ['REP0RTER_DATA_DIR'] = str(ROOT / 'data')
    os.environ['REP0RTER_HOST_ROOT'] = str(ROOT)
    os.environ['REP0RTER_SOURCE_COMMIT'] = subprocess.check_output(
        ['git', '-C', str(Path(__file__).resolve().parents[1]), 'rev-parse', 'HEAD'], text=True).strip()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    configure()
    from rep0rter.config import Config
    from rep0rter.operations import RedactingFormatter
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter('%(levelname)s %(name)s: %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    cfg = Config()
    command = sys.argv[1]
    if command == 'web':
        os.execv(sys.executable, [sys.executable, '-m', 'gunicorn', '--bind', '127.0.0.1:18191',
                                '--workers', '2', '--threads', '2', '--timeout', '600',
                                '--access-logfile', '/dev/null', 'rep0rter.web:create_app()'])
    if command == 'maintenance':
        from rep0rter.operations import maintenance
        maintenance(cfg, backup_dir=ROOT / 'backups', offsite=os.environ.get('REP0RTER_BACKUP_OFFSITE'))
        return 0
    # Reporting, manual repair, rebuild and deployment share this lock. Account
    # transactions use SQLite and the existing site-generation lock independently.
    with (ROOT / 'control.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another host operation is active; no work started')
            return 75
        from rep0rter.cli import main as cli
        if command == 'report':
            # Collection degradation is recorded separately from process failure.
            # A successful publication must not be replayed by a service restart.
            from rep0rter.cli import run_once
            collected, posted = run_once(cfg)
            print(f'Reporting complete: collected={collected}, posted={posted}', flush=True)
            return 0
        return cli([command, *sys.argv[2:]])


if __name__ == '__main__':
    raise SystemExit(main())
