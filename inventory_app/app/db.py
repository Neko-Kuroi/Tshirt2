"""素の sqlite3 を使う薄いラッパー。

- 行は dict のサブクラス(Row)で返し、row["col"] でも row.col でも読める(Jinja2からも使える)
- 自動コミットモードで接続し、書き込みは transaction() で BEGIN IMMEDIATE ... COMMIT する
"""
import sqlite3
from contextlib import contextmanager

from flask import current_app, g


class Row(dict):
    __slots__ = ()

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _row_factory(cursor, row):
    return Row(zip([c[0] for c in cursor.description], row))


def connect(path):
    db = sqlite3.connect(path, isolation_level=None, timeout=5)  # timeout = busy_timeout(秒)
    db.row_factory = _row_factory
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction():
    """書き込み用。例外が出たら全部ロールバックする。"""
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")
