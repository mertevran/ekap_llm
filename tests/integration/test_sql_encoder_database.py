"""Gerçek PostgreSQL üzerinde SQL Encoder salt okunur bütünleşme testleri."""

from __future__ import annotations

import pytest

from app.sql_encoder.service import SqlEncoderService

pytestmark = [pytest.mark.integration, pytest.mark.external]


def test_live_schema_is_allowed_and_contains_no_credentials(require_database) -> None:
    schema = SqlEncoderService().get_database_schema()

    assert schema["schema_validated"] is True
    assert schema["access"] == "select_only"
    assert schema["credentials_included"] is False
    assert {table["qualified_name"] for table in schema["tables"]} >= {
        "public.tenders",
        "public.tender_okas_codes",
    }
    assert "database_password" not in str(schema)


def test_live_structured_search_is_readonly_and_bounded(require_database) -> None:
    payload = SqlEncoderService().search_tenders(
        status="active",
        limit=5,
        random_order=True,
        random_seed=20_260_806,
    )

    result = payload["result"]
    assert result["transaction_read_only"] is True
    assert result["row_count"] <= 5
    assert result["max_rows"] >= result["row_count"]
    assert payload["sql"].lstrip().upper().startswith("SELECT")
    assert "DELETE" not in payload["sql"].upper()
