import json
from unittest.mock import MagicMock, patch

import pytest

from app.decision.ollama_decision_model import OllamaDecisionModel
from app.pipeline.exceptions import DecisionServiceError


def get_valid_json():
    return {
        "decision": "uygun",
        "confidence": 0.9,
        "birincil_profil_kodu": "CAT1",
        "ikincil_profil_kodlari": [],
        "uygunluk_gerekceleri": ["Sebep 1"],
        "uygunsuzluk_gerekceleri": [],
        "zorunlu_kriter_sonuclari": [],
        "eksik_kanitlar": [],
        "kritik_belirsizlikler": [],
        "kullanilan_chunk_idleri": ["c1"],
        "kaynak_disinda_bilgi_var_mi": False,
        "insan_incelemesi_gerekcesi": ""
    }

@patch('app.decision.ollama_decision_model.httpx.Client')
def test_fallback_cleans_ikincil_profil_kodlari(mock_client_class):
    mock_client = MagicMock()
    # Geçersiz ikincil profil aynı yanıt üzerinde güvenli biçimde temizlenir.
    bad_json = get_valid_json()
    bad_json["ikincil_profil_kodlari"] = ["CAT1"]  # Invalid because CAT1 is primary

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": json.dumps(bad_json),
        "done_reason": "stop"
    }

    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client

    model = OllamaDecisionModel(name="qwen2", prompt_version="isbak_qwen_decision_v3")

    result = model.analyze(
        tender_id="1", ikn="1", category_code="CAT1",
        tender_context="ctx", company_context="ctx", valid_chunk_ids=["c1"]
    )

    # It should have cleaned ikincil_profil_kodlari and succeeded
    assert result.ikincil_profil_kodlari == []
    assert result.decision == "uygun"
    assert mock_client.post.call_count == 1


@patch('app.decision.ollama_decision_model.httpx.Client')
def test_fallback_cleans_zorunlu_kriter(mock_client_class):
    mock_client = MagicMock()
    bad_json = get_valid_json()
    bad_json["zorunlu_kriter_sonuclari"] = [
        {
            "criterion_id": "test_ok",
            "description": "guzel",
            "status": "karsilaniyor",
            "explanation": "var",
            "evidence_chunk_ids": ["c1"]
        },
        {
            "criterion_id": "fiyat avantajı", # Invalid
            "description": "fiyat",
            "status": "bilinmiyor",
            "explanation": "fiyat",
            "evidence_chunk_ids": ["c1"]
        }
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": json.dumps(bad_json),
        "done_reason": "stop"
    }

    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client

    model = OllamaDecisionModel(name="qwen2", prompt_version="isbak_qwen_decision_v3")

    result = model.analyze(
        tender_id="1", ikn="1", category_code="CAT1",
        tender_context="ctx", company_context="ctx", valid_chunk_ids=["c1"]
    )

    # Second criterion should be deleted, first should be kept
    assert len(result.zorunlu_kriter_sonuclari) == 1
    assert result.zorunlu_kriter_sonuclari[0].criterion_id == "test_ok"


@patch('app.decision.ollama_decision_model.httpx.Client')
def test_fallback_raises_error_if_decision_invalid(mock_client_class):
    mock_client = MagicMock()
    bad_json = get_valid_json()
    bad_json["decision"] = "invalid_decision"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": json.dumps(bad_json),
        "done_reason": "stop"
    }

    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client

    model = OllamaDecisionModel(name="qwen2", prompt_version="isbak_qwen_decision_v3")

    with pytest.raises(DecisionServiceError, match="Temel alanlar bozuk olduğu için temizleme yapılamadı"):
        model.analyze(
            tender_id="1", ikn="1", category_code="CAT1",
            tender_context="ctx", company_context="ctx", valid_chunk_ids=["c1"]
        )
