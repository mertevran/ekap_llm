"""SQL Encoder ile karar hattına aktarılacak ihaleleri doğrudan seçer."""

from __future__ import annotations

from typing import Any, Protocol

from app.sql_encoder.models import TenderSelection
from app.sql_encoder.service import SqlEncoderService


class SqlEncoderSelectionError(RuntimeError):
    """SQL Encoder güvenli ve kullanılabilir bir ihale seçimi üretemediğinde oluşur."""


class SqlEncoderServiceProtocol(Protocol):
    """Seçim akışının kullandığı dar servis sözleşmesi."""

    def list_allowed_tables(self) -> dict[str, Any]: ...

    def get_database_schema(self) -> dict[str, Any]: ...

    def generate_sql(
        self,
        *,
        natural_language_request: str,
        random_seed: int | None = None,
    ) -> dict[str, Any]: ...

    def validate_sql(
        self,
        *,
        sql: str,
        parameter_count: int = 0,
    ) -> dict[str, Any]: ...

    def execute_readonly_query(
        self,
        *,
        sql: str,
        parameters: list[Any] | None = None,
    ) -> dict[str, Any]: ...


def select_tenders_via_sql_encoder(
    *,
    natural_language_request: str,
    random_seed: int | None,
    service: SqlEncoderServiceProtocol | None = None,
) -> TenderSelection:
    """Doğal dil isteğini güvenli SQL seçimine dönüştürüp doğrudan çalıştırır.

    Taşıma veya araç sunucusu kullanılmaz. Aynı süreç içinde şema izin listesi
    doğrulanır, Qwen yalnız tipli arama niyeti üretir, Python parametreli SELECT
    sorgusunu derler ve sorgu salt okunur PostgreSQL işleminde yürütülür.
    """

    sql_service = service or SqlEncoderService()
    allowed = sql_service.list_allowed_tables()
    schema = sql_service.get_database_schema()
    generated = sql_service.generate_sql(
        natural_language_request=natural_language_request,
        random_seed=random_seed,
    )
    sql = str(generated.get("sql") or "")
    parameters = list(generated.get("parameters") or [])

    validation = sql_service.validate_sql(
        sql=sql,
        parameter_count=len(parameters),
    )
    if not validation.get("valid"):
        raise SqlEncoderSelectionError(
            "Üretilen SQL ikinci güvenlik doğrulamasından geçemedi: "
            + "; ".join(str(item) for item in validation.get("errors", []))
        )

    execution = sql_service.execute_readonly_query(
        sql=sql,
        parameters=parameters,
    )
    rows = [dict(row) for row in execution.get("rows", []) if isinstance(row, dict)]
    selected_ikns = list(
        dict.fromkeys(
            str(row.get("ikn") or "").strip()
            for row in rows
            if str(row.get("ikn") or "").strip()
        )
    )
    if not selected_ikns:
        raise SqlEncoderSelectionError(
            "SQL Encoder sorgusu karar hattına aktarılacak ihale bulamadı."
        )

    allowed_names = {
        str(item.get("qualified_name") or "")
        for item in allowed.get("tables", [])
        if isinstance(item, dict)
    }
    schema_tables = [
        str(item.get("qualified_name") or "")
        for item in schema.get("tables", [])
        if isinstance(item, dict)
        and str(item.get("qualified_name") or "") in allowed_names
    ]
    return TenderSelection(
        natural_language_request=" ".join(natural_language_request.split()),
        encoder_model=str(generated.get("encoder_model") or ""),
        sql=sql,
        parameters=parameters,
        random_seed=random_seed,
        selected_ikns=selected_ikns,
        rows=rows,
        schema_tables=schema_tables,
        query_hash=str(execution.get("query_hash") or validation.get("query_hash") or ""),
    )


__all__ = [
    "SqlEncoderSelectionError",
    "SqlEncoderServiceProtocol",
    "select_tenders_via_sql_encoder",
]
