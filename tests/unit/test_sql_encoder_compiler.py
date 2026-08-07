"""Tipli ihale arama niyetinin güvenli SQL'e derlenme testleri."""

from __future__ import annotations

from app.sql_encoder.catalog import AllowedSchemaCatalog
from app.sql_encoder.compiler import TenderSqlCompiler
from app.sql_encoder.models import TenderSearchIntent
from app.sql_encoder.validator import SqlSafetyValidator


def _compiler(*, max_rows: int = 100) -> TenderSqlCompiler:
    return TenderSqlCompiler(
        active_status_values=["İhale İlanı Yayımlanmış, Katılıma Açık"],
        max_rows=max_rows,
        default_random_seed=20_260_806,
    )


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


def test_active_random_request_is_deterministic_and_validated() -> None:
    compiled = _compiler().compile(
        TenderSearchIntent(status="active", limit=5, random_order=True),
        random_seed=1234,
    )

    assert "t.ihale_durumu = ANY(%s::text[])" in compiled.sql
    assert "t.ihale_tarihi >= CURRENT_TIMESTAMP" in compiled.sql
    assert "ORDER BY md5(" in compiled.sql
    assert compiled.parameters[-2:] == ["1234", 5]
    assert compiled.effective_limit == 5
    assert compiled.sql.count("NOT ILIKE %s") == 3
    assert "isbak" not in compiled.sql.casefold()
    report = _validator().validate(
        compiled.sql,
        parameter_count=len(compiled.parameters),
    )
    assert report.valid is True


def test_keyword_is_bound_as_data_and_cannot_inject_sql() -> None:
    malicious = "kamera%' OR 1=1; DROP TABLE tenders; --"
    compiled = _compiler().compile(
        TenderSearchIntent(keyword=malicious, limit=7),
        random_seed=None,
    )

    assert malicious not in compiled.sql
    assert any("DROP TABLE" in str(parameter) for parameter in compiled.parameters)
    assert compiled.parameters[-1] == 7
    assert _validator().validate(
        compiled.sql,
        parameter_count=len(compiled.parameters),
    ).valid


def test_limit_is_capped_by_server_policy() -> None:
    compiled = _compiler(max_rows=10).compile(
        TenderSearchIntent(status="all", limit=500),
        random_seed=None,
    )

    assert compiled.effective_limit == 10
    assert compiled.parameters[-1] == 10
    assert "ihale_durumu = ANY" not in compiled.sql
    assert "CURRENT_TIMESTAMP" not in compiled.sql


def test_inactive_request_does_not_apply_active_date_rule() -> None:
    compiled = _compiler().compile(
        TenderSearchIntent(status="inactive", limit=5),
        random_seed=None,
    )

    assert "CURRENT_TIMESTAMP" not in compiled.sql


def test_okas_prefix_uses_allowed_correlated_subquery() -> None:
    compiled = _compiler().compile(
        TenderSearchIntent(okas_code_prefix="3232", limit=3),
        random_seed=None,
    )

    assert "public.tender_okas_codes" in compiled.sql
    assert "%3232%" not in compiled.sql
    assert "3232%" in compiled.parameters
    report = _validator().validate(
        compiled.sql,
        parameter_count=len(compiled.parameters),
    )
    assert set(report.tables) == {
        "public.tender_okas_codes",
        "public.tenders",
    }
