from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import CriterionResult, ModelDecision
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


def test_unverified_participation_forces_review():
    result = IsbakDeterministicValidator().validate(
        tender_id="1", ikn="2026/1", category_code="ENT-05",
        primary_decision=decision(), valid_chunk_ids=["chk_1"]
    )
    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"
    assert result.missing_mandatory_evidence is False


def test_unknown_real_mandatory_criterion_forces_review():
    criterion = CriterionResult(
        criterion_id="iso_9001",
        description="ISO 9001 belgesi",
        status="bilinmiyor",
        evidence_chunk_ids=["chk_1"],
    )
    result = IsbakDeterministicValidator().validate(
        tender_id="1",
        ikn="2026/1",
        category_code="ENT-05",
        primary_decision=decision(zorunlu_kriter_sonuclari=[criterion]),
        valid_chunk_ids=["chk_1"],
    )

    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"
    assert result.missing_mandatory_evidence is True
    assert result.human_review_required is True
    assert result.missing_required_evidence[0] == "iso_9001: ISO 9001 belgesi"
    assert "İş deneyim belgesi doğrulanamadı." in result.missing_required_evidence


def test_failed_real_mandatory_criterion_forces_rejection():
    criterion = CriterionResult(
        criterion_id="yetki_belgesi",
        description="Yetki belgesi",
        status="karsilanmiyor",
        evidence_chunk_ids=["chk_1"],
    )
    result = IsbakDeterministicValidator().validate(
        tender_id="1",
        ikn="2026/1",
        category_code="ENT-05",
        primary_decision=decision(zorunlu_kriter_sonuclari=[criterion]),
        valid_chunk_ids=["chk_1"],
    )

    assert result.passed is False
    assert result.forced_decision == "uygun_degil"
    assert result.missing_mandatory_evidence is False
    assert result.human_review_required is False


class _CriterionDecisionModel:
    name = "test-model"

    def __init__(self, criterion_status: str) -> None:
        self.criterion_status = criterion_status

    def analyze(self, **kwargs):
        del kwargs
        criterion = CriterionResult(
            criterion_id="iso_9001",
            description="ISO 9001 belgesi",
            status=self.criterion_status,
            evidence_chunk_ids=["chk_1"],
        )
        return decision(zorunlu_kriter_sonuclari=[criterion])


def _run_pipeline_for_criterion(criterion_status: str):
    pipeline = IsbakDecisionPipeline(
        primary_model=_CriterionDecisionModel(criterion_status),
        validator=IsbakDeterministicValidator(),
    )
    return pipeline.run(
        tender_id="1",
        ikn="2026/1",
        tender_name="Test ihalesi",
        authority_name="Test idaresi",
        category_code="ENT-05",
        primary_profile_code="ENT-05",
        secondary_profile_codes=[],
        tender_context="İhale bağlamı",
        company_context="Şirket bağlamı",
        evaluation_rules={},
        evidence_count=1,
        valid_chunk_ids=["chk_1"],
    )


def test_pipeline_reports_unknown_mandatory_criterion():
    result = _run_pipeline_for_criterion("bilinmiyor")

    assert result.activity_decision == "uygun"
    assert result.katilim_yeterliligi_durumu == "dogrulanmadi"
    assert result.final_decision == "inceleme_gerekli"
    assert result.merge_rule == "validation_override_blocking_issue"
    assert result.human_review_required is True
    assert result.missing_mandatory_evidence is True
    assert result.mandatory_missing_evidence == [
        "iso_9001: ISO 9001 belgesi"
    ]


def test_pipeline_rejects_failed_mandatory_criterion():
    result = _run_pipeline_for_criterion("karsilanmiyor")

    assert result.activity_decision == "uygun"
    assert result.katilim_yeterliligi_durumu == "karsilanmiyor"
    assert result.final_decision == "uygun_degil"
    assert result.merge_rule == "validation_override_mandatory_rejection"
    assert result.human_review_required is True


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
