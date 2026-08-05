from app.decision.models import ModelDecision
from app.decision.validator import IsbakDeterministicValidator


def decision(**overrides):
    data = dict(
        model_name="test",
        decision="uygun",
        confidence=0.8,
        birincil_profil_kodu="ENT-05",
        ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=["Akıllı aydınlatma kapsamı örtüşüyor."],
        uygunsuzluk_gerekceleri=[],
        zorunlu_kriter_sonuclari=[],
        faaliyet_eslesmesi="guclu",
        negatif_kapsam_cakismasi=False,
        katilim_yeterliligi_durumu="dogrulanmadi",
        kritik_faaliyet_belirsizlikleri=[],
        dogrulanamayan_katilim_sartlari=["İş deneyim belgesi doğrulanamadı."],
        kullanilan_chunk_idleri=["chk_1"],
    )
    data.update(overrides)
    return ModelDecision(**data)


def test_unverified_participation_does_not_force_review():
    result = IsbakDeterministicValidator().validate(
        tender_id="1", ikn="2026/1", category_code="ENT-05",
        primary_decision=decision(), valid_chunk_ids=["chk_1"]
    )
    assert result.passed is True
    assert result.forced_decision is None
    assert result.missing_mandatory_evidence is False


def test_invalid_chunk_id_is_blocking():
    result = IsbakDeterministicValidator().validate(
        tender_id="1", ikn="2026/1", category_code="ENT-05",
        primary_decision=decision(kullanilan_chunk_idleri=["uydurma"]),
        valid_chunk_ids=["chk_1"]
    )
    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"


def test_activity_conflict_is_blocking_but_python_does_not_choose_unsuitable():
    result = IsbakDeterministicValidator().validate(
        tender_id="1", ikn="2026/1", category_code="ENT-05",
        primary_decision=decision(negatif_kapsam_cakismasi=True),
        valid_chunk_ids=["chk_1"]
    )
    assert result.forced_decision == "inceleme_gerekli"
    assert result.verified_rejection is False
