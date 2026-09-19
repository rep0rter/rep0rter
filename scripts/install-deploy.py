#!/usr/bin/env python3
"""Install/update the deployment timer for the current Linux user."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    os.umask(0o077)
    source = Path(__file__).resolve().parent.parent
    root = Path.home() / '.local/share/rep0rter-deploy'
    units = Path.home() / '.config/systemd/user'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    units.mkdir(parents=True, exist_ok=True)
    for executable in ('git', 'docker', 'gh', 'systemctl'):
        if not shutil.which(executable):
            raise SystemExit(f'Missing required command: {executable}')
    if output('loginctl', 'show-user', str(os.getuid()), '-p', 'Linger', '--value') != 'yes':
        raise SystemExit('Enable boot/logout operation first: loginctl enable-linger')
    subprocess.run(['gh', 'api', 'repos/rep0rter/rep0rter/actions/workflows/tests.yml',
                    '--silent'], check=True)
    config_path = root / 'config.json'
    if not config_path.exists():
        if not (source / '.env').is_file():
            raise SystemExit(f'Production environment file missing: {source / ".env"}')
        inspected = json.loads(output('docker', 'inspect', 'rep0rter-worker'))[0]
        data_dir = next(mount['Source'] for mount in inspected['Mounts'] if mount['Destination'] == '/data')
        config = dict(repository='rep0rter/rep0rter', remote='https://github.com/rep0rter/rep0rter.git',
                      source_dir=str(source), env_file=str(source / '.env'), data_dir=data_dir,
                      state_dir=str(root), minimum_sha=output('git', '-C', str(source), 'rev-parse', 'HEAD'))
        config_path.write_text(json.dumps(config, indent=2) + '\n')
    # Keep the installed controller outside the checkout and release directories.
    # Updating main's controller code requires an explicit reinstall on the host.
    subprocess.run(['systemctl', '--user', 'stop', 'rep0rter-deploy.timer'], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    status = subprocess.run(['systemctl', '--user', 'is-active', 'rep0rter-deploy.service'],
                            text=True, capture_output=True)
    if status.stdout.strip() in ('active', 'activating', 'deactivating'):
        raise SystemExit('Wait for the current deployment to finish before reinstalling')
    shutil.copyfile(source / 'scripts/deploy.py', root / 'deploy.py')
    replacements = {
        '@PYTHON@': sys.executable, '@SCRIPT@': str(root / 'deploy.py'), '@CONFIG@': str(config_path),
        '@PATH@': ':'.join(dict.fromkeys([
            str(Path(shutil.which(name)).parent) for name in ('git', 'docker', 'gh')
        ] + ['/usr/local/bin', '/usr/bin', '/bin'])),
    }
    for name in ('rep0rter-deploy.service', 'rep0rter-deploy.timer'):
        text = (source / 'deploy/systemd' / name).read_text()
        for key, value in replacements.items():
            text = text.replace(key, value)
        (units / name).write_text(text)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', 'rep0rter-deploy.timer'], check=True)
    print(f'Installed; config and private deployment state: {root}')
    print('View deployment logs: journalctl --user -u rep0rter-deploy.service -f')


if __name__ == '__main__':
    main()
