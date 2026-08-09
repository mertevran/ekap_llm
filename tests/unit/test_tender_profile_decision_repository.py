"""Unit testler — TenderProfileDecisionRepository (mocked)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_db_connection():
    with patch("app.database.tender_profile_decision_repository.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        yield mock_cursor

@pytest.fixture
def repo(mock_db_connection):
    from app.database.tender_profile_decision_repository import (
        TenderProfileDecisionRepository,
    )
    return TenderProfileDecisionRepository()

def test_get_existing_decision_not_found(repo, mock_db_connection):
    mock_db_connection.fetchone.return_value = None

    result = repo.get_existing_decision(
        tender_id="NONEXISTENT_TENDER",
        profile_code="AUS-99",
        decision_version="v1",
    )
    assert result is None
    mock_db_connection.execute.assert_called_once()

def test_save_and_retrieve_completed(repo, mock_db_connection):
    from app.config.isbak_rag_settings import DECISION_VERSION

    tender_id = "TEST_TENDER_REPO_001"
    profile_code = "AUS-99"

    # Simulate INSERT returning an ID
    mock_db_connection.fetchone.return_value = (1,)

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
    assert row_id == 1

    # Simulate SELECT returning a row
    mock_db_connection.fetchone.return_value = {
        "final_decision": "uygun",
        "processing_status": "completed"
    }

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

def test_save_failed(repo, mock_db_connection):
    tender_id = "TEST_TENDER_FAILED_001"

    # Simulate INSERT returning an ID
    mock_db_connection.fetchone.return_value = (2,)

    row_id = repo.save_failed(
        tender_id=tender_id,
        profile_code="AUS-99",
        matching_mode="tender",
        error_message="Test hatası",
    )
    assert row_id == 2
    mock_db_connection.execute.assert_called_once()
