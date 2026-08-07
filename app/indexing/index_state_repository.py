from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import dict_row

from app.database.connection import get_connection

DEFAULT_TABLE_NAME = "llm_rag.tender_index_state"
_TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class TenderIndexState:
    tender_id: str
    ikn: str
    classification: str | None
    evidence_score: int
    source_hash: str
    classifier_version: str
    embedding_model: str | None
    vector_collection: str | None
    index_status: str
    chunk_count: int
    is_active: bool
    tender_updated_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    indexed_at: datetime | None
    deleted_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    chunking_version: str | None = None
    embedding_version: str | None = None
    vector_backend: str | None = None
    index_version: str | None = None
    metadata_hash: str | None = None
    cache_version: str | None = None
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None


class IndexStateRepository:
    """RAG indeksleme durum tablosunu yönetir."""

    def __init__(
        self,
        table_name: str = DEFAULT_TABLE_NAME,
    ) -> None:
        normalized = str(table_name).strip()
        if not _TABLE_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("Durum tablosu adı schema.table biçiminde olmalıdır.")
        self.table_name = normalized

    def _select_columns(self) -> str:
        return """
            tender_id, ikn, classification, evidence_score, source_hash,
            classifier_version, embedding_model, vector_collection,
            index_status, chunk_count, is_active, tender_updated_at,
            first_seen_at, last_seen_at, indexed_at, deleted_at,
            error_message, created_at, updated_at,
            chunking_version, embedding_version, vector_backend,
            index_version, metadata_hash, cache_version,
            processing_started_at, processing_completed_at
        """

    def get_by_tender_id(self, tender_id: str) -> TenderIndexState | None:
        query = f"""
            SELECT {self._select_columns()}
            FROM {self.table_name}
            WHERE tender_id = %s
        """

        with get_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, (tender_id,))
                row = cursor.fetchone()

        if row is None:
            return None

        return TenderIndexState(**row)

    def get_many_by_tender_ids(self, tender_ids: Iterable[str]) -> dict[str, TenderIndexState]:
        normalized_ids = self._normalize_ids(tender_ids)
        if not normalized_ids:
            return {}

        query = f"""
            SELECT {self._select_columns()}
            FROM {self.table_name}
            WHERE tender_id = ANY(%s)
        """

        with get_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, (normalized_ids,))
                rows = cursor.fetchall()

        return {row["tender_id"]: TenderIndexState(**row) for row in rows}

    def upsert_pending(
        self,
        *,
        tender_id: str,
        ikn: str,
        classification: str | None,
        evidence_score: int,
        source_hash: str,
        classifier_version: str,
        tender_updated_at: datetime | None,
        chunking_version: str | None = None,
        embedding_model: str | None = None,
        embedding_version: str | None = None,
        vector_backend: str | None = None,
        index_version: str | None = None,
        metadata_hash: str | None = None,
        cache_version: str | None = None,
    ) -> None:
        query = f"""
            INSERT INTO {self.table_name} (
                tender_id, ikn, classification, evidence_score, source_hash,
                classifier_version, index_status, chunk_count, is_active,
                tender_updated_at, first_seen_at, last_seen_at,
                chunking_version, embedding_model, embedding_version,
                vector_backend, index_version, metadata_hash, cache_version
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, 'pending', 0, TRUE, %s,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (tender_id)
            DO UPDATE SET
                ikn = EXCLUDED.ikn,
                classification = COALESCE({self.table_name}.classification, EXCLUDED.classification),
                evidence_score = EXCLUDED.evidence_score,
                source_hash = EXCLUDED.source_hash,
                classifier_version = EXCLUDED.classifier_version,
                index_status = 'pending',
                chunk_count = 0,
                is_active = TRUE,
                tender_updated_at = EXCLUDED.tender_updated_at,
                last_seen_at = CURRENT_TIMESTAMP,
                indexed_at = NULL,
                error_message = NULL,
                chunking_version = EXCLUDED.chunking_version,
                embedding_model = EXCLUDED.embedding_model,
                embedding_version = EXCLUDED.embedding_version,
                vector_backend = EXCLUDED.vector_backend,
                index_version = EXCLUDED.index_version,
                metadata_hash = EXCLUDED.metadata_hash,
                cache_version = EXCLUDED.cache_version
        """
        self._execute(
            query,
            (
                tender_id,
                ikn,
                classification,
                evidence_score,
                source_hash,
                classifier_version,
                tender_updated_at,
                chunking_version,
                embedding_model,
                embedding_version,
                vector_backend,
                index_version,
                metadata_hash,
                cache_version,
            ),
        )

    def mark_processing(self, tender_id: str) -> None:
        query = f"""
            UPDATE {self.table_name}
            SET index_status = 'processing',
                processing_started_at = CURRENT_TIMESTAMP,
                last_seen_at = CURRENT_TIMESTAMP
            WHERE tender_id = %s
        """
        self._execute(query, (tender_id,))

    def mark_indexed(
        self,
        *,
        tender_id: str,
        chunk_count: int,
        embedding_model: str,
        vector_collection: str,
    ) -> None:
        query = f"""
            UPDATE {self.table_name}
            SET
                index_status = 'indexed',
                chunk_count = %s,
                embedding_model = %s,
                vector_collection = %s,
                is_active = TRUE,
                last_seen_at = CURRENT_TIMESTAMP,
                indexed_at = CURRENT_TIMESTAMP,
                processing_completed_at = CURRENT_TIMESTAMP,
                deleted_at = NULL,
                error_message = NULL
            WHERE tender_id = %s
        """
        self._execute(query, (chunk_count, embedding_model, vector_collection, tender_id))

    def mark_skipped(
        self,
        *,
        tender_id: str,
        classification: str | None,
        evidence_score: int,
        source_hash: str,
        classifier_version: str,
        tender_updated_at: datetime | None,
        skip_reason: str = "skipped",
        ikn: str | None = None,
    ) -> None:
        """
        İndekslenmemesi gereken ihaleyi durum tablosuna kaydeder.

        Kayıt yoksa INSERT, varsa UPDATE yapılır. Böylece örneğin İSBAK'ın
        kendi ihaleleri embedding (gömme) yapılmadan takip tablosunda tutulur
        ve sonraki artımlı indeksleme çalışmalarında yeniden seçilmez.
        """

        normalized_ikn = str(ikn or "").strip()

        query = f"""
            INSERT INTO {self.table_name} (
                tender_id,
                ikn,
                classification,
                evidence_score,
                source_hash,
                classifier_version,
                embedding_model,
                vector_collection,
                index_status,
                chunk_count,
                is_active,
                tender_updated_at,
                first_seen_at,
                last_seen_at,
                indexed_at,
                deleted_at,
                error_message,
                processing_started_at,
                processing_completed_at
            )
            VALUES (
                %s,
                COALESCE(
                    NULLIF(%s, ''),
                    (SELECT t.ikn FROM public.tenders t WHERE t.id = %s),
                    ''
                ),
                %s,
                %s,
                %s,
                %s,
                NULL,
                NULL,
                %s,
                0,
                TRUE,
                %s,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL
            )
            ON CONFLICT (tender_id)
            DO UPDATE SET
                ikn = CASE
                    WHEN EXCLUDED.ikn <> '' THEN EXCLUDED.ikn
                    ELSE {self.table_name}.ikn
                END,
                classification = COALESCE(
                    {self.table_name}.classification,
                    EXCLUDED.classification
                ),
                evidence_score = EXCLUDED.evidence_score,
                source_hash = EXCLUDED.source_hash,
                classifier_version = EXCLUDED.classifier_version,
                embedding_model = NULL,
                vector_collection = NULL,
                index_status = EXCLUDED.index_status,
                chunk_count = 0,
                is_active = TRUE,
                tender_updated_at = EXCLUDED.tender_updated_at,
                last_seen_at = CURRENT_TIMESTAMP,
                indexed_at = NULL,
                deleted_at = NULL,
                error_message = NULL,
                processing_started_at = NULL,
                processing_completed_at = CURRENT_TIMESTAMP
        """

        self._execute(
            query,
            (
                tender_id,
                normalized_ikn,
                tender_id,
                classification,
                evidence_score,
                source_hash,
                classifier_version,
                skip_reason,
                tender_updated_at,
            ),
        )

    def mark_failed(self, *, tender_id: str, error_message: str) -> None:
        query = f"""
            UPDATE {self.table_name}
            SET
                index_status = 'failed',
                last_seen_at = CURRENT_TIMESTAMP,
                error_message = %s
            WHERE tender_id = %s
        """
        self._execute(query, (error_message[:4000], tender_id))

    def mark_deleted(self, *, tender_id: str, is_active: bool) -> None:
        query = f"""
            UPDATE {self.table_name}
            SET
                index_status = 'deleted',
                chunk_count = 0,
                is_active = %s,
                last_seen_at = CURRENT_TIMESTAMP,
                deleted_at = CURRENT_TIMESTAMP,
                error_message = NULL
            WHERE tender_id = %s
        """
        self._execute(query, (is_active, tender_id))

    def update_classification(self, *, tender_id: str, classification: str) -> None:
        query = f"""
            UPDATE {self.table_name}
            SET
                classification = %s,
                last_seen_at = CURRENT_TIMESTAMP
            WHERE tender_id = %s
        """
        self._execute(query, (classification, tender_id))

    def touch_seen(self, tender_ids: Iterable[str]) -> int:
        normalized_ids = self._normalize_ids(tender_ids)
        if not normalized_ids:
            return 0
        query = f"""
            UPDATE {self.table_name}
            SET is_active = TRUE, last_seen_at = CURRENT_TIMESTAMP
            WHERE tender_id = ANY(%s)
        """
        return self._execute(query, (normalized_ids,))

    def find_missing_from_active_snapshot(
        self, active_tender_ids: Iterable[str]
    ) -> list[TenderIndexState]:
        normalized_ids = self._normalize_ids(active_tender_ids)
        if normalized_ids:
            query = f"""
                SELECT {self._select_columns()}
                FROM {self.table_name}
                WHERE is_active = TRUE AND NOT (tender_id = ANY(%s))
            """
            parameters: tuple[object, ...] = (normalized_ids,)
        else:
            query = f"""
                SELECT {self._select_columns()}
                FROM {self.table_name}
                WHERE is_active = TRUE
            """
            parameters = ()
        with get_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, parameters)
                rows = cursor.fetchall()
        return [TenderIndexState(**row) for row in rows]

    @staticmethod
    def _normalize_ids(tender_ids: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in tender_ids if str(value).strip()))

    @staticmethod
    def _execute(query: str, parameters: tuple[object, ...]) -> int:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                row_count = cursor.rowcount
            connection.commit()
        return int(row_count)


__all__ = ["DEFAULT_TABLE_NAME", "IndexStateRepository", "TenderIndexState"]