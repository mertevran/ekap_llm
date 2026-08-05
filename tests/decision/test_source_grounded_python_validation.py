from __future__ import annotations

from app.decision.activity_scope import analyze_negative_scope
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import (
    CriterionResult,
    DecisionValidationContext,
    ModelDecision,
)
from app.decision.validator import IsbakDeterministicValidator


def _decision(**overrides) -> ModelDecision:
    data = {
        "model_name": "test",
        "decision": "uygun",
        "confidence": 0.82,
        "birincil_profil_kodu": "TEST-01",
        "ikincil_profil_kodlari": [],
        "uygunluk_gerekceleri": ["İhale faaliyeti profil kapsamıyla örtüşüyor."],
        "uygunsuzluk_gerekceleri": [],
        "zorunlu_kriter_sonuclari": [],
        "faaliyet_eslesmesi": "guclu",
        "negatif_kapsam_cakismasi": False,
        "katilim_yeterliligi_durumu": "dogrulanmadi",
        "kritik_faaliyet_belirsizlikleri": [],
        "dogrulanamayan_katilim_sartlari": [],
        "kullanilan_chunk_idleri": ["chk_1"],
    }
    data.update(overrides)
    return ModelDecision(**data)


def _criterion(
    *,
    criterion_id: str,
    description: str,
    status: str = "bilinmiyor",
    explanation: str = "",
) -> CriterionResult:
    return CriterionResult(
        criterion_id=criterion_id,
        description=description,
        status=status,
        evidence_chunk_ids=["chk_1"],
        explanation=explanation,
    )


def _context(
    evidence: str,
    *,
    title: str = "Test ihalesi",
    negative_terms: list[str] | None = None,
) -> DecisionValidationContext:
    return DecisionValidationContext(
        tender_name=title,
        tender_type="Hizmet Alımı",
        evidence_text_by_chunk={"chk_1": evidence},
        profile_signals={"negatif_terimler": negative_terms or []},
    )


def _validate_with_context(
    decision: ModelDecision,
    context: DecisionValidationContext,
):
    return IsbakDeterministicValidator().validate_with_context(
        tender_id="1",
        ikn="2026/1",
        category_code="TEST-01",
        primary_decision=decision,
        evidence_count=1,
        valid_chunk_ids=["chk_1"],
        validation_context=context,
    )


def test_explicitly_not_required_criterion_does_not_force_review() -> None:
    criterion = _criterion(
        criterion_id="ZOR-01",
        description="Mesleki ve teknik yeterlik kriteri",
        explanation=(
            "Mesleki ve teknik yeterliğe ilişkin bilgi, belge veya kriter "
            "belirtilmemiştir."
        ),
    )
    result = _validate_with_context(
        _decision(zorunlu_kriter_sonuclari=[criterion]),
        _context(
            "Mesleki ve teknik yeterliğe ilişkin bilgi, belge veya kriter "
            "belirtilmemiştir."
        ),
    )

    assert result.passed is True
    assert result.forced_decision is None
    assert result.missing_mandatory_evidence is False
    assert result.criterion_assessments[0].source_status == "not_required"
    assert any(
        issue.code == "criterion_explicitly_not_required"
        for issue in result.issues
    )


def test_generic_section_heading_is_not_accepted_as_mandatory() -> None:
    criterion = _criterion(
        criterion_id="4.3",
        description="Mesleki ve teknik yeterliğe ilişkin bilgi, belge veya kriterler",
    )
    result = _validate_with_context(
        _decision(zorunlu_kriter_sonuclari=[criterion]),
        _context("4.3 Mesleki ve teknik yeterliğe ilişkin bilgi, belge veya kriterler"),
    )

    assert result.passed is True
    assert result.missing_mandatory_evidence is False
    assert result.criterion_assessments[0].source_status == "unverified"
    assert any(
        issue.code == "criterion_not_proven_mandatory"
        for issue in result.issues
    )


def test_real_unknown_work_experience_requirement_still_forces_review() -> None:
    criterion = _criterion(
        criterion_id="ZOR-03",
        description="Asgari iş deneyimi",
    )
    result = _validate_with_context(
        _decision(zorunlu_kriter_sonuclari=[criterion]),
        _context(
            "İstekli tarafından teklif edilen bedelin yüzde 25'inden az olmamak "
            "üzere iş deneyimini gösteren belgelerin sunulması gerekir."
        ),
    )

    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"
    assert result.missing_mandatory_evidence is True
    assert result.criterion_assessments[0].source_status == "mandatory"


def test_negated_failed_criterion_cannot_force_rejection() -> None:
    criterion = _criterion(
        criterion_id="ZOR-03",
        description="İş deneyimi belgesi",
        status="karsilanmiyor",
    )
    result = _validate_with_context(
        _decision(zorunlu_kriter_sonuclari=[criterion]),
        _context("İş deneyimini gösteren belge istenmeyecektir."),
    )

    assert result.passed is True
    assert result.forced_decision is None
    assert result.mandatory_rejection_verified is False
    assert result.criterion_assessments[0].source_status == "not_required"


