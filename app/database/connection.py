from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection
from psycopg.conninfo import make_conninfo

from app.config import get_settings


def build_connection_string() -> str:
    settings = get_settings()
    parameters = {
        "host": settings.database_host,
        "port": settings.database_port,
        "dbname": settings.database_name,
        "user": settings.database_user,
        "password": settings.database_password,
        "connect_timeout": settings.database_connect_timeout_seconds,
        "application_name": settings.database_application_name,
        "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
    }
    if settings.database_sslmode.strip():
        parameters["sslmode"] = settings.database_sslmode.strip()
    return make_conninfo(**parameters)


@contextmanager
def get_connection() -> Iterator[Connection]:
    connection = psycopg.connect(build_connection_string())

    try:
        yield connection
    finally:
        connection.close()
