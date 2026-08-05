import pytest
from app.decision.ollama_decision_model import OllamaDecisionModel
from app.pipeline.exceptions import DecisionServiceError

@pytest.fixture
def model():
    return OllamaDecisionModel(name="test_model", prompt_version="isbak_qwen_decision_v3")

def test_reject_empty_strings(model):
    data = {
        "uygunsuzluk_gerekceleri": [""]
    }
    with pytest.raises(ValueError, match="boş veya yalnızca boşluk içeren string"):
        model._validate_semantic_field_usage(data, "tender context", [])

def test_reject_negative_in_uygunluk(model):
    data = {
        "uygunluk_gerekceleri": ["Belge kanıtı bulunmamaktadır."]
    }
    with pytest.raises(ValueError, match="eksiklik veya bilinmezlik ifadesi bulunamaz"):
        model._validate_semantic_field_usage(data, "tender context", [])

def test_allow_negative_in_eksik_kanitlar(model):
    data = {
        "eksik_kanitlar": ["Belge kanıtı bulunmamaktadır."]
    }
    # Should not raise
    model._validate_semantic_field_usage(data, "tender context", [])

def test_reject_price_advantage_as_uncertainty(model):
    data = {
        "kritik_belirsizlikler": ["%15 fiyat avantajı belirsizdir."]
    }
    with pytest.raises(ValueError, match="Fiyat avantajı veya standart teklif süreci"):
        model._validate_semantic_field_usage(data, "tender context", [])

def test_reject_ekap_as_uncertainty(model):
    data = {
        "kritik_belirsizlikler": ["EKAP üzerinden teklif verilmesi belirsizdir."]
    }
    with pytest.raises(ValueError, match="Fiyat avantajı veya standart teklif süreci"):
        model._validate_semantic_field_usage(data, "tender context", [])

def test_reject_generic_financial_gap_without_tender_req(model):
    data = {
        "kritik_belirsizlikler": ["mali yeterlilik eksik"]
    }
    with pytest.raises(ValueError, match="İhalede mali şart yokken"):
        model._validate_semantic_field_usage(data, "genel teknik şartlar", [])

def test_allow_generic_financial_gap_with_tender_req(model):
    data = {
        "kritik_belirsizlikler": ["mali yeterlilik eksik"]
    }
    # Should not raise
    model._validate_semantic_field_usage(data, "İhalede mali yeterlilik belgesi istenmektedir", [])

def test_reject_empty_evidence_in_criteria(model):
    data = {
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "C1",
                "status": "karsilaniyor",
                "evidence_chunk_ids": []
            }
        ]
    }
    with pytest.raises(ValueError, match="evidence_chunk_ids boş olamaz"):
        model._validate_semantic_field_usage(data, "tender context", ["c1"])

def test_allow_valid_unknown_criteria(model):
    data = {
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "C1",
                "status": "bilinmiyor",
                "evidence_chunk_ids": ["c1"]
            }
        ]
    }
    # Should not raise for valid chunk ID
    model._validate_semantic_field_usage(data, "tender context", ["c1"])


def test_reject_price_advantage_in_eksik_kanitlar(model):
    data = {
        "eksik_kanitlar": ["%15 fiyat avantajı sağlanamadı."]
    }
    with pytest.raises(ValueError, match="Fiyat avantajı veya standart teklif süreci"):
        model._validate_semantic_field_usage(data, "tender context", [])

def test_reject_ekap_in_eksik_kanitlar(model):
    data = {
        "eksik_kanitlar": ["EKAP üzerinden teklif verilmemiş."]
    }
    with pytest.raises(ValueError, match="Fiyat avantajı veya standart teklif süreci"):
        model._validate_semantic_field_usage(data, "tender context", [])

def test_reject_chk_in_criterion_id(model):
    data = {
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "chk_12345",
                "status": "karsilaniyor",
                "evidence_chunk_ids": ["chk_12345"]
            }
        ]
    }
    with pytest.raises(ValueError, match="parça kimliği yazılamaz"):
        model._validate_semantic_field_usage(data, "tender context", ["chk_12345"])

def test_reject_unsupported_ikincil_profil(model):
    data = {
        "birincil_profil_kodu": "CAT1",
        "ikincil_profil_kodlari": ["CAT2"],
        "uygunluk_gerekceleri": ["Sadece CAT1 ile ilgili şeyler var."]
    }
    with pytest.raises(ValueError, match="gerekçelerde desteklenmiyor"):
        model._validate_semantic_field_usage(data, "tender context", [])

def test_reject_primary_in_ikincil_profil(model):
    data = {
        "birincil_profil_kodu": "CAT1",
        "ikincil_profil_kodlari": ["CAT1"],
        "uygunluk_gerekceleri": ["CAT1 destekli."]
    }
    with pytest.raises(ValueError, match="Birincil profil kodu ikincil_profil_kodlari listesine eklenemez"):
        model._validate_semantic_field_usage(data, "tender context", [])

def test_allow_supported_ikincil_profil(model):
    data = {
        "birincil_profil_kodu": "CAT1",
        "ikincil_profil_kodlari": ["CAT2"],
        "uygunluk_gerekceleri": ["Bu durum CAT2 profili ile de ilişkilidir."]
    }
    # Should not raise
    model._validate_semantic_field_usage(data, "tender context", [])


def test_reject_karsilanmiyor_without_negative_evidence(model):
    data = {
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "test_1",
                "description": "Şart",
                "status": "karsilanmiyor",
                "explanation": "Kanıt bulunamadı.",
                "evidence_chunk_ids": ["chk_1"]
            }
        ]
    }
    with pytest.raises(ValueError, match="bilinmiyor olmal"):
        model._validate_semantic_field_usage(data, "tender context", ["chk_1"])

def test_accept_karsilanmiyor_with_negative_evidence(model):
    data = {
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "test_1",
                "description": "Şart",
                "status": "karsilanmiyor",
                "explanation": "Belgenin süresi dolmuştur.",
                "evidence_chunk_ids": ["chk_1"]
            }
        ]
    }
    # Should pass
    model._validate_semantic_field_usage(data, "tender context", ["chk_1"])

def test_reject_mandatory_criterion_price_advantage(model):
    data = {
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "Fiyat Avantajı",
                "description": "Yerli malı",
                "status": "bilinmiyor",
                "explanation": "%15 fiyat avantajı sağlanacak.",
                "evidence_chunk_ids": ["chk_1"]
            }
        ]
    }
    with pytest.raises(ValueError, match="Fiyat avantajı kriteri eklenemez"):
        model._validate_semantic_field_usage(data, "tender context", ["chk_1"])

def test_accept_valid_bilinmiyor_criterion(model):
    data = {
        "zorunlu_kriter_sonuclari": [
            {
                "criterion_id": "gercek_sart",
                "description": "Gerçek zorunlu şart",
                "status": "bilinmiyor",
                "explanation": "Belge bulunamadı.",
                "evidence_chunk_ids": ["chk_1"]
            }
        ]
    }
    # Should pass
    model._validate_semantic_field_usage(data, "tender context", ["chk_1"])
