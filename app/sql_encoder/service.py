"""Doğrudan kullanılan güvenli SQL Encoder uygulama servisi."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.config import get_settings
from app.sql_encoder.catalog import AllowedSchemaCatalog
from app.sql_encoder.compiler import TenderSqlCompiler
from app.sql_encoder.encoder import OllamaTenderIntentEncoder, TenderIntentEncoder
from app.sql_encoder.executor import ReadonlyQueryExecutor
from app.sql_encoder.models import GeneratedSql, TenderSearchIntent, TenderStatus
from app.sql_encoder.validator import SqlSafetyValidator, UnsafeSqlError


class SqlEncoderService:
    """Şema, SQL üretimi, doğrulama ve çalıştırmayı tek güvenlik sınırında toplar."""

    def __init__(
        self,
        *,
        catalog: AllowedSchemaCatalog | None = None,
        validator: SqlSafetyValidator | None = None,
        compiler: TenderSqlCompiler | None = None,
        encoder: TenderIntentEncoder | None = None,
        executor: ReadonlyQueryExecutor | None = None,
    ) -> None:
        settings = get_settings()
        self.max_rows = settings.sql_encoder_max_rows
        self.catalog = catalog or AllowedSchemaCatalog(
            statement_timeout_ms=settings.sql_encoder_statement_timeout_ms,
            lock_timeout_ms=settings.sql_encoder_lock_timeout_ms,
        )
        self.validator = validator or SqlSafetyValidator(
            catalog=self.catalog,
            max_sql_chars=settings.sql_encoder_max_sql_chars,
            max_joins=settings.sql_encoder_max_joins,
            max_ast_nodes=settings.sql_encoder_max_ast_nodes,
        )
        self.compiler = compiler or TenderSqlCompiler(
            active_status_values=settings.active_tender_status_values,
            max_rows=settings.sql_encoder_max_rows,
            default_random_seed=settings.sql_encoder_default_random_seed,
        )
        self.encoder = encoder or OllamaTenderIntentEncoder(
            name=settings.qwen_model,
            host=settings.ollama_base_url,
            timeout_seconds=float(settings.sql_encoder_timeout_seconds),
            max_attempts=settings.ollama_max_attempts,
            backoff_seconds=settings.ollama_retry_backoff_seconds,
            num_ctx=settings.sql_encoder_num_ctx,
            num_predict=settings.sql_encoder_num_predict,
            num_thread=settings.ollama_num_thread,
            num_batch=settings.ollama_num_batch,
            max_request_chars=settings.sql_encoder_max_request_chars,
        )
        self.executor = executor or ReadonlyQueryExecutor(
            validator=self.validator,
            max_rows=settings.sql_encoder_max_rows,
            statement_timeout_ms=settings.sql_encoder_statement_timeout_ms,
            lock_timeout_ms=settings.sql_encoder_lock_timeout_ms,
        )

    def list_allowed_tables(self) -> dict[str, Any]:
        return {
            "access": "select_only",
            "tables": self.catalog.list_allowed_tables(),
        }

    def get_database_schema(self) -> dict[str, Any]:
        return self.catalog.get_database_schema()

    def generate_sql(
        self,
        *,
        natural_language_request: str,
        random_seed: int | None = None,
    ) -> dict[str, Any]:
        intent = self.encoder.encode(natural_language_request)
        generated = self._compile_intent(
            natural_language_request=natural_language_request,
            intent=intent,
            random_seed=random_seed,
        )
        return generated.model_dump(mode="json")

    def validate_sql(
        self,
        *,
        sql: str,
        parameter_count: int = 0,
    ) -> dict[str, Any]:
        try:
            report = self.validator.validate(sql, parameter_count=parameter_count)
        except UnsafeSqlError as exc:
            return {
                "valid": False,
                "errors": list(exc.errors),
                "query_hash": "",
                "statement_type": "",
                "tables": [],
                "columns": [],
                "functions": [],
                "placeholder_count": 0,
                "join_count": 0,
                "ast_node_count": 0,
            }
        return report.model_dump(mode="json")

    def execute_readonly_query(
        self,
        *,
        sql: str,
        parameters: list[Any] | None = None,
    ) -> dict[str, Any]:
        result = self.executor.execute(sql=sql, parameters=parameters)
        return result.model_dump(mode="json")

    def search_tenders(
        self,
        *,
        status: TenderStatus = "active",
        limit: int = 5,
        random_order: bool = False,
        random_seed: int | None = None,
        city: str | None = None,
        tender_type: str | None = None,
        authority: str | None = None,
        keyword: str | None = None,
        okas_code_prefix: str | None = None,
        tender_date_from: date | None = None,
        tender_date_to: date | None = None,
    ) -> dict[str, Any]:
        intent = TenderSearchIntent(
            status=status,
            limit=limit,
            random_order=random_order,
            city=city,
            tender_type=tender_type,
            authority=authority,
            keyword=keyword,
            okas_code_prefix=okas_code_prefix,
            tender_date_from=tender_date_from,
            tender_date_to=tender_date_to,
        )
        generated = self._compile_intent(
            natural_language_request="structured_search_tenders",
            intent=intent,
            random_seed=random_seed,
        )
        result = self.executor.execute(
            sql=generated.sql,
            parameters=generated.parameters,
        )
        return {
            "intent": intent.model_dump(mode="json"),
            "sql": generated.sql,
            "parameters": generated.parameters,
            "result": result.model_dump(mode="json"),
        }

    def get_tender_by_id(self, *, tender_id: str) -> dict[str, Any]:
        identifier = str(tender_id or "").strip()
        if not identifier:
            raise ValueError("tender_id boş olamaz.")

        primary_sql = """
            SELECT
                t.id, t.ikn, t.adi, t.idare_adi, t.il, t.ihale_tarihi,
                t.ihale_turu, t.ihale_usulu, t.ihale_durumu, t.kapsam,
                t.e_ihale, t.kismi_teklif, t.ihale_yeri, t.isin_yeri,
                t.dokuman_sayisi, t.created_at, t.updated_at, t.takip_durumu
            FROM public.tenders AS t
            WHERE t.id::text = %s OR t.ikn::text = %s
            ORDER BY t.id ASC
            LIMIT %s
        """
        primary = self.executor.execute(
            sql=primary_sql,
            parameters=[identifier, identifier, 1],
        )
        if not primary.rows:
            raise LookupError(f"İhale bulunamadı: {identifier}")

        tender = dict(primary.rows[0])
        resolved_id = str(tender["id"])
        child_queries = {
            "announcements": """
                SELECT a.id, a.tender_id, a.ilan_tipi, a.ilan_tarihi,
                       a.baslik, a.icerik, a.created_at
                FROM public.tender_announcements AS a
                WHERE a.tender_id::text = %s
                ORDER BY a.created_at ASC, a.id ASC
                LIMIT %s
            """,
            "characteristics": """
                SELECT c.id, c.tender_id, c.ozellik
                FROM public.tender_characteristics AS c
                WHERE c.tender_id::text = %s
                ORDER BY c.id ASC
                LIMIT %s
            """,
            "okas_codes": """
                SELECT o.id, o.tender_id, o.kod, o.ad
                FROM public.tender_okas_codes AS o
                WHERE o.tender_id::text = %s
                ORDER BY o.id ASC
                LIMIT %s
            """,
        }
        truncation: dict[str, bool] = {}
        for key, sql in child_queries.items():
            child_result = self.executor.execute(
                sql=sql,
                parameters=[resolved_id, self.max_rows],
            )
            tender[key] = child_result.rows
            truncation[key] = child_result.truncated

        return {
            "tender": tender,
            "child_result_truncated": truncation,
            "access": "select_only",
        }

    def _compile_intent(
        self,
        *,
        natural_language_request: str,
        intent: TenderSearchIntent,
        random_seed: int | None,
    ) -> GeneratedSql:
        compiled = self.compiler.compile(intent, random_seed=random_seed)
        validation = self.validator.validate(
            compiled.sql,
            parameter_count=len(compiled.parameters),
        )
        return GeneratedSql(
            natural_language_request=" ".join(natural_language_request.split()),
            encoder_model=self.encoder.name,
            intent=intent,
            sql=compiled.sql,
            parameters=compiled.parameters,
            validation=validation,
            effective_limit=compiled.effective_limit,
            random_seed=random_seed,
        )


__all__ = ["SqlEncoderService"]
