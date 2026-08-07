"""PostgreSQL işlemlerini doğrulanmış salt okunur oturumda çalıştırır."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from psycopg.rows import dict_row

from app.database.connection import get_connection

ConnectionFactory = Callable[[], Any]


class ReadonlySessionError(RuntimeError):
    """PostgreSQL oturumu salt okunur olarak doğrulanamadığında üretilir."""


def _first_value(row: Any) -> Any:
    if isinstance(row, Mapping):
        return next(iter(row.values()), None)
    if isinstance(row, (list, tuple)):
        return row[0] if row else None
    return row


@contextmanager
def readonly_cursor(
    *,
    statement_timeout_ms: int,
    lock_timeout_ms: int,
    connection_factory: ConnectionFactory = get_connection,
) -> Iterator[Any]:
    """Bağlantı ve işlem seviyesinde salt okunurluğu zorunlu kılar."""

    if statement_timeout_ms <= 0:
        raise ValueError("statement_timeout_ms pozitif olmalıdır.")
    if lock_timeout_ms <= 0:
        raise ValueError("lock_timeout_ms pozitif olmalıdır.")

    with connection_factory() as connection:
        connection.read_only = True
        with connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(statement_timeout_ms),),
                )
                cursor.execute(
                    "SELECT set_config('lock_timeout', %s, true)",
                    (str(lock_timeout_ms),),
                )
                cursor.execute("SHOW transaction_read_only")
                readonly_value = str(_first_value(cursor.fetchone()) or "").casefold()
                if readonly_value not in {"on", "true", "1"}:
                    raise ReadonlySessionError(
                        "PostgreSQL işlemi salt okunur olarak doğrulanamadı."
                    )
                yield cursor


__all__ = [
    "ConnectionFactory",
    "ReadonlySessionError",
    "readonly_cursor",
]
