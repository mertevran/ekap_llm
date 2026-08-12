"""llm_rag.tender_profile_decision tablosu için repository.

Karar kayıtlarını oluşturur, günceller ve sorgular.
Veritabanı erişimi mevcut psycopg bağlantı yaklaşımıyla uyumludur.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row

from app.config.isbak_rag_settings import DECISION_VERSION
from app.database.connection import get_connection

logger = logging.getLogger(__name__)

_TABLE = "llm_rag.tender_profile_decision"


class TenderProfileDecisionRepository:
    """llm_rag.tender_profile_decision tablosu işlemleri."""

    # ------------------------------------------------------------------
    # Okuma
    # ------------------------------------------------------------------

    def get_existing_decision(
        self,
        *,
        tender_id: str,
        profile_code: str,
        tender_source_hash: str | None = None,
        profile_source_hash: str | None = None,
        prompt_version: str | None = None,
        primary_model: str | None = None,
        decision_version: str = DECISION_VERSION,
    ) -> dict[str, Any] | None:
        """Benzersiz anahtar alanlarına göre mevcut kararı döndürür.

        Yoksa None döner.
        """
        sql = f"""
            SELECT *
            FROM {_TABLE}
            WHERE tender_id = %s
              AND profile_code = %s
              AND COALESCE(tender_source_hash, '') = %s
              AND COALESCE(profile_source_hash, '') = %s
              AND COALESCE(prompt_version, '') = %s
              AND COALESCE(primary_model, '') = %s
              AND decision_version = %s
            LIMIT 1
        """
        params = (
            str(tender_id),
            str(profile_code).upper(),
            str(tender_source_hash or ""),
            str(profile_source_hash or ""),
            str(prompt_version or ""),
            str(primary_model or ""),
            str(decision_version),
        )
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return dict(row) if row else None

    def list_by_tender(
        self,
        tender_id: str,
        *,
        decision_version: str = DECISION_VERSION,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """tender_id'ye göre karar kayıtlarını listeler."""
        sql = f"""
            SELECT *
            FROM {_TABLE}
            WHERE tender_id = %s
              AND decision_version = %s
            ORDER BY created_at DESC
            LIMIT %s
        """
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (str(tender_id), str(decision_version), limit))
                return [dict(r) for r in cur.fetchall()]

    def list_by_profile(
        self,
        profile_code: str,
        *,
        decision_version: str = DECISION_VERSION,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """profile_code'a göre karar kayıtlarını listeler."""
        sql = f"""
            SELECT *
            FROM {_TABLE}
            WHERE profile_code = %s
              AND decision_version = %s
            ORDER BY retrieval_score DESC NULLS LAST
            LIMIT %s
        """
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    sql, (str(profile_code).upper(), str(decision_version), limit)
                )
                return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Yazma
    # ------------------------------------------------------------------

    def mark_processing(
        self,
        *,
        tender_id: str,
        profile_code: str,
        matching_mode: str,
        primary_model: str | None = None,
        prompt_version: str | None = None,
        tender_source_hash: str | None = None,
        profile_source_hash: str | None = None,
        decision_version: str = DECISION_VERSION,
    ) -> int:
        """Yeni bir kayıt oluşturur ve durumunu 'processing' yapar.

        Returns:
            Oluşturulan kaydın id değeri.
        """
        sql = f"""
            INSERT INTO {_TABLE} (
                tender_id, profile_code, matching_mode,
                primary_model, prompt_version,
                tender_source_hash, profile_source_hash,
                decision_version, processing_status
            ) VALUES (
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, 'processing'
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """
        params = (
            str(tender_id),
            str(profile_code).upper(),
            str(matching_mode),
            primary_model,
            prompt_version,
            tender_source_hash,
            profile_source_hash,
            str(decision_version),
        )
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                conn.commit()
                return int(row[0]) if row else -1

    def save_completed(
        self,
        *,
        tender_id: str,
        ikn: str | None = None,
        tender_name: str | None = None,
        authority_name: str | None = None,
        profile_code: str,
        profile_name: str | None = None,
        matching_mode: str,
        retrieval_rank: int | None = None,
        retrieval_score: float | None = None,
        score_breakdown: dict[str, Any] | None = None,
        primary_model: str | None = None,
        prompt_version: str | None = None,
        model_decision: str | None = None,
        model_confidence: float | None = None,
        final_decision: str,
        final_confidence: float | None = None,
        positive_reasons: list[str] | None = None,
        negative_reasons: list[str] | None = None,
        missing_evidence: list[str] | None = None,
        evidence_chunk_ids: list[str] | None = None,
        human_review_required: bool = False,
        human_review_reason: str | None = None,
        tender_source_hash: str | None = None,
        profile_source_hash: str | None = None,
        decision_version: str = DECISION_VERSION,
    ) -> int:
        """Tamamlanan kararı kaydeder (INSERT … ON CONFLICT UPDATE).

        Returns:
            Kaydın id değeri.
        """
        sql = f"""
            INSERT INTO {_TABLE} (
                tender_id, ikn, tender_name, authority_name,
                profile_code, profile_name,
                matching_mode,
                retrieval_rank, retrieval_score, score_breakdown,
                primary_model, secondary_model, prompt_version,
                model_decision, model_confidence,
                secondary_decision, secondary_confidence,
                final_decision, final_confidence,
                positive_reasons, negative_reasons, missing_evidence,
                evidence_chunk_ids,
                human_review_required, human_review_reason,
                tender_source_hash, profile_source_hash,
                decision_version,
                processing_status, evaluated_at
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s,
                %s,
                %s, %s, %s,
                %s, NULL, %s, /* NULL for legacy secondary_model */
                %s, %s,
                NULL, NULL, /* NULL for legacy secondary_decision and secondary_confidence */
                %s, %s,
                %s, %s, %s,
                %s,
                %s, %s,
                %s, %s,
                %s,
                'completed', %s
            )
            ON CONFLICT (
                    tender_id,
                    profile_code,
                    (COALESCE(tender_source_hash, '')),
                    (COALESCE(profile_source_hash, '')),
                    (COALESCE(prompt_version, '')),
                    (COALESCE(primary_model, '')),
                    decision_version
                )
            DO UPDATE SET
                ikn                   = EXCLUDED.ikn,
                tender_name           = EXCLUDED.tender_name,
                authority_name        = EXCLUDED.authority_name,
                profile_name          = EXCLUDED.profile_name,
                matching_mode         = EXCLUDED.matching_mode,
                retrieval_rank        = EXCLUDED.retrieval_rank,
                retrieval_score       = EXCLUDED.retrieval_score,
                score_breakdown       = EXCLUDED.score_breakdown,
                secondary_model       = NULL, /* legacy compatibility */
                model_decision        = EXCLUDED.model_decision,
                model_confidence      = EXCLUDED.model_confidence,
                secondary_decision    = NULL, /* legacy compatibility */
                secondary_confidence  = NULL, /* legacy compatibility */
                final_decision        = EXCLUDED.final_decision,
                final_confidence      = EXCLUDED.final_confidence,
                positive_reasons      = EXCLUDED.positive_reasons,
                negative_reasons      = EXCLUDED.negative_reasons,
                missing_evidence      = EXCLUDED.missing_evidence,
                evidence_chunk_ids    = EXCLUDED.evidence_chunk_ids,
                human_review_required = EXCLUDED.human_review_required,
                human_review_reason   = EXCLUDED.human_review_reason,
                processing_status     = 'completed',
                evaluated_at          = EXCLUDED.evaluated_at,
                updated_at            = CURRENT_TIMESTAMP
            RETURNING id
        """

        def _jsonb(val: list | dict | None) -> str | None:
            if val is None:
                return None
            return json.dumps(val, ensure_ascii=False)

        params = (
            str(tender_id),
            ikn,
            tender_name,
            authority_name,
            str(profile_code).upper(),
            profile_name,
            str(matching_mode),
            retrieval_rank,
            retrieval_score,
            _jsonb(score_breakdown),
            primary_model,
            prompt_version,
            model_decision,
            model_confidence,
            str(final_decision),
            final_confidence,
            _jsonb(positive_reasons),
            _jsonb(negative_reasons),
            _jsonb(missing_evidence),
            _jsonb(evidence_chunk_ids),
            human_review_required,
            human_review_reason,
            tender_source_hash,
            profile_source_hash,
            str(decision_version),
            datetime.now(UTC),
        )

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                conn.commit()
                return int(row[0]) if row else -1

    def save_failed(
        self,
        *,
        tender_id: str,
        profile_code: str,
        matching_mode: str,
        error_message: str,
        tender_source_hash: str | None = None,
        profile_source_hash: str | None = None,
        primary_model: str | None = None,
        prompt_version: str | None = None,
        decision_version: str = DECISION_VERSION,
        final_decision: str = "inceleme_gerekli",
    ) -> int:
        """Başarısız kararı kaydeder.

        Returns:
            Kaydın id değeri.
        """
        sql = f"""
            INSERT INTO {_TABLE} (
                tender_id, profile_code, matching_mode,
                primary_model, prompt_version,
                tender_source_hash, profile_source_hash,
                decision_version,
                final_decision,
                processing_status, error_message,
                human_review_required
            ) VALUES (
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s,
                %s,
                'failed', %s,
                TRUE
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """
        params = (
            str(tender_id),
            str(profile_code).upper(),
            str(matching_mode),
            primary_model,
            prompt_version,
            tender_source_hash,
            profile_source_hash,
            str(decision_version),
            str(final_decision),
            str(error_message)[:2000],
        )
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                conn.commit()
                return int(row[0]) if row else -1


__all__ = ["TenderProfileDecisionRepository"]
