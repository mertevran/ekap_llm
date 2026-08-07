"""Salt okunur PostgreSQL çalıştırıcısının yalıtılmış testleri."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.sql_encoder.catalog import AllowedSchemaCatalog
from app.sql_encoder.executor import ReadonlyQueryExecutor
from app.sql_encoder.readonly import ReadonlySessionError, readonly_cursor
from app.sql_encoder.validator import SqlSafetyValidator


class FakeCursor:
    def __init__(self, *, readonly: str = "on") -> None:
        self.readonly = readonly
        self.calls: list[tuple[str, Any]] = []
        self.description: list[SimpleNamespace] | None = None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, parameters: Any = None) -> None:
        self.calls.append((sql, parameters))
        if sql.lstrip().upper().startswith("SELECT T.ID"):
            self.description = [
                SimpleNamespace(name="id"),
                SimpleNamespace(name="ihale_tarihi"),
                SimpleNamespace(name="tutar"),
            ]

    def fetchone(self) -> dict[str, str]:
        return {"transaction_read_only": self.readonly}

    def fetchmany(self, size: int) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = [
            {
                "id": index,
                "ihale_tarihi": date(2026, 8, index),
                "tutar": Decimal(f"{index}.50"),
            }
            for index in range(1, 4)
        ]
        return rows[:size]


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.fake_cursor = cursor
        self.read_only = False

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    @contextmanager
    def transaction(self):
        yield

    def cursor(self, **_kwargs: object) -> FakeCursor:
        return self.fake_cursor


def _validator() -> SqlSafetyValidator:
    catalog = AllowedSchemaCatalog(
        statement_timeout_ms=1_000,
        lock_timeout_ms=100,
    )
    return SqlSafetyValidator(
        catalog=catalog,
        max_sql_chars=12_000,
        max_joins=3,
        max_ast_nodes=500,
    )


def test_executor_forces_readonly_timeouts_and_caps_rows() -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    executor = ReadonlyQueryExecutor(
        validator=_validator(),
        max_rows=2,
        statement_timeout_ms=1_234,
        lock_timeout_ms=321,
        connection_factory=lambda: connection,
    )

    result = executor.execute(
        sql="SELECT t.id, t.ihale_tarihi, t.dokuman_sayisi AS tutar "
        "FROM public.tenders AS t LIMIT %s",
        parameters=[3],
    )

    assert connection.read_only is True
    assert result.row_count == 2
    assert result.truncated is True
    assert result.rows[0]["ihale_tarihi"] == "2026-08-01"
    assert result.rows[0]["tutar"] == "1.50"
    assert cursor.calls[0][1] == ("1234",)
    assert cursor.calls[1][1] == ("321",)
    assert cursor.calls[2][0] == "SHOW transaction_read_only"


def test_readonly_cursor_fails_closed_when_database_reports_off() -> None:
    cursor = FakeCursor(readonly="off")
    connection = FakeConnection(cursor)

    with pytest.raises(ReadonlySessionError, match="salt okunur"):
        with readonly_cursor(
            statement_timeout_ms=1_000,
            lock_timeout_ms=100,
            connection_factory=lambda: connection,
        ):
            pass

