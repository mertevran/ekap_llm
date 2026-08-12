from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import ModelDecision, ValidationResult


class QwenModel:
    name = "qwen3.5:4b-q4_K_M"

    def __init__(self, decision: str = "uygun", confidence: float = 0.90):
        self.decision = decision
        self.confidence = confidence

    def analyze(self, **kwargs):
        return ModelDecision(
            model_name=self.name,
            decision=self.decision,
            confidence=self.confidence,
            birincil_profil_kodu="ENT-05",
            ikincil_profil_kodlari=[],
            uygunluk_gerekceleri=["Faaliyet kapsamı eşleşiyor."],
            uygunsuzluk_gerekceleri=[],
            zorunlu_kriter_sonuclari=[],
            faaliyet_eslesmesi="guclu",
            kullanilan_chunk_idleri=["chunk-1"],
        )


class PassingValidator:
    def validate(self, **kwargs):
        return ValidationResult(passed=True, forced_decision=None)


def run_single_model(decision: str = "uygun", confidence: float = 0.90):
    pipeline = IsbakDecisionPipeline(
        primary_model=QwenModel(decision=decision, confidence=confidence),
        validator=PassingValidator(),
    )
    return pipeline.run(
        tender_id="tender-1",
        ikn="2026/1",
        tender_name="Test ihalesi",
        authority_name="Test idaresi",
        category_code="ENT-05",
        primary_profile_code="ENT-05",
        secondary_profile_codes=[],
        tender_context="İhale bağlamı",
        company_context="Şirket bağlamı",
        evaluation_rules={
            "ikinci_gorus_tetikleyicileri": [
                "dusuk_guven_duzeyi",
                "karar_inceleme_gerekli",
            ]
        },
        evidence_count=1,
        valid_chunk_ids=["chunk-1"],
    )


def test_single_model_uses_qwen_and_python_validation_only():
    result = run_single_model()

    assert result.final_decision == "uygun"
    assert result.human_review_required is False
    assert result.merge_rule == "single_model"


def test_single_model_review_decision_requires_human_review():
    result = run_single_model(decision="inceleme_gerekli", confidence=0.60)

    assert result.final_decision == "inceleme_gerekli"
    assert result.final_confidence == 0.60
    assert result.human_review_required is True
