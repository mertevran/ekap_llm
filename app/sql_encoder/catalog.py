"""SQL Encoder katmanı için değiştirilemez tablo ve sütun izin listesi."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.database.tender_repository import DatabaseSchemaError, TenderRepository
from app.sql_encoder.readonly import ConnectionFactory, readonly_cursor


class AllowedSchemaCatalog:
    """Yalnız EKAP karar hattının ihtiyaç duyduğu kaynak şemasını açar."""

    schema_name = "public"

    def __init__(
        self,
        *,
        statement_timeout_ms: int,
        lock_timeout_ms: int,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self.statement_timeout_ms = statement_timeout_ms
        self.lock_timeout_ms = lock_timeout_ms
        self.connection_factory = connection_factory
        self._columns = {
            table.casefold(): frozenset(column.casefold() for column in columns)
            for table, columns in TenderRepository.REQUIRED_SOURCE_COLUMNS.items()
        }

    @property
    def allowed_tables(self) -> dict[str, frozenset[str]]:
        return dict(self._columns)

    def is_allowed_table(self, *, schema: str, table: str) -> bool:
        normalized_schema = (schema or self.schema_name).casefold()
        return (
            normalized_schema == self.schema_name
            and table.casefold() in self._columns
        )

    def is_allowed_column(self, *, table: str, column: str) -> bool:
        return column.casefold() in self._columns.get(table.casefold(), frozenset())

    def list_allowed_tables(self) -> list[dict[str, Any]]:
        return [
            {
                "schema": self.schema_name,
                "table": table,
                "qualified_name": f"{self.schema_name}.{table}",
                "columns": sorted(columns),
                "access": "select_only",
            }
            for table, columns in sorted(self._columns.items())
        ]

    def get_database_schema(self) -> dict[str, Any]:
        """Canlı şemayı salt okunur işlemde denetler ve izinli kısmı döndürür."""

        cursor_args: dict[str, Any] = {
            "statement_timeout_ms": self.statement_timeout_ms,
            "lock_timeout_ms": self.lock_timeout_ms,
        }
        if self.connection_factory is not None:
            cursor_args["connection_factory"] = self.connection_factory

        table_names = sorted(self._columns)
        with readonly_cursor(**cursor_args) as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = ANY(%s::text[])
                ORDER BY table_name, ordinal_position
                """,
                (self.schema_name, table_names),
            )
            rows = cursor.fetchall()

        actual: dict[str, set[str]] = defaultdict(set)
        details: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            table = str(row["table_name"]).casefold()
            column = str(row["column_name"]).casefold()
            actual[table].add(column)
            if column in self._columns.get(table, frozenset()):
                details[table].append(
                    {
                        "name": column,
                        "data_type": str(row["data_type"]),
                        "nullable": str(row["is_nullable"]).upper() == "YES",
                    }
                )

        missing = {
            table: sorted(required - actual.get(table, set()))
            for table, required in self._columns.items()
            if required - actual.get(table, set())
        }
        if missing:
            detail = "; ".join(
                f"{table}: {', '.join(columns)}"
                for table, columns in sorted(missing.items())
            )
            raise DatabaseSchemaError(
                "SQL Encoder katmanı için gerekli EKAP şeması eksik: " + detail
            )

        return {
            "schema": self.schema_name,
            "access": "select_only",
            "schema_validated": True,
            "credentials_included": False,
            "tables": [
                {
                    "name": table,
                    "qualified_name": f"{self.schema_name}.{table}",
                    "columns": details.get(table, []),
                }
                for table in table_names
            ],
        }


__all__ = ["AllowedSchemaCatalog"]
