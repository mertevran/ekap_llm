from __future__ import annotations

from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import (
    DecisionValidationContext,
    ModelDecision,
    SuitableTenderPart,
)
from app.decision.validator import IsbakDeterministicValidator


def _decision(*, suitable_parts: list[SuitableTenderPart]) -> ModelDecision:
    return ModelDecision(
        model_name="test",
        decision="uygun",
        confidence=0.82,
        birincil_profil_kodu="TEST-01",
        ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=["Kamera kısmı faaliyet alanıyla örtüşüyor."],
        uygunsuzluk_gerekceleri=[],
        zorunlu_kriter_sonuclari=[],
        faaliyet_eslesmesi="kismi",
        kullanilan_chunk_idleri=["chk_1"],
        uygun_kisimlar=suitable_parts,
    )


def _context() -> DecisionValidationContext:
    return DecisionValidationContext(
        tender_name="İki kısımlı ihale",
        tender_type="Mal Alımı",
        evidence_text_by_chunk={"chk_1": "1. Kısım: Kamera sistemi"},
        partial_offer=True,
        tender_parts=[
            {
                "kisim_no": "1",
                "kisim_adi": "Kamera sistemi",
                "kaynak_tablo": "public.tenders",
                "kaynak_kayit_id": "1",
            },
            {
                "kisim_no": "2",
                "kisim_adi": "Araç kiralama",
                "kaynak_tablo": "public.tenders",
                "kaynak_kayit_id": "1",
            },
        ],
        source_origin="postgresql",
        source_complete=True,
    )


def _validate(decision: ModelDecision, context: DecisionValidationContext):
    return IsbakDeterministicValidator().validate_with_context(
        tender_id="1",
        ikn="2026/1",
        category_code="TEST-01",
        primary_decision=decision,
        evidence_count=1,
        valid_chunk_ids=["chk_1"],
        validation_context=context,
    )


def test_partial_suitable_decision_requires_suitable_parts() -> None:
    result = _validate(_decision(suitable_parts=[]), _context())

    assert result.passed is False
    assert result.forced_decision == "inceleme_gerekli"
    assert any(
        issue.code == "partial_suitable_parts_missing"
        for issue in result.issues
    )


def test_source_backed_suitable_part_is_accepted() -> None:
    part = SuitableTenderPart(
        part_number="1",
        part_name="Kamera sistemi",
        evidence_chunk_ids=["chk_1"],
        reason="Kamera sistemi profille örtüşüyor.",
    )
    result = _validate(_decision(suitable_parts=[part]), _context())

    assert result.passed is True
    assert not any(issue.code == "invalid_suitable_part" for issue in result.issues)


def test_invented_suitable_part_is_blocked() -> None:
    part = SuitableTenderPart(
        part_number="9",
        part_name="Uydurma kısım",
        evidence_chunk_ids=["chk_1"],
        reason="Uydurma gerekçe.",
    )
    result = _validate(_decision(suitable_parts=[part]), _context())

    assert result.passed is False
    assert any(issue.code == "invalid_suitable_part" for issue in result.issues)


class _StaticModel:
    name = "static"

    def __init__(self, decision: ModelDecision) -> None:
        self.decision = decision

    def analyze(self, **kwargs) -> ModelDecision:
        del kwargs
        return self.decision


def test_positive_result_requires_human_approval_and_blocks_automation() -> None:
    part = SuitableTenderPart(
        part_number="1",
        part_name="Kamera sistemi",
        evidence_chunk_ids=["chk_1"],
        reason="Kamera sistemi profille örtüşüyor.",
    )
    pipeline = IsbakDecisionPipeline(
        primary_model=_StaticModel(_decision(suitable_parts=[part])),
        validator=IsbakDeterministicValidator(),
    )
    result = pipeline.run(
        tender_id="1",
        ikn="2026/1",
        tender_name="İki kısımlı ihale",
        authority_name="Test",
        category_code="TEST-01",
        primary_profile_code="TEST-01",
        secondary_profile_codes=[],
        tender_context="Kaynak",
        company_context="Profil",
        evaluation_rules={},
        evidence_count=1,
        valid_chunk_ids=["chk_1"],
        validation_context=_context(),
    )

    assert result.final_decision == "uygun"
    assert result.activity_decision == "uygun"
    assert result.human_approval_required is True
    assert result.human_approval_status == "bekliyor"
    assert result.automatic_action_allowed is False
    public = result.to_public_dict()
    assert public["automatic_action_allowed"] is False
    assert public["uygun_kisimlar"][0]["kisim_no"] == "1"
