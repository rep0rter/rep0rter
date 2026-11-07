#!/usr/bin/env python3
"""Export the complete leased Cloudflare state, sealed to the receiver's RSA key.

GitHub artifacts contain ciphertext only. Fetch/decrypt run on the destination;
all configuration, identities, exclusions and delivery state remain private.
"""
import argparse
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import urllib.request
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cloudflare'))
from github_runner import RunnerRuntime, install_configuration

CONTEXT = b'rep0rter-sky-server-migration-v1'


def inventory(database):
    # Transfer snapshots contain the complete main database, never live WAL
    # state. Inspect without creating transient WAL/SHM files that could vanish
    # between manifest generation and archive creation.
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Snapshot database integrity check failed')
        return {name: c.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def fetch(root, credentials, *, keep_lease=False):
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    request = urllib.request.Request(credentials['url'] + '/healthz',
        headers={'Cache-Control': 'no-cache', 'User-Agent': 'rep0rter-github-runner/1.0'})
    with urllib.request.urlopen(request, timeout=20) as r:
        health = json.load(r)
    runtime = RunnerRuntime(credentials['url'], credentials['token'], root, uuid.uuid4().hex)
    acquired = False
    try:
        # Use the deployed source identity for this read-only snapshot operation;
        # never collect, checkpoint, render, publish or deliver through this tool.
        acquired = True
        runtime.start(health['source_commit'], 'build', asset_workers=8)
        counts = inventory(root / 'rep0rter.sqlite')
        manifest = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob('*') if p.is_file()}
        (root / 'snapshot.json').write_text(json.dumps({'source_commit': health['source_commit'],
                                                       'tables': counts, 'sha256': manifest}))
        (root / 'runner.json').write_text(json.dumps({**credentials, 'lease': runtime.lease,
                                                    'source_commit': health['source_commit']}))
        if keep_lease:
            print('Authoritative snapshot downloaded; source account writes remain leased', flush=True)
            return
    finally:
        if acquired and not (keep_lease and (root / 'snapshot.json').exists()):
            try:
                runtime.finish(True)
            except RuntimeError:
                # A failed start may never have acquired our lease. In that
                # case finish is fenced by the lease ID and cannot release a
                # different runner. A lost connection leaves the TTL in force.
                print('Source lease release was not acknowledged; its expiry remains in force', flush=True)
    print('Snapshot verified; source lease released', flush=True)


def seal(data, public_key):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    receiver = serialization.load_pem_public_key(public_key)
    wrapped = receiver.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                algorithm=hashes.SHA256(), label=CONTEXT))
    return b'R0M1' + struct.pack('!H', len(wrapped)) + wrapped + nonce + AESGCM(key).encrypt(nonce, data, CONTEXT)


def unseal(data, private_key):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if data[:4] != b'R0M1':
        raise ValueError('Unsupported encrypted snapshot')
    length = struct.unpack('!H', data[4:6])[0]
    receiver = serialization.load_pem_private_key(private_key, password=None)
    key = receiver.decrypt(data[6:6+length], padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                       algorithm=hashes.SHA256(), label=CONTEXT))
    return AESGCM(key).decrypt(data[6+length:18+length], data[18+length:], CONTEXT)


def unpack(data, root):
    if root.exists():
        raise ValueError('Import destination must be new')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or not {'rep0rter.sqlite', 'exclusions.json', 'snapshot.json', 'service.json', 'runner.json'} <= set(names):
            raise ValueError('Incomplete snapshot archive')
        for item in z.infolist():
            name = item.filename
            if ('\\' in name or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))
                    or item.is_dir() or item.file_size > 40 * 1024 * 1024
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Invalid snapshot archive path or size')
        if sum(i.file_size for i in z.infolist()) > 256 * 1024 * 1024:
            raise ValueError('Snapshot archive is too large')
        root.mkdir(mode=0o700, parents=True)
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(z.read(name))
            path.chmod(0o600)
    manifest = json.loads((root / 'snapshot.json').read_text())
    for name, digest in manifest['sha256'].items():
        if name not in names or hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Snapshot digest mismatch')
    if inventory(root / 'rep0rter.sqlite') != manifest['tables']:
        raise ValueError('Snapshot table inventory mismatch')
    print('Decrypted snapshot; all file hashes and complete database inventory verified')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('export', 'decrypt', 'fetch'))
    p.add_argument('--public-key', type=Path)
    p.add_argument('--private-key', type=Path)
    p.add_argument('--input', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--credentials', type=Path)
    p.add_argument('--keep-lease', action='store_true')
    args = p.parse_args()
    os.umask(0o077)
    if args.action == 'decrypt':
        unpack(unseal(args.input.read_bytes(), args.private_key.read_bytes()), args.output)
    elif args.action == 'fetch':
        fetch(args.output, json.loads(args.credentials.read_text()), keep_lease=args.keep_lease)
    else:
        if args.output.exists():
            raise ValueError('Ciphertext output must be new')
        install_configuration()
        with tempfile.TemporaryDirectory(prefix='rep0rter-export-') as tmp:
            root = Path(tmp) / 'snapshot'
            fetch(root, {'url': os.environ['REP0RTER_WORKER_URL'], 'token': os.environ['REP0RTER_RUNNER_TOKEN']})
            values = {k: v for k, v in os.environ.items() if k.startswith(('REP0RTER_', 'AI_', 'TELEGRAM_'))
                      and k not in ('REP0RTER_CONFIG', 'REP0RTER_RUNNER_TOKEN')}
            (root / 'service.json').write_text(json.dumps(values))
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                for path in root.rglob('*'):
                    if path.is_file():
                        z.write(path, path.relative_to(root).as_posix())
            args.output.write_bytes(seal(archive.getvalue(), args.public_key.read_bytes()))
            args.output.chmod(0o600)
        print('Wrote encrypted migration artifact; no plaintext configuration or snapshot uploaded')


if __name__ == '__main__':
    main()
