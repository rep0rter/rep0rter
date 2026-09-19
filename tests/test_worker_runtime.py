"""Persistence failures must never become successful publications."""
import sqlite3
from types import SimpleNamespace
import pytest

from rep0rter.runtime import connect, services, DurabilityError


@pytest.fixture
def runtime():
    state = SimpleNamespace(poisoned=False, durable=None, commits=0)
    def persist(data):
        state.durable = data
        state.commits += 1
    state.commit_database = persist
    token = services.set(state)
    try:
        yield state
    finally:
        services.reset(token)


def recovered(runtime):
    result = sqlite3.connect(':memory:')
    result.deserialize(runtime.durable)
    return result


def test_committed_transaction_survives_loss_of_local_files(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY, title TEXT)')
    with db:
        db.execute('INSERT INTO posts VALUES (1,?)', ('First',))
        db.execute('INSERT INTO posts VALUES (2,?)', ('Second',))
        assert recovered(runtime).execute('SELECT count(*) FROM posts').fetchone()[0] == 0
    db.close()
    (tmp_path / 'db.sqlite').unlink()
    restored = recovered(runtime)
    assert restored.execute('SELECT * FROM posts').fetchall() == [(1, 'First'), (2, 'Second')]


def test_rollback_is_not_persisted(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY, title TEXT)')
    with pytest.raises(sqlite3.IntegrityError):
        with db:
            db.execute("INSERT INTO posts VALUES (1,'First')")
            db.execute("INSERT INTO posts VALUES (1,'Duplicate')")
    assert db.execute('SELECT count(*) FROM posts').fetchone()[0] == 0
    assert recovered(runtime).execute('SELECT count(*) FROM posts').fetchone()[0] == 0
    db.close()


def test_failed_flush_aborts_invocation_even_through_exception_handlers(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY)')
    def fail(data):
        raise OSError('Storage unavailable')
    runtime.commit_database = fail
    with pytest.raises(DurabilityError):
        with db:
            db.execute('INSERT INTO posts VALUES (1)')
    assert runtime.poisoned
    with pytest.raises(DurabilityError):
        db.execute('SELECT * FROM posts')
    assert recovered(runtime).execute('SELECT count(*) FROM posts').fetchone()[0] == 0
    db.close()


def test_explicit_commit_and_scripts_are_persistent(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.executescript('CREATE TABLE posts(id INTEGER PRIMARY KEY); INSERT INTO posts VALUES (1);')
    assert recovered(runtime).execute('SELECT * FROM posts').fetchall() == [(1,)]
    db.execute('INSERT INTO posts VALUES (2)')
    db.commit()
    assert recovered(runtime).execute('SELECT * FROM posts').fetchall() == [(1,), (2,)]
    db.close()


def test_uncommitted_close_does_not_publish(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY)')
    db.execute('INSERT INTO posts VALUES (1)')
    db.close()
    assert recovered(runtime).execute('SELECT count(*) FROM posts').fetchone()[0] == 0


def test_wal_snapshot_can_be_reopened_after_restart(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY)')
    with db:
        db.execute('INSERT INTO posts VALUES (1)')
    restored = tmp_path / 'restored.sqlite'
    restored.write_bytes(runtime.durable)
    with sqlite3.connect(restored) as check:
        assert check.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert check.execute('SELECT * FROM posts').fetchall() == [(1,)]
    db.close()
