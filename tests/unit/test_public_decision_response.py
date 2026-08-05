from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import (
    DecisionValidationContext,
    ModelDecision,
    ValidationResult,
)


class Model:
    name = "qwen-test"

    def analyze(self, **kwargs):
        return ModelDecision(
            model_name=self.name,
            decision="uygun",
            confidence=0.84,
            birincil_profil_kodu="ENT-05",
            ikincil_profil_kodlari=[],
            uygunluk_gerekceleri=["Yazılım bakım kapsamı doğrulanmıştır."],
            uygunsuzluk_gerekceleri=[],
            zorunlu_kriter_sonuclari=[],
            faaliyet_eslesmesi="guclu",
            kullanilan_chunk_idleri=["chk_1", "chk_2", "chk_3", "chk_4"],
        )


class Validator:
    def validate_with_context(self, **kwargs):
        return ValidationResult(passed=True, forced_decision=None)


def test_public_response_is_limited_and_has_two_sentence_summary():
    pipeline = IsbakDecisionPipeline(
        primary_model=Model(),
        validator=Validator(),
    )
    context = DecisionValidationContext(
        tender_name="Yazılım bakım ihalesi",
        evidence_text_by_chunk={
            f"chk_{index}": "x" * 260
            for index in range(1, 5)
        },
    )
    result = pipeline.run(
        tender_id="1",
        ikn="2026/1",
        tender_name="Yazılım bakım ihalesi",
        authority_name="Test idaresi",
        category_code="ENT-05",
        primary_profile_code="ENT-05",
        secondary_profile_codes=[],
        tender_context="bağlam",
        company_context="profil",
        evaluation_rules={},
        evidence_count=4,
        valid_chunk_ids=list(context.evidence_text_by_chunk),
        validation_context=context,
    )
    public = result.to_public_dict()
    assert public["decision"] == "uygun"
    assert len(public["matched_evidences"]) == 3
    assert all(len(item["excerpt"]) <= 200 for item in public["matched_evidences"])
    assert public["decision_summary"].count(".") <= 2
