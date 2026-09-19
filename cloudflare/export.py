"""Create a consistent SQLite snapshot and site archive; never include .env."""
import argparse
from pathlib import Path
import os
import sqlite3
import tempfile
import zipfile


def export(data_dir, output):
    data_dir, output = Path(data_dir).resolve(), Path(output)
    # Resolve the published symlink once so the archive has one generation.
    site = (data_dir / 'site').resolve()
    if not (site / 'index.html').is_file() or not (site / 'feed.xml').is_file():
        raise ValueError('A complete published site is required')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream, tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / 'rep0rter.sqlite'
            source = sqlite3.connect((data_dir / 'rep0rter.sqlite').as_uri() + '?mode=ro', uri=True)
            target = sqlite3.connect(database)
            try:
                source.backup(target)
                target.execute('PRAGMA journal_mode=DELETE')
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Database integrity check failed')
            finally:
                target.close()
                source.close()
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.write(database, 'rep0rter.sqlite')
                for prefix, directory in [('site', site), ('image-cache', data_dir / 'image-cache')]:
                    if directory.exists():
                        for path in sorted(directory.rglob('*')):
                            if path.is_file() and not path.is_symlink():
                                archive.write(path, prefix + '/' + path.relative_to(directory).as_posix())
                policy = data_dir / 'exclusions.json'
                if policy.is_file():
                    archive.write(policy, 'exclusions.json')
    except BaseException:
        output.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.data_dir, args.output)
    print('Created private migration archive; SQLite integrity check passed.')
