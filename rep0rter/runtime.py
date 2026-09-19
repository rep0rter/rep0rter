"""Optional Workers services; the ordinary Python runtime remains the default."""
from contextvars import ContextVar
import sqlite3
import sys

services = ContextVar('rep0rter_services', default=None)

if sys.platform != 'emscripten':
    try:
        import fcntl as locks
    except ModuleNotFoundError:  # Windows local development
        from ._fcntl_compat import fcntl as locks
else:
    class locks:
        # The Workers entrypoint serializes every application operation. There
        # are no OS processes or shared filesystem writers in that runtime.
        LOCK_EX = 2
        LOCK_NB = 4

        @staticmethod
        def flock(*args):
            if services.get() is None:
                raise RuntimeError('Workers file operations require serialized runtime services')


class DurabilityError(BaseException):
    """Abort this invocation on uncertain persistence; never continue publishing."""


class DurableConnection(sqlite3.Connection):
    """Keep SQLite transaction semantics, durably flushing commits before I/O.

    The Workers runtime stores changed database pages in one atomic Durable
    Object transaction. Local memory is a cache, never the recovery source.
    A failed flush poisons the invocation and forces rehydration from storage.
    """
    def _persist(self):
        runtime = services.get()
        if runtime.poisoned:
            raise DurabilityError('Database persistence failed; reload required')
        if self.in_transaction:
            return
        version = (self.total_changes,
                   super().execute("PRAGMA schema_version").fetchone()[0],
                   super().execute("PRAGMA user_version").fetchone()[0])
        if version == getattr(self, "_persisted_version", None):
            return
        if super().execute("PRAGMA page_count").fetchone()[0] == 0:
            return
        try:
            runtime.commit_database(self.serialize())
            self._persisted_version = version
        except BaseException as exc:
            import logging
            logging.getLogger(__name__).exception("Durable database flush failed")
            runtime.poisoned = True
            raise DurabilityError('Database persistence failed; reload required') from exc

    def execute(self, sql, parameters=()):
        if services.get().poisoned:
            raise DurabilityError('Database persistence failed; reload required')
        result = super().execute(sql, parameters)
        if not self.in_transaction:
            self._persist()
        return result

    def executescript(self, script):
        self.commit()
        try:
            return super().executescript(script)
        finally:
            self._persist()

    def commit(self):
        super().commit()
        self._persist()

    def __exit__(self, typ, value, traceback):
        if typ is None:
            try:
                self.commit()
            except BaseException:
                self.rollback()
                raise
        else:
            self.rollback()
        return False


def connect(path):
    if services.get() is None:
        return sqlite3.connect(path)
    return sqlite3.connect(path, factory=DurableConnection)


def sleep(seconds):
    if sys.platform != 'emscripten':
        import time
        time.sleep(seconds)
    else:
        import asyncio
        from pyodide.ffi import run_sync
        run_sync(asyncio.sleep(seconds))
