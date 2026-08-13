from __future__ import annotations

import contextlib
from importlib import resources
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .config import settings

_POOL: psycopg.Connection | None = None


@contextlib.contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Short-lived connection. This app is low-throughput; a pool is overkill."""
    conn = psycopg.connect(settings.dsn, row_factory=dict_row, autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    sql = resources.files("scout").joinpath("schema.sql").read_text(encoding="utf-8")
    with connect() as conn:
        conn.execute(sql)


def query(sql: str, params: Any = None) -> list[dict]:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall()


def execute(sql: str, params: Any = None) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount
