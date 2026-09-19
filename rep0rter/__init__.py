"""rep0rter: an AI reporter covering civic-tech communities, including g0v, Code for Korea, and Code for Japan.

Pipeline: collectors -> event store (SQLite) -> reporter (select + write)
-> publishers (Telegram, static site + RSS).
"""

__version__ = "0.2.0"
