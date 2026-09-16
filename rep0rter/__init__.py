"""rep0rter: a virtual reporter for the g0v community.

Pipeline: collectors -> event store (SQLite) -> reporter (select + write)
-> publishers (Telegram, static site + RSS).
"""

__version__ = "0.2.0"
