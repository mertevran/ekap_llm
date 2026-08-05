from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import ModelDecision, ValidationResult


class MockDecisionModel:
    def __init__(self, name: str):
        self.name = name
        self.last_args = {}

    def analyze(self, **kwargs) -> ModelDecision:
        self.last_args = kwargs
        return ModelDecision(
            model_name=self.name,
            decision="uygun",
            confidence=0.9,
            birincil_profil_kodu=kwargs.get("primary_profile_code", ""),
            ikincil_profil_kodlari=[],
            uygunluk_gerekceleri=["test"],
            uygunsuzluk_gerekceleri=[],
            zorunlu_kriter_sonuclari=[],
        )

class MockValidator:
    def validate(self, **kwargs) -> ValidationResult:
        return ValidationResult(
            passed=True,
            forced_decision=None,
        )

def test_pipeline_args_passing():
    primary = MockDecisionModel("primary")
    validator = MockValidator()
    pipeline = IsbakDecisionPipeline(primary_model=primary, validator=validator)

    pipeline.run(
        tender_id="t1",
        ikn="123",
        tender_name="test",
        authority_name="auth",
        category_code="PROF-1",
        primary_profile_code="PROF-1",
        secondary_profile_codes=[],
        tender_context="ctx1",
        company_context="ctx2",
        evaluation_rules={},
        evidence_count=1,
        matching_mode="tender_to_profile",
        retrieval_score=0.8,
        score_breakdown={"max_similarity": 0.8},
        valid_chunk_ids=["chunk1"],
    )

    assert primary.last_args["category_code"] == "PROF-1"
    assert primary.last_args["primary_profile_code"] == "PROF-1"
    assert primary.last_args["matching_mode"] == "tender_to_profile"
    assert primary.last_args["retrieval_score"] == 0.8
    assert primary.last_args["score_breakdown"] == {"max_similarity": 0.8}
    assert primary.last_args["valid_chunk_ids"] == ["chunk1"]
