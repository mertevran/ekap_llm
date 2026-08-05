import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import Response

from app.decision.ollama_decision_model import OllamaDecisionModel
from app.pipeline.exceptions import DecisionServiceError


@pytest.fixture
def model():
    return OllamaDecisionModel(name="test_model", prompt_version="isbak_qwen_decision_v3")

def test_valid_json_response_parsed(model):
    valid_json = {
        "decision": "uygun",
        "confidence": 0.95,
        "birincil_profil_kodu": "AUS-01",
        "kullanilan_chunk_idleri": ["chunk-1"],
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "c1",
                "status": "karsilaniyor",
                "evidence_chunk_ids": ["chunk-1"]
            }
        ]
    }
    mock_resp = MagicMock(spec=Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": json.dumps(valid_json)}

    with patch("httpx.Client.post", return_value=mock_resp):
        decision = model.analyze(
            tender_id="1", ikn="1", category_code="AUS-01", 
            tender_context="", company_context="", valid_chunk_ids=["chunk-1"]
        )
        assert decision.decision == "uygun"
        assert decision.confidence == 0.95

def test_empty_response_raises_error(model):
    mock_resp = MagicMock(spec=Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "   ", "thinking": "some thinking"}

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(DecisionServiceError) as exc:
            model.analyze(
                tender_id="1", ikn="1", category_code="AUS-01", 
                tender_context="", company_context="", valid_chunk_ids=["chunk-1"]
            )
        assert "Boş model yanıtı" in str(exc.value)

def test_invalid_json_retries_and_fails(model):
    mock_resp = MagicMock(spec=Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "This is not JSON"}

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(Exception) as exc:
            model.analyze(
                tender_id="1", ikn="1", category_code="AUS-01", 
                tender_context="", company_context="", valid_chunk_ids=["chunk-1"]
            )
        assert "JSON kök elemanı obje (dict) olmalıdır" in str(exc.value) or "Kesilmiş cevap oluştu" in str(exc.value)

def test_invalid_json_type_list(model):
    mock_resp = MagicMock(spec=Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "[{\"decision\": \"uygun\"}]"}

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(DecisionServiceError) as exc:
            model.analyze(
                tender_id="1", ikn="1", category_code="AUS-01", 
                tender_context="", company_context="", valid_chunk_ids=["chunk-1"]
            )
        assert "JSON kök elemanı obje (dict) olmalıdır" in str(exc.value)

from app.decision.ollama_decision_model import DECISION_OUTPUT_SCHEMA

def test_decision_output_schema_structure():
    # A. Modül seviyesinde import
    assert isinstance(DECISION_OUTPUT_SCHEMA, dict)
    
    # B. required listesi decision ve confidence içermeli
    req = DECISION_OUTPUT_SCHEMA.get("required", [])
    assert "decision" in req
    assert "confidence" in req
    
    props = DECISION_OUTPUT_SCHEMA.get("properties", {})
    
    # C. decision
    dec = props.get("decision", {})
    assert dec.get("type") == "string"
    assert set(dec.get("enum", [])) == {"uygun", "uygun_degil", "inceleme_gerekli"}
    assert "default" not in dec
    
    # D. confidence
    conf = props.get("confidence", {})
    assert conf.get("type") == "number"
    assert conf.get("minimum") == 0.0
    assert conf.get("maximum") == 1.0
    assert "default" not in conf

def test_decision_output_schema_payload(monkeypatch):
    from app.decision.ollama_decision_model import OllamaDecisionModel
    model = OllamaDecisionModel(name="test")
    
    payloads = []
    
    class MockResponse:
        def __init__(self, data):
            self.data = data
            self.status_code = 200
        def json(self): return self.data
        def raise_for_status(self): pass
        
    class MockClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, json, **kwargs):
            payloads.append(json)
            return MockResponse({
                "response": '{"decision": "uygun", "confidence": 0.8, "birincil_profil_kodu": "TEST", "zorunlu_kriter_sonuclari": []}',
                "done_reason": "stop"
            })
            
    import httpx
    monkeypatch.setattr(httpx, "Client", MockClient)
    
    decision = model.analyze(
        tender_id="T", ikn="I", category_code="TEST", tender_context="t", company_context="c", primary_profile_code="TEST"
    )
    
    # E. payload testinde format == DECISION_OUTPUT_SCHEMA
    assert payloads[0]["format"] == DECISION_OUTPUT_SCHEMA
    
    # G. Geçerli şema yanıtı ModelDecision üretmeli
    assert decision.decision == "uygun"

def test_decision_missing_fields_validation(monkeypatch):
    from app.decision.ollama_decision_model import OllamaDecisionModel
    import pytest
    model = OllamaDecisionModel(name="test")
    
    class MockResponse:
        def __init__(self, data):
            self.data = data
            self.status_code = 200
        def json(self): return self.data
        def raise_for_status(self): pass
        
    class MockClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, json, **kwargs):
            # F. decision alanı yok
            return MockResponse({
                "response": '{"confidence": 0.8, "birincil_profil_kodu": "TEST", "zorunlu_kriter_sonuclari": []}',
                "done_reason": "stop"
            })
            
    import httpx
    monkeypatch.setattr(httpx, "Client", MockClient)
    
    # python_validator hala reddetmeli (decision eksik olunca normalize_decision patlar)
    from app.pipeline.exceptions import DecisionServiceError
    with pytest.raises(DecisionServiceError):
        model.analyze(tender_id="T", ikn="I", category_code="TEST", tender_context="t", company_context="c", primary_profile_code="TEST")

def test_criterion_schema_invalid_fields():
    props = DECISION_OUTPUT_SCHEMA["properties"]["zorunlu_kriter_sonuclari"]["items"]["properties"]
    
    # H. Kriter nesnesinde eski alanlar olmamalı
    assert "kural_kodu" not in props
    assert "sonuc" not in props
    assert "durum" not in props
    assert "aciklama" not in props
    assert "kanit_idleri" not in props
    assert DECISION_OUTPUT_SCHEMA["properties"]["zorunlu_kriter_sonuclari"]["items"].get("additionalProperties") is False
