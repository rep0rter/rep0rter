#!/usr/bin/env python3
"""Host-side, CI-gated deployment. Uses only the Python standard library."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode


def log(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%S%z'), message, flush=True)


def run(*args, env=None, capture=False, timeout=120):
    result = subprocess.run(
        [str(arg) for arg in args], env=env, check=True,
        stdout=subprocess.PIPE if capture else None, text=True, timeout=timeout,
    )
    return result.stdout.strip() if capture else None


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def ci_passed(runs, sha, repository):
    # The endpoint is scoped to tests.yml; never accept PR/fork/other-SHA results.
    matching = [item for item in runs if (
        item['head_sha'] == sha and item['head_branch'] == 'main'
        and item['event'] == 'push'
        and item.get('head_repository', {}).get('full_name') == repository
    )]
    latest = max(matching, key=lambda item: item['id'], default=None)
    return bool(latest and latest['status'] == 'completed'
                and latest['conclusion'] == 'success')


class Deployer:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['state_dir'])
        self.repo = self.root / 'repository'
        self.state_path = self.root / 'state.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.env = dict(os.environ, REP0RTER_ENV_FILE=config['env_file'],
                        REP0RTER_DATA_HOST_DIR=config['data_dir'])

    def save(self):
        write_json(self.state_path, self.state)

    def compose(self, file, *args, capture=False, timeout=120):
        return run('docker', 'compose', '--project-name', 'rep0rter',
                   '--env-file', self.config['env_file'], '-f', file,
                   *args, env=self.env, capture=capture, timeout=timeout)

    def fetch(self):
        if not self.repo.exists():
            run('git', 'clone', '--no-checkout', '--single-branch', '--branch', 'main',
                self.config['remote'], self.repo)
        run('git', '-C', self.repo, 'fetch', '--prune', 'origin',
            '+refs/heads/main:refs/remotes/origin/main')
        return run('git', '-C', self.repo, 'rev-parse', 'origin/main', capture=True)

    def eligible(self, sha):
        query = urlencode({'branch': 'main', 'event': 'push', 'head_sha': sha, 'per_page': 100})
        response = json.loads(run(
            'gh', 'api', f"repos/{self.config['repository']}/actions/workflows/tests.yml/runs?{query}",
            capture=True,
        ))
        return ci_passed(response['workflow_runs'], sha, self.config['repository'])

    def freeze(self, source, destination, *, running):
        """Resolve paths/env privately and pin images so rollback never rebuilds."""
        config = json.loads(self.compose(source, 'config', '--format', 'json', capture=True))
        for name, service in config['services'].items():
            if running:
                ids = self.compose(source, 'ps', '-q', name, capture=True).split()
                if len(ids) != 1:
                    raise RuntimeError(f'Expected one running {name} container')
                image = run('docker', 'inspect', '--format', '{{.Image}}', ids[0], capture=True)
            else:
                image = run('docker', 'image', 'inspect', '--format', '{{.Id}}',
                            service['image'], capture=True)
            # A tag protects the old image from normal dangling-image pruning.
            run('docker', 'image', 'tag', image, f'rep0rter-retained:{image[7:]}')
            service['image'] = image
            service.pop('build', None)
            service.pop('pull_policy', None)
        write_json(destination, config)

    def stop_writers(self, config):
        self.compose(config, 'stop', '--timeout', '60', 'worker', 'maintenance', 'accounts', timeout=240)

    def start(self, config):
        self.compose(config, 'up', '-d', '--no-build', '--pull', 'never', '--remove-orphans',
                     '--wait', '--wait-timeout', '1000', timeout=1200)

    def render(self, config):
        # No collection, LLM calls, or Telegram publication.
        self.compose(config, 'run', '--rm', '--no-deps', '--name', 'rep0rter-deploy-render',
                     'worker', 'build-site', timeout=1200)

    def recover(self):
        pending = self.state['pending']
        log(f"Restoring application before failed/interrupted deployment {pending['sha']}")
        self.stop_writers(pending['candidate'])
        # A killed client can leave a one-off renderer behind.
        leftover = run('docker', 'ps', '-aq', '--filter', 'name=^/rep0rter-deploy-render$', capture=True)
        if leftover:
            run('docker', 'container', 'rm', '-f', leftover, timeout=120)
        try:
            self.render(pending['previous'])
        finally:
            self.start(pending['previous'])
        write_json(self.root / 'current.json', json.loads(Path(pending['previous']).read_text()))
        self.state['failed_sha'] = pending['sha']
        self.state.pop('pending')
        self.save()
        log('Previous application restored; database kept intact')

    def deploy(self, sha):
        release = self.root / 'releases' / sha
        release.parent.mkdir(exist_ok=True)
        if not release.exists():
            run('git', '-C', self.repo, 'worktree', 'add', '--detach', release, sha)
        source = release / 'compose.yaml'
        previous = self.root / f'previous-{sha}.json'
        current = self.root / 'current.json'
        self.freeze(current if current.exists() else Path(self.config['source_dir']) / 'compose.yaml',
                    previous, running=True)
        self.env['REP0RTER_IMAGE'] = f'rep0rter:{sha}'
        log(f'Building {sha} while production remains running')
        self.compose(source, 'build', timeout=1800)
        self.compose(source, 'pull', '--ignore-buildable', '--policy', 'missing', timeout=600)
        candidate = self.root / f'release-{sha}.json'
        self.freeze(source, candidate, running=False)
        if self.fetch() != sha or not self.eligible(sha):
            log('Main or CI changed during build; leaving production unchanged')
            return
        self.compose(previous, 'exec', '-T', 'worker', 'python', '-m', 'rep0rter',
                     'backup', '--offsite', '', timeout=300)
        # Journal before the first disruptive step; recover even after a reboot.
        self.state['pending'] = {'sha': sha, 'candidate': str(candidate), 'previous': str(previous)}
        self.save()
        try:
            self.stop_writers(previous)
            self.render(candidate)
            self.start(candidate)
            write_json(current, json.loads(candidate.read_text()))
            self.state.update(deployed_sha=sha, deployed_at=time.time(),
                              previous_config=str(previous), failed_sha=None)
            self.state.pop('pending')
            self.save()
        except Exception:
            self.recover()
            raise
        log(f'Deployed {sha}; all services passed startup checks')

    def tick(self, retry=False, check=False):
        if self.state.get('pending'):
            if check:
                log('Interrupted deployment needs recovery')
                return
            self.recover()
        sha = self.fetch()
        if sha == self.state.get('deployed_sha'):
            log(f'Already deployed {sha}')
            return
        if sha == self.state.get('failed_sha') and not retry:
            log(f'Deployment previously failed for {sha}; waiting for a new commit or --retry')
            return
        # Do not replace newer local production code with an older remote main.
        run('git', '-C', self.repo, 'merge-base', '--is-ancestor', self.config['minimum_sha'], sha)
        if not self.eligible(sha):
            log(f'Waiting for successful push tests on main at {sha}')
            return
        if check:
            log(f'Ready to deploy {sha}; check only, production unchanged')
            return
        try:
            self.deploy(sha)
        except Exception:
            self.state['failed_sha'] = sha
            self.save()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='fetch and check CI without deployment')
    parser.add_argument('--retry', action='store_true', help='retry the failed main commit')
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    root = Path(config['state_dir'])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'deploy.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('Another deployment is active')
            return 0
        try:
            Deployer(config).tick(retry=args.retry, check=args.check)
        except Exception as error:
            log(f'Deployment check failed: {error}')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
