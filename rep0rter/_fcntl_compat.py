"""Minimal fcntl compatibility for Windows test runs.

The project uses file locking only as an advisory coordination primitive; when
`fcntl` is unavailable we can safely no-op the lock operations.
"""
from __future__ import annotations

import os
from typing import Any


class _Compat:
    LOCK_EX = 1
    LOCK_NB = 2

    @staticmethod
    def flock(fd: Any, operation: int) -> None:
        return None

    @staticmethod
    def lockf(fd: Any, operation: int, *args: Any, **kwargs: Any) -> None:
        return None


fcntl = _Compat()
