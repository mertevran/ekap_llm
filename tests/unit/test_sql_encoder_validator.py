"""SQL Encoder güvenlik doğrulayıcısının kapalı-varsayılan testleri."""

from __future__ import annotations

import pytest

from app.sql_encoder.catalog import AllowedSchemaCatalog
from app.sql_encoder.validator import SqlSafetyValidator, UnsafeSqlError


@pytest.fixture
def validator() -> SqlSafetyValidator:
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


def test_accepts_parameterized_allowed_select(validator: SqlSafetyValidator) -> None:
    report = validator.validate(
        "SELECT t.id, t.ikn FROM public.tenders AS t "
        "WHERE t.ihale_durumu = %s ORDER BY t.id LIMIT %s",
        parameter_count=2,
    )

    assert report.valid is True
    assert report.statement_type == "SELECT"
    assert report.tables == ["public.tenders"]
    assert report.placeholder_count == 2
    assert len(report.query_hash) == 64


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM public.tenders WHERE id = %s",
        "UPDATE public.tenders SET adi = 'x' WHERE id = %s",
        "INSERT INTO public.tenders (id) VALUES ('1')",
        "DROP TABLE public.tenders",
        "TRUNCATE TABLE public.tenders",
        "ALTER TABLE public.tenders ADD COLUMN injected text",
    ],
)
def test_rejects_every_write_or_schema_change(
    validator: SqlSafetyValidator,
    sql: str,
) -> None:
    with pytest.raises(UnsafeSqlError, match="SELECT|Yasak"):
        validator.validate(sql, parameter_count=sql.count("%s"))


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (
            "SELECT t.id FROM public.tenders AS t; "
            "SELECT t.ikn FROM public.tenders AS t",
            "Tam olarak bir SQL",
        ),
        ("SELECT s.secret FROM public.secrets AS s", "İzin verilmeyen tablo"),
        ("SELECT t.password FROM public.tenders AS t", "İzin verilmeyen sütun"),
        ("SELECT * FROM public.tenders", r"SELECT \*"),
        ("SELECT t.id FROM public.tenders AS t -- gizli", "yorumları"),
        (
            "SELECT t.id, pg_sleep(1) FROM public.tenders AS t",
            "İzin verilmeyen SQL işlevi",
        ),
        ("SELECT t.id FROM public.tenders AS t FOR UPDATE", "Yasak SQL yapısı"),
        (
            "WITH x AS (SELECT t.id FROM public.tenders AS t) SELECT x.id FROM x",
            "SELECT|Yasak",
        ),
    ],
)
def test_rejects_unsafe_select_shapes(
    validator: SqlSafetyValidator,
    sql: str,
    message: str,
) -> None:
    with pytest.raises(UnsafeSqlError, match=message):
        validator.validate(sql, parameter_count=0)


def test_rejects_placeholder_parameter_mismatch(
    validator: SqlSafetyValidator,
) -> None:
    with pytest.raises(UnsafeSqlError, match="yer tutucu"):
        validator.validate(
            "SELECT t.id FROM public.tenders AS t WHERE t.ikn = %s LIMIT %s",
            parameter_count=1,
        )


def test_count_star_is_the_only_allowed_star(validator: SqlSafetyValidator) -> None:
    report = validator.validate(
        "SELECT COUNT(*) AS toplam FROM public.tenders AS t",
        parameter_count=0,
    )

    assert report.valid is True
    assert "COUNT" in report.functions
