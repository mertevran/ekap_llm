import pytest

from app.decision.ollama_decision_model import OllamaDecisionModel


def test_model_validates_valid_chunk_ids_in_kullanilan():
    model = OllamaDecisionModel(name="test", prompt_version="isbak_qwen_decision_v3")
    data = {
        "decision": "uygun",
        "confidence": 0.9,
        "birincil_profil_kodu": "AUS-01",
        "kullanilan_chunk_idleri": ["fake_chunk"],
        "zorunlu_kriter_sonuclari": []
    }
    with pytest.raises(ValueError) as exc:
        model._validate_and_build(data, "AUS-01", ["real_chunk"])
    assert "Uydurma veya geçersiz chunk_id kullanıldı: fake_chunk" in str(exc.value)

def test_model_validates_valid_chunk_ids_in_criteria():
    model = OllamaDecisionModel(name="test", prompt_version="isbak_qwen_decision_v3")
    data = {
        "decision": "uygun",
        "confidence": 0.9,
        "birincil_profil_kodu": "AUS-01",
        "kullanilan_chunk_idleri": ["real_chunk"],
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "c1",
                "status": "karsilaniyor",
                "evidence_chunk_ids": ["fake_chunk"]
            }
        ]
    }
    with pytest.raises(ValueError) as exc:
        model._validate_and_build(data, "AUS-01", ["real_chunk"])
    assert "Kriter (c1) içinde uydurma chunk_id: fake_chunk" in str(exc.value)

def test_model_allows_valid_chunk_ids():
    model = OllamaDecisionModel(name="test", prompt_version="isbak_qwen_decision_v3")
    data = {
        "decision": "uygun",
        "confidence": 0.9,
        "birincil_profil_kodu": "AUS-01",
        "kullanilan_chunk_idleri": ["real_chunk"],
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "c1",
                "status": "karsilaniyor",
                "evidence_chunk_ids": ["real_chunk"]
            }
        ]
    }
    # Should not raise exception
    decision = model._validate_and_build(data, "AUS-01", ["real_chunk"])
    assert decision.kullanilan_chunk_idleri == ["real_chunk"]

def test_model_profile_code_mismatch():
    model = OllamaDecisionModel(name="test", prompt_version="isbak_qwen_decision_v3")
    data = {
        "decision": "uygun",
        "confidence": 0.9,
        "birincil_profil_kodu": "YANLIS-01",
        "kullanilan_chunk_idleri": [],
        "zorunlu_kriter_sonuclari": []
    }
    with pytest.raises(ValueError) as exc:
        model._validate_and_build(data, "DOGRU-01", [])
    assert "Geçersiz profil kodu: YANLIS-01" in str(exc.value)
