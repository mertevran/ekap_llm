"""Doğrulanmış SELECT sorgularını sınırlandırılmış salt okunur işlemde çalıştırır."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.sql_encoder.models import ReadonlyQueryResult
from app.sql_encoder.readonly import ConnectionFactory, readonly_cursor
from app.sql_encoder.validator import SqlSafetyValidator


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class ReadonlyQueryExecutor:
    """SQL'i yeniden doğrular, salt okunurluğu denetler ve sonucu keser."""

    def __init__(
        self,
        *,
        validator: SqlSafetyValidator,
        max_rows: int,
        statement_timeout_ms: int,
        lock_timeout_ms: int,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if max_rows <= 0:
            raise ValueError("max_rows pozitif olmalıdır.")
        self.validator = validator
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms
        self.lock_timeout_ms = lock_timeout_ms
        self.connection_factory = connection_factory

    def execute(
        self,
        *,
        sql: str,
        parameters: list[Any] | None = None,
    ) -> ReadonlyQueryResult:
        bound_parameters = list(parameters or [])
        validation = self.validator.validate(
            sql,
            parameter_count=len(bound_parameters),
        )

        cursor_args: dict[str, Any] = {
            "statement_timeout_ms": self.statement_timeout_ms,
            "lock_timeout_ms": self.lock_timeout_ms,
        }
        if self.connection_factory is not None:
            cursor_args["connection_factory"] = self.connection_factory

        started = time.perf_counter()
        with readonly_cursor(**cursor_args) as cursor:
            cursor.execute(sql, bound_parameters)
            if cursor.description is None:
                raise RuntimeError("SELECT sorgusu sonuç sütunu döndürmedi.")
            columns = [str(item.name) for item in cursor.description]
            raw_rows = list(cursor.fetchmany(self.max_rows + 1))

        truncated = len(raw_rows) > self.max_rows
        rows: list[dict[str, Any]] = []
        for raw_row in raw_rows[: self.max_rows]:
            if isinstance(raw_row, Mapping):
                row = {str(key): _json_safe(value) for key, value in raw_row.items()}
            else:
                row = {
                    column: _json_safe(value)
                    for column, value in zip(columns, raw_row, strict=False)
                }
            rows.append(row)

        return ReadonlyQueryResult(
            query_hash=validation.query_hash,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            max_rows=self.max_rows,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            transaction_read_only=True,
        )


__all__ = ["ReadonlyQueryExecutor"]
