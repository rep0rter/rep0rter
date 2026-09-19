"""Deploy tested main to native Workers using the current Wrangler login."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

root = Path(__file__).resolve().parents[1]


def output(*command):
    return subprocess.check_output(command, cwd=root, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    if output('git', 'status', '--porcelain'):
        parser.error('Commit all changes in this worktree before deployment.')
    sha = output('git', 'rev-parse', 'HEAD')
    remote_sha = output('git', 'ls-remote', 'origin', 'refs/heads/main').split()[0]
    if sha != remote_sha:
        parser.error('Only the current remote main commit may deploy.')
    query = urlencode({'branch': 'main', 'event': 'push', 'head_sha': sha, 'per_page': 100})
    result = json.loads(output('gh', 'api', 'repos/rep0rter/rep0rter/actions/workflows/tests.yml/runs?' + query))
    runs = [r for r in result['workflow_runs'] if r['head_sha'] == sha and r['head_branch'] == 'main'
            and r['event'] == 'push' and r.get('head_repository', {}).get('full_name') == 'rep0rter/rep0rter']
    latest = max(runs, key=lambda r: r['id'], default=None)
    if not latest or latest['status'] != 'completed' or latest['conclusion'] != 'success':
        parser.error('The latest Offline tests push run for this exact commit must pass.')
    subprocess.run([sys.executable, str(root / 'cloudflare/prepare.py')], check=True)
    if output('git', 'ls-remote', 'origin', 'refs/heads/main').split()[0] != sha:
        parser.error('Main changed while preparing; update and try again.')
    config = 'wrangler-production.jsonc' if args.production else 'wrangler.jsonc'
    subprocess.run(['pywrangler', 'deploy', '--config', config], cwd=root / 'cloudflare', check=True)
    web_config = 'wrangler-web-production.jsonc' if args.production else 'wrangler-web.jsonc'
    subprocess.run(['npm', 'exec', '--yes', '--package=wrangler@4.135.0', '--', 'wrangler', 'deploy', '--config', web_config],
                   cwd=root / 'cloudflare', check=True)
    print('Deployed source commit:', sha)
    print('Verify public health and the reporting cycle before considering deployment complete.')


if __name__ == '__main__':
    main()
