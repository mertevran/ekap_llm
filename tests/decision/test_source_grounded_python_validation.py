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
    positive_terms: list[str] | None = None,
) -> DecisionValidationContext:
    return DecisionValidationContext(
        tender_name=title,
        tender_type="Hizmet Alımı",
        evidence_text_by_chunk={"chk_1": evidence},
        profile_signals={
            "negatif_terimler": negative_terms or [],
            "guclu_terimler": positive_terms or [],
        },
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
            "belirtilmemiştir.",
            positive_terms=["Test ihalesi"],
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
        _context(
            "4.3 Mesleki ve teknik yeterliğe ilişkin bilgi, belge veya kriterler",
            positive_terms=["Test ihalesi"],
        ),
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
        _context(
            "İş deneyimini gösteren belge istenmeyecektir.",
            positive_terms=["Test ihalesi"],
        ),
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
            positive_terms=["raporlama yazılımı"],
        )
    )

    assert result.verified is False


def test_negative_scope_recognizes_semantic_and_morphological_variants() -> None:
    result = analyze_negative_scope(
        _context(
            "Ambulans klimalarının periyodik bakımı ve onarımı yapılacaktır.",
            title="Ambulans Klima Bakım Hizmeti",
            negative_terms=["araç klima bakımı"],
        )
    )

    assert result.verified is True
    assert result.scope_type == "full"
    assert result.matched_terms == ["araç klima bakımı"]


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
            "belirtilmemiştir.",
            positive_terms=["Test ihalesi"],
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


def test_proven_out_of_scope_student_transport() -> None:
    # Test 1 — Öğrenci taşıma (AUS-04)
    result = analyze_negative_scope(
        _context(
            "973 öğrencinin 67 araç ile okullara güvenli taşınması işi",
            title="973 Öğrencinin 67 Araç ile 22 Taşıma Merkezi Okula Taşınması",
            positive_terms=["toplu taşıma", "raylı sistem"],
        )
    )
    assert result.verified is True
    assert result.out_of_scope_verified is True
    assert result.negative_verification_method == "proven_out_of_scope"


def test_proven_out_of_scope_patient_transport() -> None:
    # Test 2 — Hasta taşıma
    result = analyze_negative_scope(
        _context(
            "hastaların hastanelere taşınması için hasta taşıma aracı",
            title="Hasta Taşıma Aracı Hizmet Alımı",
            positive_terms=["sunucu", "veri depolama"],
        )
    )
    assert result.verified is True
    assert result.out_of_scope_verified is True
    assert result.negative_verification_method == "proven_out_of_scope"
    assert any("hasta" in reason and "taşıma" in reason for reason in result.out_of_scope_reasons)


def test_proven_out_of_scope_food_service() -> None:
    # Test 3 — Yemek hizmeti
    result = analyze_negative_scope(
        _context(
            "personel için yemek hizmeti hazırlanması ve dağıtılması",
            title="24 Aylık Yemek ve Yemek Hizmet Alımı",
            positive_terms=["kamera", "yazılım"],
        )
    )
    assert result.verified is True
    assert result.out_of_scope_verified is True
    assert result.negative_verification_method == "proven_out_of_scope"


def test_proven_out_of_scope_fuel_oil() -> None:
    # Test 4 — Fuel-Oil
    result = analyze_negative_scope(
        _context(
            "tesisler için 400 ton fuel-oil tedariki",
            title="400 Ton Fuel-Oil No 5 satın alınması",
            positive_terms=["haberleşme"],
        )
    )
    assert result.verified is True
    assert result.out_of_scope_verified is True


def test_real_positive_tech_tender_is_not_rejected() -> None:
    # Test 5 — Gerçek pozitif teknoloji ihalesi
    result = analyze_negative_scope(
        _context(
            "veri depolama sistemleri ve sanallaştırma yazılımı alınacaktır.",
            title="Veri Depolama Sistemleri ve Sanallaştırma Yazılımı Lisansı",
            positive_terms=["veri depolama", "sanallaştırma"],
        )
    )
    assert result.out_of_scope_verified is False
    assert result.verified is False


def test_ambiguous_generic_maintenance_stays_review() -> None:
    # Test 6 — Belirsiz genel bakım
    result = analyze_negative_scope(
        _context(
            "sistemlerin periyodik bakımı ve onarımı",
            title="Bakım ve Onarım Hizmeti",
            positive_terms=["yazılım geliştirme"],
        )
    )
    assert result.verified is False
    assert result.out_of_scope_verified is False


def test_public_transport_tech_is_not_rejected() -> None:
    # Test 7 — Toplu taşıma teknolojisi yanlış reddedilmemeli
    result = analyze_negative_scope(
        _context(
            "toplu taşıma araç takip ve yolcu bilgilendirme sistemi",
            title="Toplu Taşıma Araç Takip ve Yolcu Bilgilendirme Sistemi",
            positive_terms=["toplu taşıma", "araç takip"],
        )
    )
    assert result.verified is False
    assert result.out_of_scope_verified is False


def test_existing_negative_term_behavior_kept() -> None:
    # Test 8 — Mevcut negatif terim davranışı korunmalı
    result = analyze_negative_scope(
        _context(
            "sürücüsüz araç kiralama hizmeti alınacaktır",
            title="Araç Kiralama Hizmet Alımı",
            positive_terms=["yazılım"],
            negative_terms=["araç kiralama hizmeti"],
        )
    )
    assert result.verified is True
    assert result.negative_verification_method == "profile_negative_term"


def test_qwen_rejection_without_evidence_stays_review() -> None:
    # Test 9 — Qwen tek başına ret veremez
    decision = _decision(
        decision="uygun_degil",
        faaliyet_eslesmesi="zayif",
        negatif_kapsam_cakismasi=True,
        uygunsuzluk_gerekceleri=["Konu bizim dışımızda"]
    )
    context = _context(
        "farklı bir ürün tedarik edilecek, detaylar ektedir.",
        title="Özel Sensör Alımı",
        positive_terms=["yazılım"],
    )
    pipeline = IsbakDecisionPipeline(
        primary_model=_StaticModel(decision),
        validator=IsbakDeterministicValidator(),
    )
    result = pipeline.run(
        tender_id="1", ikn="1", tender_name=context.tender_name, authority_name="A",
        category_code="T", primary_profile_code="T", secondary_profile_codes=[],
        tender_context="", company_context="", evaluation_rules={},
        evidence_count=1, valid_chunk_ids=["chk_1"], validation_context=context
    )
    assert result.final_decision == "inceleme_gerekli"
    assert result.negative_scope_verified is False
