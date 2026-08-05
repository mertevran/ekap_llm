"""Unit testler — TenderProfileDecisionRepository (harici — DB gerektiriyor)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.external


@pytest.fixture
def repo():
    from app.database.tender_profile_decision_repository import (
        TenderProfileDecisionRepository,
    )
    return TenderProfileDecisionRepository()


def test_get_existing_decision_not_found(repo):
    result = repo.get_existing_decision(
        tender_id="NONEXISTENT_TENDER",
        profile_code="AUS-99",
        decision_version="v1",
    )
    assert result is None


def test_save_and_retrieve_completed(repo):
    from app.config.isbak_rag_settings import DECISION_VERSION

    tender_id = "TEST_TENDER_REPO_001"
    profile_code = "AUS-99"

    row_id = repo.save_completed(
        tender_id=tender_id,
        ikn="2099/9999",
        tender_name="Test İhale",
        authority_name="Test Kurum",
        profile_code=profile_code,
        profile_name="Test Profil",
        matching_mode="profile",
        retrieval_rank=1,
        retrieval_score=0.75,
        score_breakdown={"final_score": 0.75},
        primary_model="test-model",
        final_decision="uygun",
        final_confidence=0.9,
        tender_source_hash="hash_t_001",
        profile_source_hash="hash_p_001",
        prompt_version="isbak_qwen_decision_v2",
        decision_version=DECISION_VERSION,
    )
    assert row_id > 0

    result = repo.get_existing_decision(
        tender_id=tender_id,
        profile_code=profile_code,
        tender_source_hash="hash_t_001",
        profile_source_hash="hash_p_001",
        primary_model="test-model",
        prompt_version="isbak_qwen_decision_v2",
        decision_version=DECISION_VERSION,
    )
    assert result is not None
    assert result["final_decision"] == "uygun"
    assert result["processing_status"] == "completed"

    # Temizle
    from app.database.connection import get_connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM llm_rag.tender_profile_decision WHERE tender_id = %s",
                (tender_id,),
            )
            conn.commit()


def test_save_failed(repo):
    tender_id = "TEST_TENDER_FAILED_001"
    row_id = repo.save_failed(
        tender_id=tender_id,
        profile_code="AUS-99",
        matching_mode="tender",
        error_message="Test hatası",
    )
    # Satır oluşturulmuş olmalı (id > 0) veya zaten var (-1)
    assert row_id != 0

    # Temizle
    from app.database.connection import get_connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM llm_rag.tender_profile_decision WHERE tender_id = %s",
                (tender_id,),
            )
            conn.commit()
