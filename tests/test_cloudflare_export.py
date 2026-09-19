import importlib.util
from pathlib import Path
import sqlite3
import stat
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('cf_export', Path(__file__).parents[1] / 'cloudflare/export.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_export_snapshots_wal_and_excludes_secrets_and_unpublished_files(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    published = data / '.site-releases' / 'current'
    published.mkdir(parents=True)
    (published / 'index.html').write_text('site')
    (published / 'feed.xml').write_text('feed')
    (data / 'site').symlink_to(published, target_is_directory=True)
    (data / '.env').write_text('PRIVATE_SECRET=must-not-export')
    (data / 'backups').mkdir()
    (data / 'backups/old.sqlite').write_text('old private data')
    (data / 'exclusions.json').write_text('{"schema":1,"rules":[],"tombstones":[]}')
    connection = sqlite3.connect(data / 'rep0rter.sqlite')
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY)')
    connection.execute('INSERT INTO posts VALUES(1)')
    connection.commit()
    archive = tmp_path / 'export.zip'
    module.export(data, archive)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist()) == {'rep0rter.sqlite', 'site/index.html', 'site/feed.xml', 'exclusions.json'}
        restored = sqlite3.connect(':memory:')
        restored.deserialize(z.read('rep0rter.sqlite'))
        assert restored.execute('SELECT * FROM posts').fetchall() == [(1,)]
        restored.close()
    connection.close()
    with pytest.raises(FileExistsError):
        module.export(data, archive)


def test_export_refuses_incomplete_site_without_creating_archive(tmp_path):
    archive = tmp_path / 'export.zip'
    with pytest.raises(ValueError):
        module.export(tmp_path / 'missing', archive)
    assert not archive.exists()
