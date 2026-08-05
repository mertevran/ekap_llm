from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection

from app.config import get_settings


def build_connection_string() -> str:
    settings = get_settings()

    return (
        f"host={settings.database_host} "
        f"port={settings.database_port} "
        f"dbname={settings.database_name} "
        f"user={settings.database_user} "
        f"password={settings.database_password} "
        f"connect_timeout=10"
    )


@contextmanager
def get_connection() -> Iterator[Connection]:
    connection = psycopg.connect(build_connection_string())

    try:
        yield connection
    finally:
        connection.close()
