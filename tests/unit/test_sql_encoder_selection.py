"""Doğrudan SQL Encoder ihale seçim zincirinin testleri."""

from __future__ import annotations

from typing import Any

import pytest

from app.sql_encoder.selection import (
    SqlEncoderSelectionError,
    select_tenders_via_sql_encoder,
)


class FakeSqlEncoderService:
    def __init__(self, *, validation_valid: bool = True) -> None:
        self.calls: list[str] = []
        self.validation_valid = validation_valid

    def list_allowed_tables(self) -> dict[str, Any]:
        self.calls.append("list_allowed_tables")
        return {
            "access": "select_only",
            "tables": [{"qualified_name": "public.tenders"}],
        }

    def get_database_schema(self) -> dict[str, Any]:
        self.calls.append("get_database_schema")
        return {
            "schema_validated": True,
            "tables": [{"qualified_name": "public.tenders"}],
        }

    def generate_sql(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append("generate_sql")
        return {
            "encoder_model": "qwen3.5:4b-q4_K_M",
            "sql": "SELECT t.ikn FROM public.tenders AS t LIMIT %s",
            "parameters": [2],
        }

    def validate_sql(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append("validate_sql")
        return {
            "valid": self.validation_valid,
            "query_hash": "abc123",
            "errors": [] if self.validation_valid else ["reddedildi"],
        }

    def execute_readonly_query(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append("execute_readonly_query")
        return {
            "query_hash": "abc123",
            "rows": [{"ikn": "2026/1"}, {"ikn": "2026/2"}],
        }


def test_direct_selection_executes_full_security_chain() -> None:
    fake_service = FakeSqlEncoderService()

    selection = select_tenders_via_sql_encoder(
        natural_language_request="Aktif 2 rastgele ihaleyi getir",
        random_seed=42,
        service=fake_service,
    )

    assert selection.selected_ikns == ["2026/1", "2026/2"]
    assert selection.schema_tables == ["public.tenders"]
    assert selection.encoder_model == "qwen3.5:4b-q4_K_M"
    assert fake_service.calls == [
        "list_allowed_tables",
        "get_database_schema",
        "generate_sql",
        "validate_sql",
        "execute_readonly_query",
    ]


def test_direct_selection_stops_before_database_when_validation_fails() -> None:
    fake_service = FakeSqlEncoderService(validation_valid=False)

    with pytest.raises(SqlEncoderSelectionError, match="ikinci güvenlik"):
        select_tenders_via_sql_encoder(
            natural_language_request="Aktif 2 ihaleyi getir",
            random_seed=42,
            service=fake_service,
        )

    assert "execute_readonly_query" not in fake_service.calls
