"""Pause/retire Singa through its installed controller, under its deployment lock."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pause', 'retire-web'))
    args = parser.parse_args()
    os.umask(0o077)
    root = Path.home() / '.local/share/rep0rter-deploy'
    with (root / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Deployment controller is busy; no changes made. Retry after it completes.')
            return 75
        spec = importlib.util.spec_from_file_location('host_deployer', root / 'deploy.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        controller = module.Deployer(json.loads((root / 'config.json').read_text()))
        if controller.state.get('pending'):
            print('Pending deployment requires controller recovery before cutover.')
            return 75
        current = root / 'current.json'
        if not current.exists():
            raise RuntimeError('No verified current deployment configuration')
        if args.action == 'pause':
            subprocess.run(['systemctl','--user','disable','--now','rep0rter-deploy.timer'], check=True)
            controller.stop_writers(current)
            module.log('Cloudflare cutover: local writers paused, web retained, data retained')
        else:
            controller.compose(current, 'stop', '--timeout', '30', 'web')
            module.log('Cloudflare cutover: local web retired, data retained')
    return 0


if __name__ == '__main__':
    sys.exit(main())
