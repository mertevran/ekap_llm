import pytest

from app.decision.models import CriterionResult, ModelDecision
from app.validation.isbak_rule_validator import IsbakRuleValidator


@pytest.fixture
def base_decision():
    return ModelDecision(
        model_name="test_model",
        decision="uygun",
        confidence=0.9,
        birincil_profil_kodu="AUS-01",
        ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=["Her şey uygun"],
        uygunsuzluk_gerekceleri=[],
        zorunlu_kriter_sonuclari=[],
        eksik_kanitlar=[],
        kritik_belirsizlikler=[],
        kullanilan_chunk_idleri=["chunk-1"],
        kaynak_disinda_bilgi_var_mi=False,
        insan_incelemesi_gerekcesi=""
    )

def test_valid_uygun_accepted(base_decision):
    validator = IsbakRuleValidator()
    result = validator.validate(
        tender_id="T1", ikn="1", category_code="AUS-01",
        primary_decision=base_decision, evidence_count=1, valid_chunk_ids=["chunk-1"]
    )
    assert result.passed is True
    assert result.forced_decision is None

def test_uygun_without_reasons_rejected():
    validator = IsbakRuleValidator()
    decision = ModelDecision(
        model_name="test_model", decision="uygun", confidence=0.9,
        birincil_profil_kodu="AUS-01", ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=[], # Boş
        uygunsuzluk_gerekceleri=[], zorunlu_kriter_sonuclari=[],
    )
    result = validator.validate(
        tender_id="T1", ikn="1", category_code="AUS-01",
        primary_decision=decision, evidence_count=1, valid_chunk_ids=["chunk-1"]
    )
    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"
    assert "RULE_UYGUN_REQUIRES_REASON" in result.deterministic_rules_applied

def test_uygun_degil_without_reasons_rejected():
    validator = IsbakRuleValidator()
    decision = ModelDecision(
        model_name="test", decision="uygun_degil", confidence=0.9,
        birincil_profil_kodu="AUS-01", ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=[], uygunsuzluk_gerekceleri=[], # Boş
        zorunlu_kriter_sonuclari=[
            CriterionResult(criterion_id="c1", description="", status="karsilaniyor")
        ],
    )
    result = validator.validate(
        tender_id="T1", ikn="1", category_code="AUS-01",
        primary_decision=decision, evidence_count=1, valid_chunk_ids=["chunk-1"]
    )
    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"

def test_inceleme_gerekli_without_uncertainty():
    validator = IsbakRuleValidator()
    decision = ModelDecision(
        model_name="test", decision="inceleme_gerekli", confidence=0.9,
        birincil_profil_kodu="AUS-01", ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=[], uygunsuzluk_gerekceleri=[], zorunlu_kriter_sonuclari=[],
        eksik_kanitlar=[], kritik_belirsizlikler=[], insan_incelemesi_gerekcesi=""
    )
    result = validator.validate(
        tender_id="T1", ikn="1", category_code="AUS-01",
        primary_decision=decision, evidence_count=1, valid_chunk_ids=["chunk-1"]
    )
    # Passed = true, ama uyarı üretilmeli
    assert result.passed is True
    assert len(result.warnings) > 0

def test_external_info_forces_review(base_decision):
    validator = IsbakRuleValidator()
    decision = ModelDecision(
        **{**base_decision.__dict__, "kaynak_disinda_bilgi_var_mi": True}
    )
    result = validator.validate(
        tender_id="T1", ikn="1", category_code="AUS-01",
        primary_decision=decision, evidence_count=1, valid_chunk_ids=["chunk-1"]
    )
    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"

def test_empty_valid_chunk_ids_forces_review(base_decision):
    validator = IsbakRuleValidator()
    result = validator.validate(
        tender_id="T1", ikn="1", category_code="AUS-01",
        primary_decision=base_decision, evidence_count=0, valid_chunk_ids=[]
    )
    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"
