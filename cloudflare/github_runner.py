"""Run Python/Chromium on GitHub while checkpointing every DB commit to Workers."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile


class RunnerRuntime:
    def __init__(self, url, token, root, lease):
        self.url, self.token, self.root, self.lease = url.rstrip('/'), token, Path(root), lease
        self.sequence = 0
        self.pages = []
        self.poisoned = False
        self.policy_digest = None

    def request(self, action, body=b'', content_type='application/json'):
        request = urllib.request.Request(self.url + '/__runner/' + action, data=body, method='POST',
            headers={'Authorization': 'Bearer ' + self.token, 'X-Runner-Lease': self.lease,
                     'Content-Type': content_type, 'User-Agent': 'rep0rter-github-runner/1.0'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code < 500 or attempt == 2:
                    raise RuntimeError(f'Worker {action} failed with HTTP {error.code}') from None
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise RuntimeError(f'Worker {action} connection failed') from None
            time.sleep(2 ** attempt)

    def start(self, revision, mode):
        snapshot = self.request('start', json.dumps({'lease': self.lease, 'source_commit': revision, 'mode': mode}).encode())
        with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
            for info in archive.infolist():
                name = info.filename
                if name.startswith('/') or '..' in name.split('/') or '\\' in name or info.file_size > 32*1024*1024:
                    raise ValueError('Invalid worker snapshot')
            archive.extractall(self.root)
        data = (self.root / 'rep0rter.sqlite').read_bytes()
        self.pages = [data[i:i+4096] for i in range(0, len(data), 4096)]

    def commit_database(self, data):
        current = [data[i:i+4096] for i in range(0, len(data), 4096)]
        changes = [[number, base64.b64encode(page).decode()] for number, page in enumerate(current)
                   if number >= len(self.pages) or page != self.pages[number]]
        if not changes and len(current) == len(self.pages):
            return
        sequence = self.sequence + 1
        result = json.loads(self.request('checkpoint', json.dumps({'sequence': sequence, 'count': len(current), 'pages': changes}).encode()))
        if result.get('committed') != sequence:
            raise RuntimeError('Worker did not acknowledge database checkpoint')
        self.sequence, self.pages = sequence, current

    def persist_policy(self, path):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != self.policy_digest:
            self.request('policy', data)
            self.policy_digest = digest

    def hydrate_assets(self, cfg):
        pass  # The lease snapshot already includes all assets.

    def hydrate_site(self):
        pass  # The local published generation is the runner's authoritative copy.

    def publish_site(self, cfg):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for prefix in ('site', 'image-cache'):
                folder = self.root / prefix
                if folder.exists():
                    for path in sorted(folder.rglob('*')):
                        if path.is_file():
                            archive.write(path, prefix + '/' + path.relative_to(folder).as_posix())
            archive.write(self.root / 'exclusions.json', 'exclusions.json')
        self.request('publish', output.getvalue(), 'application/zip')

    def finish(self, success, collection_healthy=False):
        self.request('finish', json.dumps({'success': success, 'collection_healthy': collection_healthy}).encode())


def install_configuration():
    """Add Threads to the existing reporting job without replacing shared secrets."""
    configuration = json.loads(os.environ['REP0RTER_CONFIG'])
    for key, value in configuration.items():
        if key.startswith(('REP0RTER_', 'TELEGRAM_', 'AI_')) and isinstance(value, str):
            os.environ[key] = value
    if 'THREADS_ENABLED' in os.environ:
        enabled = os.environ['THREADS_ENABLED'].lower() in ('1', 'true', 'yes')
        token = os.environ.get('THREADS_ACCESS_TOKEN', '')
        target = os.environ.get('THREADS_USER_ID', '')
        batch = int(os.environ.get('THREADS_BATCH_SIZE', '10'))
        if enabled and (not token or not target or not 1 <= batch <= 10):
            raise ValueError('Threads requires a token, user ID and batch size from 1 to 10')
        os.environ.update(REP0RTER_THREADS_ENABLED='1' if enabled else '0',
                          REP0RTER_THREADS_ACCESS_TOKEN=token,
                          REP0RTER_THREADS_USER_ID=target,
                          REP0RTER_THREADS_BATCH_SIZE=str(batch))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('build', 'report'), default='report')
    args = parser.parse_args()
    os.umask(0o077)
    install_configuration()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='rep0rter-run-') as folder:
        os.environ['REP0RTER_DATA_DIR'] = folder
        os.environ['REP0RTER_SITE_URL'] = 'https://rep0rter.observe.tw'
        runtime = RunnerRuntime(os.environ['REP0RTER_WORKER_URL'], os.environ['REP0RTER_RUNNER_TOKEN'], folder, uuid.uuid4().hex)
        runtime.start(revision, args.mode)
        # Import configuration only after installing the runner's environment.
        from rep0rter.config import Config
        from rep0rter.runtime import services
        from rep0rter.store import Store
        from rep0rter.publishers.site import build
        token = services.set(runtime)
        success = healthy = False
        try:
            cfg = Config()
            if args.mode == 'report':
                from rep0rter.cli import run_once
                collected, posted = run_once(cfg)
                with Store(cfg.db_path) as store:
                    healthy = json.loads(store.get_kv('collector_health', '{}')).get('healthy', False)
                print(f'Reporting complete: collected={collected}, posted={posted}, collection_healthy={healthy}')
            else:
                with Store(cfg.db_path) as store:
                    build(store, cfg)
                print('Site generation published.')
            success = True
        finally:
            services.reset(token)
            runtime.finish(success, healthy)


if __name__ == '__main__':
    main()
