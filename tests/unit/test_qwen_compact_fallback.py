import json
from unittest.mock import MagicMock, patch

import pytest

from app.decision.ollama_decision_model import DECISION_OUTPUT_SCHEMA, OllamaDecisionModel
from app.pipeline.exceptions import TruncatedModelOutput


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
def test_qwen_compact_fallback_success(mock_client_class):
    # İlk yanıt kesik (done_reason=length), ikinci yanıt başarılı
    mock_client = MagicMock()
    mock_response_1 = MagicMock()
    mock_response_1.status_code = 200
    mock_response_1.json.return_value = {
        "response": '{"decision": "uygun", "confidence":', # Kesik JSON
        "done_reason": "length"
    }

    mock_response_2 = MagicMock()
    mock_response_2.status_code = 200
    mock_response_2.json.return_value = {
        "response": json.dumps(get_valid_json()),
        "done_reason": "stop"
    }

    mock_client.post.side_effect = [mock_response_1, mock_response_2]
    mock_client_class.return_value.__enter__.return_value = mock_client

    model = OllamaDecisionModel(name="qwen2", prompt_version="isbak_qwen_decision_v3")
    assert model.prompt_version == "isbak_qwen_decision_v3"

    result = model.analyze(
        tender_id="1", ikn="1", category_code="CAT1",
        tender_context="ctx", company_context="ctx", valid_chunk_ids=["c1"]
    )

    assert result.decision == "uygun"
    assert model.prompt_version == "isbak_qwen_decision_v3" # prompt_version shouldn't change
    assert mock_client.post.call_count == 2

    # İkinci çağrıda compact isteminin kullanıldığını kontrol edebiliriz
    second_call_kwargs = mock_client.post.call_args_list[1][1]
    assert "isbak_qwen_decision_compact" in second_call_kwargs["json"]["prompt"] or "kısa JSON üret" in second_call_kwargs["json"]["prompt"]

@patch('app.decision.ollama_decision_model.httpx.Client')
def test_qwen_compact_fallback_both_truncated(mock_client_class):
    # Hem ana yanıt hem compact yanıt kesik
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": '{"decision": "uygun", "confidence":',
        "done_reason": "length"
    }

    mock_client.post.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client

    model = OllamaDecisionModel(name="qwen2", prompt_version="isbak_qwen_decision_v3")

    with pytest.raises(TruncatedModelOutput):
        model.analyze(
            tender_id="1", ikn="1", category_code="CAT1",
            tender_context="ctx", company_context="ctx", valid_chunk_ids=["c1"]
        )

def test_schema_limits_present():
    props = DECISION_OUTPUT_SCHEMA["properties"]
    assert props["uygunluk_gerekceleri"]["items"]["maxLength"] == 300
    assert props["uygunluk_gerekceleri"]["maxItems"] == 3

    assert props["zorunlu_kriter_sonuclari"]["maxItems"] == 5
    crit_props = props["zorunlu_kriter_sonuclari"]["items"]["properties"]
    assert crit_props["criterion_id"]["maxLength"] == 160
    assert crit_props["description"]["maxLength"] == 300
    assert crit_props["explanation"]["maxLength"] == 400

    assert props["insan_incelemesi_gerekcesi"]["maxLength"] == 500

def test_qwen_num_predict_default():
    from app.config import get_settings
    settings = get_settings()
    model = OllamaDecisionModel(name="qwen2", prompt_version="isbak_qwen_decision_v3")
    # Kısa v4 sözleşmesi için varsayılan üretim bütçesi ayardan (settings) okunmalıdır.
    assert model.num_predict == settings.qwen_decision_num_predict