def test_negative_scope_matches_common_tender_wording_variants() -> None:
    cases = (
        (
            "OBB Güvenlik ve Kontrol Hizmet Binası Yapım İşi",
            ["yapım işi kontrollüğü"],
            "yapım işi kontrollüğü",
        ),
        (
            "42 Hat Araç ile Sürücüsüyle Araç Kiralama Hizmet Alımı",
            ["araç kiralama hizmeti"],
            "araç kiralama hizmeti",
        ),
        (
            "Trafik İşaret Levhaları, Levha Direkleri ve Malzeme Alımı",
            ["trafik işaret levhası alımı"],
            "trafik işaret levhası alımı",
        ),
    )
    for title, negative_terms, expected_term in cases:
        result = analyze_negative_scope(
            _context("İhale kapsamı başlıkta açıklanmıştır.", title=title, negative_terms=negative_terms)
        )
        assert result.verified is True
        assert expected_term in result.matched_terms


def test_negative_scope_does_not_match_on_one_generic_word() -> None:
    result = analyze_negative_scope(
        _context(
            "Mali işler birimi için raporlama yazılımı alınacaktır.",
            title="Kurumsal raporlama yazılımı",
            negative_terms=["mali danışmanlık"],
        )
    )

    assert result.verified is False


class _StaticModel:
    name = "static-model"

    def __init__(self, model_decision: ModelDecision) -> None:
        self.model_decision = model_decision

    def analyze(self, **kwargs) -> ModelDecision:
        del kwargs
        return self.model_decision


def _run_pipeline(
    model_decision: ModelDecision,
    context: DecisionValidationContext,
):
    pipeline = IsbakDecisionPipeline(
        primary_model=_StaticModel(model_decision),
        validator=IsbakDeterministicValidator(),
    )
    return pipeline.run(
        tender_id="1",
        ikn="2026/1",
        tender_name=context.tender_name,
        authority_name="Test idaresi",
        category_code="TEST-01",
        primary_profile_code="TEST-01",
        secondary_profile_codes=[],
        tender_context="İhale bağlamı",
        company_context="Şirket bağlamı",
        evaluation_rules={},
        evidence_count=1,
        valid_chunk_ids=["chk_1"],
        validation_context=context,
    )


def test_pipeline_keeps_suitable_when_source_says_criterion_not_required() -> None:
    criterion = _criterion(
        criterion_id="ZOR-01",
        description="Mesleki ve teknik yeterlik kriteri belirtilmemiştir",
    )
    result = _run_pipeline(
        _decision(
            zorunlu_kriter_sonuclari=[criterion],
            dogrulanamayan_katilim_sartlari=[
                "Mesleki ve teknik yeterlik kriterleri belirtilmemiştir."
            ],
        ),
        _context(
            "Mesleki ve teknik yeterliğe ilişkin bilgi, belge veya kriter "
            "belirtilmemiştir."
        ),
    )

    assert result.activity_decision == "uygun"
    assert result.final_decision == "uygun"
    assert result.katilim_yeterliligi_durumu == "uygulanamaz"
    assert result.participation_review_required is False
    assert result.dogrulanamayan_katilim_sartlari == []
    assert result.optional_missing_evidence


def test_activity_rejection_does_not_become_participation_rejection() -> None:
    result = _run_pipeline(
        _decision(
            decision="uygun_degil",
            faaliyet_eslesmesi="guclu",
            negatif_kapsam_cakismasi=True,
            uygunluk_gerekceleri=[],
            uygunsuzluk_gerekceleri=["Araç kiralama profilin negatif kapsamındadır."],
        ),
        _context(
            "Sürücüsüyle araç kiralama hizmet alımı yapılacaktır.",
            title="Araç Kiralama Hizmet Alımı",
            negative_terms=["araç kiralama hizmeti"],
        ),
    )

    assert result.final_decision == "uygun_degil"
    assert result.merge_rule == "validated_rejection"
    assert result.negative_scope_verified is True
    assert result.katilim_yeterliligi_durumu == "dogrulanmadi"


def test_suitable_decision_with_unsuitable_reasons_forces_review() -> None:
    result = _validate_with_context(
        _decision(
            uygunsuzluk_gerekceleri=[
                "İhale konusu sağlık hizmetidir ve profil kapsamı dışındadır."
            ]
        ),
        _context("İş yeri hekimi ve sağlık personeli hizmeti alınacaktır."),
    )

    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"
    assert any(
        issue.code == "suitable_with_unsuitable_reasons"
        for issue in result.issues
    )
