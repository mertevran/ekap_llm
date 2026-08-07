"""Doğrulanmış arama niyetini parametreli PostgreSQL sorgusuna derler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.sql_encoder.models import TenderSearchIntent


@dataclass(frozen=True)
class CompiledTenderQuery:
    sql: str
    parameters: list[Any]
    effective_limit: int


class TenderSqlCompiler:
    """Modelin serbest SQL üretmesine izin vermeyen sabit sorgu derleyicisi."""

    _SELECT_COLUMNS = (
        "t.id",
        "t.ikn",
        "t.adi",
        "t.idare_adi",
        "t.il",
        "t.ihale_tarihi",
        "t.ihale_turu",
        "t.ihale_usulu",
        "t.ihale_durumu",
        "t.kapsam",
        "t.e_ihale",
        "t.kismi_teklif",
        "t.ihale_yeri",
        "t.isin_yeri",
        "t.dokuman_sayisi",
        "t.takip_durumu",
    )
    _OWN_AUTHORITY_PATTERNS = (
        "%isbak%",
        "%istanbul bilişim ve akıllı kent teknolojileri%",
        "%istanbul bilisim ve akilli kent teknolojileri%",
    )

    def __init__(
        self,
        *,
        active_status_values: list[str],
        max_rows: int,
        default_random_seed: int,
    ) -> None:
        self.active_status_values = [
            " ".join(str(value).split())
            for value in active_status_values
            if str(value).strip()
        ]
        if not self.active_status_values:
            raise ValueError("En az bir aktif ihale durumu tanımlanmalıdır.")
        if max_rows <= 0:
            raise ValueError("max_rows pozitif olmalıdır.")
        self.max_rows = max_rows
        self.default_random_seed = default_random_seed

    @staticmethod
    def _literal_contains(value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _literal_prefix(value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"{escaped}%"

    def compile(
        self,
        intent: TenderSearchIntent,
        *,
        random_seed: int | None,
    ) -> CompiledTenderQuery:
        effective_limit = min(intent.limit, self.max_rows)
        conditions: list[str] = []
        parameters: list[Any] = []

        if intent.status == "active":
            conditions.append("t.ihale_durumu = ANY(%s::text[])")
            parameters.append(self.active_status_values)
            conditions.append("t.ihale_tarihi >= CURRENT_TIMESTAMP")
        elif intent.status == "inactive":
            conditions.append(
                "(t.ihale_durumu IS NULL OR NOT (t.ihale_durumu = ANY(%s::text[])))"
            )
            parameters.append(self.active_status_values)

        for pattern in self._OWN_AUTHORITY_PATTERNS:
            conditions.append("COALESCE(t.idare_adi, '') NOT ILIKE %s")
            parameters.append(pattern)

        if intent.city:
            conditions.append("t.il ILIKE %s ESCAPE '\\'")
            parameters.append(self._literal_contains(intent.city))
        if intent.tender_type:
            conditions.append("t.ihale_turu ILIKE %s ESCAPE '\\'")
            parameters.append(self._literal_contains(intent.tender_type))
        if intent.authority:
            conditions.append("t.idare_adi ILIKE %s ESCAPE '\\'")
            parameters.append(self._literal_contains(intent.authority))
        if intent.keyword:
            conditions.append(
                "(t.adi ILIKE %s ESCAPE '\\' OR t.kapsam ILIKE %s ESCAPE '\\')"
            )
            keyword_pattern = self._literal_contains(intent.keyword)
            parameters.extend([keyword_pattern, keyword_pattern])
        if intent.okas_code_prefix:
            conditions.append(
                "EXISTS ("
                "SELECT 1 FROM public.tender_okas_codes AS o "
                "WHERE o.tender_id::text = t.id::text "
                "AND o.kod LIKE %s ESCAPE '\\'"
                ")"
            )
            parameters.append(self._literal_prefix(intent.okas_code_prefix))
        if intent.tender_date_from is not None:
            conditions.append("t.ihale_tarihi >= %s")
            parameters.append(intent.tender_date_from.isoformat())
        if intent.tender_date_to is not None:
            conditions.append("t.ihale_tarihi <= %s")
            parameters.append(intent.tender_date_to.isoformat())

        where_sql = " AND\n    ".join(conditions) if conditions else "TRUE"
        if intent.random_order:
            seed = self.default_random_seed if random_seed is None else random_seed
            order_sql = "md5(COALESCE(t.ikn::text, t.id::text) || %s)"
            parameters.append(str(seed))
        else:
            order_sql = "t.ihale_tarihi ASC NULLS LAST, t.ikn ASC, t.id ASC"

        parameters.append(effective_limit)
        selected_columns = ",\n    ".join(self._SELECT_COLUMNS)
        sql = (
            "SELECT\n"
            f"    {selected_columns}\n"
            "FROM public.tenders AS t\n"
            "WHERE\n"
            f"    {where_sql}\n"
            f"ORDER BY {order_sql}\n"
            "LIMIT %s"
        )
        return CompiledTenderQuery(
            sql=sql,
            parameters=parameters,
            effective_limit=effective_limit,
        )


__all__ = ["CompiledTenderQuery", "TenderSqlCompiler"]
