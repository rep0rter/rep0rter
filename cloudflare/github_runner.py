"""Run Python/Chromium on GitHub while checkpointing every DB commit to Workers."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import quote


# Observability thresholds mirror the existing engine/frontend checks; these
# values do not change or replace enforcement in those Workers.
CAPACITY_LIMITS = {
    'database_bytes': 32 * 1024 * 1024,
    'zip_bytes': 32 * 1024 * 1024,
    'expanded_bytes': 64 * 1024 * 1024,
    'public_asset_bytes': 64 * 1024 * 1024,
    'public_asset_count': 10000,
    'largest_file_bytes': 1_900_000,
}

TRANSFER_LIMITS = {
    'database_bytes': 32 * 1024 * 1024,
    'expanded_bytes': 160 * 1024 * 1024,
    'public_asset_bytes': 128 * 1024 * 1024,
    'public_asset_count': 10000,
    'largest_file_bytes': 1_900_000,
}


def report_capacity(usage):
    # Only fixed aggregate names and integers reach logs/Actions annotations.
    # Never include archive paths, contents, credentials or database records.
    measurements = {name: int(usage[name]) for name in CAPACITY_LIMITS}
    print('Capacity evidence: ' + json.dumps({
        'usage': measurements, 'limits': CAPACITY_LIMITS,
    }, sort_keys=True), flush=True)
    for name, limit in CAPACITY_LIMITS.items():
        used = measurements[name]
        if used * 5 >= limit * 4:
            print(f'::warning title=Publication capacity::{name} uses {used} of {limit} '
                  f'({used / limit:.1%}); at least 80% of the existing limit.', flush=True)


class RunnerRuntime:
    def __init__(self, url, token, root, lease):
        self.url, self.token, self.root, self.lease = url.rstrip('/'), token, Path(root), lease
        self.sequence = 0
        self.pages = []
        self.poisoned = False
        self.policy_digest = None

    def request(self, action, body=b'', content_type='application/json', headers=None):
        request = urllib.request.Request(self.url + '/__runner/' + action, data=body, method='POST',
            headers={'Authorization': 'Bearer ' + self.token, 'X-Runner-Lease': self.lease,
                     'Content-Type': content_type, 'User-Agent': 'rep0rter-github-runner/1.0', **(headers or {})})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    content = response.read(2 * 1024 * 1024 + 1)
                    if len(content) > 2 * 1024 * 1024:
                        raise RuntimeError(f'Worker {action} response exceeds transfer budget')
                    return content
            except urllib.error.HTTPError as error:
                if error.code < 500 or attempt == 2:
                    raise RuntimeError(f'Worker {action} failed with HTTP {error.code}') from None
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise RuntimeError(f'Worker {action} connection failed') from None
            time.sleep(2 ** attempt)

    def start(self, revision, mode):
        snapshot = json.loads(self.request('start', json.dumps({
            'lease': self.lease, 'source_commit': revision, 'mode': mode, 'protocol': 2}).encode()))
        if snapshot.get('protocol') != 2:
            raise ValueError('Worker does not support bounded transfer')
        files = snapshot.get('files')
        self.validate_manifest(files)
        if type(snapshot.get('database_size')) is not int or not 0 < snapshot['database_size'] <= TRANSFER_LIMITS['database_bytes']:
            raise ValueError('Invalid snapshot database size')
        digest = hashlib.sha256()
        self.pages = []
        with (self.root / 'rep0rter.sqlite').open('wb') as destination:
            for offset in range(0, snapshot['database_size'], 1024 * 1024):
                length = min(1024 * 1024, snapshot['database_size'] - offset)
                data = self.request('snapshot', json.dumps({'path': 'rep0rter.sqlite', 'offset': offset, 'length': length}).encode())
                if len(data) != length:
                    raise ValueError('Incomplete snapshot database range')
                digest.update(data)
                destination.write(data)
                self.pages.extend(data[i:i+4096] for i in range(0, len(data), 4096))
        if digest.hexdigest() != snapshot.get('database_digest'):
            raise ValueError('Snapshot database does not match manifest')
        del data
        for item in files:
            content = self.request('snapshot', json.dumps({'path': item['path']}).encode())
            if len(content) != item['size'] or hashlib.sha256(content).hexdigest() != item['digest']:
                raise ValueError('Snapshot asset does not match manifest')
            destination = self.root / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

    @staticmethod
    def validate_manifest(files):
        if not isinstance(files, list) or len(files) > 10000:
            raise ValueError('Invalid asset manifest')
        if len(json.dumps(files).encode()) > 2 * 1024 * 1024:
            raise ValueError('Asset manifest exceeds transfer budget')
        seen, total, public = set(), 0, 0
        for item in files:
            if not isinstance(item, dict):
                raise ValueError('Invalid asset manifest')
            name, size, digest = item.get('path'), item.get('size'), item.get('digest')
            if (not isinstance(name, str) or len(name) > 2048 or '\\' in name or '\x00' in name
                    or any(part in ('', '.', '..') for part in name.split('/')) or name in seen
                    or not (name == 'exclusions.json' or name.startswith(('site/', 'image-cache/')))
                    or type(size) is not int or not 0 <= size <= TRANSFER_LIMITS['largest_file_bytes']
                    or not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
                raise ValueError('Invalid asset manifest')
            seen.add(name)
            total += size
            public += size if name.startswith('site/') else 0
        if not {'site/index.html', 'site/feed.xml', 'exclusions.json'} <= seen:
            raise ValueError('Incomplete site generation')
        if total > TRANSFER_LIMITS['expanded_bytes'] or public > TRANSFER_LIMITS['public_asset_bytes']:
            raise ValueError('Site generation exceeds storage budget')

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
        files = []
        for prefix in ('site', 'image-cache'):
            folder = self.root / prefix
            if folder.exists():
                for path in sorted(folder.rglob('*')):
                    if path.is_file():
                        if path.stat().st_size > TRANSFER_LIMITS['largest_file_bytes']:
                            raise ValueError('Generated file exceeds storage row budget')
                        content = path.read_bytes()
                        files.append({'path': prefix + '/' + path.relative_to(folder).as_posix(),
                                      'size': len(content), 'digest': hashlib.sha256(content).hexdigest()})
        policy = self.root / 'exclusions.json'
        content = policy.read_bytes()
        files.append({'path': 'exclusions.json', 'size': len(content), 'digest': hashlib.sha256(content).hexdigest()})
        self.validate_manifest(files)
        usage = {'database_bytes': sum(map(len, self.pages)),
                 'expanded_bytes': sum(item['size'] for item in files),
                 'public_asset_bytes': sum(item['size'] for item in files if item['path'].startswith('site/')),
                 'public_asset_count': sum(item['path'].startswith('site/') for item in files),
                 'largest_file_bytes': max(item['size'] for item in files)}
        print('Capacity evidence: ' + json.dumps({'protocol': 2, 'usage': usage, 'limits': TRANSFER_LIMITS}, sort_keys=True), flush=True)
        for name, limit in TRANSFER_LIMITS.items():
            if usage[name] * 5 >= limit * 4:
                print(f'::warning title=Publication capacity::{name} uses {usage[name]} of {limit}; at least 80% of the existing limit.', flush=True)
        if usage['database_bytes'] > TRANSFER_LIMITS['database_bytes']:
            raise ValueError('Database exceeds memory budget')
        publication = uuid.uuid4().hex
        response = json.loads(self.request('publish-begin', json.dumps({'publication': publication, 'files': files}).encode()))
        missing = response.get('missing')
        known = {item['path']: item for item in files}
        if not isinstance(missing, list) or any(not isinstance(name, str) or name not in known for name in missing) or len(missing) != len(set(missing)):
            raise ValueError('Invalid publication response')
        for name in missing:
            content = (self.root / name).read_bytes()
            if len(content) != known[name]['size'] or hashlib.sha256(content).hexdigest() != known[name]['digest']:
                raise ValueError('Generated asset changed during publication')
            self.request('publish-file', content, 'application/octet-stream',
                         headers={'X-Publication': publication, 'X-Asset-Path': quote(name, safe='')})
        self.request('publish-commit', headers={'X-Publication': publication})

    def finish(self, success, collection_healthy=False):
        self.request('finish', json.dumps({'success': success, 'collection_healthy': collection_healthy}).encode())


def install_configuration():
    """Overlay public model and Threads settings without replacing shared secrets."""
    model_override = os.environ.get('AI_MODEL')
    additional_feeds = os.environ.get('REPORT_ADDITIONAL_FEEDS', '')
    cfj_days = os.environ.get('REPORT_CFJ_COLLECT_DAYS', '')
    if cfj_days and not 1 <= int(cfj_days) <= 365:
        raise ValueError('CfJ collection days must be between 1 and 365')
    configuration = json.loads(os.environ['REP0RTER_CONFIG'])
    for key, value in configuration.items():
        if key.startswith(('REP0RTER_', 'TELEGRAM_', 'AI_')) and isinstance(value, str):
            os.environ[key] = value
    if model_override:
        os.environ['AI_MODEL'] = model_override
    if additional_feeds:
        feeds = os.environ.get('REP0RTER_FEEDS', '') + ',' + additional_feeds
        os.environ['REP0RTER_FEEDS'] = ','.join(dict.fromkeys(
            url.strip() for url in feeds.split(',') if url.strip()))
    if cfj_days:
        os.environ['REP0RTER_CFJ_COLLECT_DAYS'] = str(int(cfj_days))
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
    parser.add_argument('--mode', choices=('build', 'report', 'deliver'), default='report')
    parser.add_argument('--post-id', type=int)
    args = parser.parse_args()
    if args.mode == 'deliver' and (args.post_id is None or args.post_id < 1):
        parser.error('deliver requires a positive --post-id')
    if args.mode != 'deliver' and args.post_id is not None:
        parser.error('--post-id requires deliver mode')
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
            print(f'AI model: {cfg.ai_model}')
            if args.mode == 'report':
                from rep0rter.cli import run_once
                collected, posted = run_once(cfg)
                with Store(cfg.db_path) as store:
                    healthy = json.loads(store.get_kv('collector_health', '{}')).get('healthy', False)
                    from rep0rter.acceptance import report as acceptance_report
                    print('Acceptance evidence: ' + json.dumps(acceptance_report(store), ensure_ascii=False))
                print(f'Reporting complete: collected={collected}, posted={posted}, collection_healthy={healthy}')
            elif args.mode == 'deliver':
                from rep0rter.delivery import enqueue_existing, deliver_pending
                with Store(cfg.db_path) as store:
                    job_id = enqueue_existing(cfg, store, args.post_id)
                    build(store, cfg)
                    deliver_pending(cfg, store)
                    job = store.conn.execute('SELECT status,message_id FROM delivery_jobs WHERE id=?', (job_id,)).fetchone()
                    if job['status'] != 'sent':
                        raise RuntimeError(f'Telegram job {job_id} is {job["status"]}; inspect the outbox before retrying')
                    print(f'Telegram delivered: post={args.post_id}, job={job_id}, message={job["message_id"]}')
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
