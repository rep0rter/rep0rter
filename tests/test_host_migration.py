"""Host migration gates, health privacy, and encrypted snapshot integrity."""
import importlib.util
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_host_deploy_requires_current_main_and_successful_exact_commit(monkeypatch):
    deploy = module('host_deploy', 'host-deploy.py')
    sha = 'a' * 40
    good = dict(id=1, head_sha=sha, head_branch='main', event='push', status='completed',
                conclusion='success', head_repository={'full_name': 'rep0rter/rep0rter'})
    call = Mock(side_effect=[sha + '\trefs/heads/main', json.dumps({'workflow_runs': [good]})])
    monkeypatch.setattr(deploy, 'run', call)
    deploy.tested_main(sha)
    call.side_effect = [sha, json.dumps({'workflow_runs': [good, {**good, 'id': 2, 'conclusion': 'failure'}]})]
    with pytest.raises(ValueError, match='Offline tests'):
        deploy.tested_main(sha)
    call.side_effect = ['b' * 40]
    with pytest.raises(ValueError, match='current remote main'):
        deploy.tested_main(sha)


def test_host_units_do_not_start_reporting_during_activation(tmp_path):
    deploy = module('host_units', 'host-deploy.py')
    files = deploy.unit_files(tmp_path)
    assert 'Restart=' not in files['rep0rter-report.service']
    assert 'Persistent=true' in files['rep0rter-report.timer']
    assert 'MemoryMax=2G' in files['rep0rter-web.service']


def test_host_health_reports_source_without_private_error_or_database_writes(tmp_path, monkeypatch):
    from rep0rter.config import Config
    from rep0rter.store import Store
    from rep0rter.web import create_app
    monkeypatch.setenv('REP0RTER_HOST_ROOT', str(tmp_path))
    monkeypatch.setenv('REP0RTER_SOURCE_COMMIT', 'a' * 40)
    cfg = Config(data_dir=tmp_path)
    cfg.site_dir.mkdir()
    (cfg.site_dir / 'index.html').write_text('<html>reader</html>')
    with Store(cfg.db_path) as store:
        store.conn.execute("INSERT INTO runs(started_at,finished_at,error) VALUES(1,2,'private-provider-response')")
        store.conn.commit()
    before = cfg.db_path.read_bytes()
    client = create_app(cfg).test_client()
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.json['runtime'] == 'sky-server'
    assert response.json['source_commit'] == 'a' * 40
    assert response.json['last_error'] == 'Reporting failed'
    assert response.json['scheduled'] is False
    assert b'private-provider-response' not in response.data
    assert cfg.db_path.read_bytes() == before
    (tmp_path / 'reporting-enabled').touch()
    assert client.get('/healthz').json['scheduled'] is True


def test_snapshot_encryption_rejects_tampering_and_other_receivers():
    crypto = pytest.importorskip('cryptography')
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    snapshot = module('host_snapshot', 'cloudflare-snapshot.py')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    sealed = snapshot.seal(b'private snapshot', public)
    assert b'private snapshot' not in sealed
    assert snapshot.unseal(sealed, private) == b'private snapshot'
    with pytest.raises(InvalidTag):
        snapshot.unseal(sealed[:-1] + bytes([sealed[-1] ^ 1]), private)


def test_snapshot_import_rejects_traversal_before_creating_destination(tmp_path):
    import io
    import zipfile
    snapshot = module('host_unpack', 'cloudflare-snapshot.py')
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        for name in ['rep0rter.sqlite', 'exclusions.json', 'snapshot.json', 'service.json', 'runner.json', '../escape']:
            z.writestr(name, '{}')
    with pytest.raises(ValueError, match='Invalid snapshot'):
        snapshot.unpack(data.getvalue(), tmp_path / 'import')
    assert not (tmp_path / 'import').exists()


def test_snapshot_inventory_does_not_create_wal_files_or_change_database(tmp_path):
    snapshot = module('host_inventory', 'cloudflare-snapshot.py')
    database = tmp_path / 'rep0rter.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE preserved(value TEXT)')
        connection.execute("INSERT INTO preserved VALUES ('saved')")
    connection.close()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert snapshot.inventory(database) == {'preserved': 1}
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_parallel_snapshot_download_checks_each_asset_and_database(tmp_path, monkeypatch):
    import hashlib
    import sys
    sys.path.insert(0, str(ROOT / 'cloudflare'))
    from github_runner import RunnerRuntime
    content = {'site/index.html': b'<html>reader</html>', 'site/feed.xml': b'<rss/>', 'exclusions.json': b'{}'}
    database = b'x' * 4096
    manifest = [{'path': k, 'size': len(v), 'digest': hashlib.sha256(v).hexdigest()} for k, v in content.items()]
    runtime = RunnerRuntime('https://synthetic.test', 'synthetic-key', tmp_path, '0' * 32)

    def request(action, body=b'', *args, **kwargs):
        if action == 'start':
            return json.dumps({'protocol': 2, 'database_size': len(database),
                               'database_digest': hashlib.sha256(database).hexdigest(), 'files': manifest}).encode()
        payload = json.loads(body)
        return database if payload['path'] == 'rep0rter.sqlite' else content[payload['path']]

    monkeypatch.setattr(runtime, 'request', request)
    runtime.start('a' * 40, 'build', asset_workers=4)
    assert (tmp_path / 'rep0rter.sqlite').read_bytes() == database
    assert all((tmp_path / k).read_bytes() == v for k, v in content.items())
    content['site/index.html'] = b'corrupted'
    with pytest.raises(ValueError, match='does not match manifest'):
        runtime.start('a' * 40, 'build', asset_workers=4)
