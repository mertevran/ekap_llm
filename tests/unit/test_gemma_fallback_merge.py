
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import ModelDecision, ValidationResult
from app.matching.decision_aggregator import DecisionAggregator
from app.pipeline.exceptions import TruncatedModelOutput


class MockModel:
    def __init__(self, name, returns_decision=None, returns_error=None):
        self.name = name
        self.returns_decision = returns_decision
        self.returns_error = returns_error

    def analyze(self, **kwargs):
        if self.returns_error:
            raise self.returns_error
        return self.returns_decision


class MockValidator:
    def __init__(self, passed=True, forced_decision=None):
        self.passed = passed
        self.forced_decision = forced_decision

    def validate(self, **kwargs):
        return ValidationResult(
            passed=self.passed,
            forced_decision=self.forced_decision,
            missing_required_evidence=[],
            contradictions=["MOCK CONTRADICTION"] if not self.passed else [],
            warnings=[],
        )

def make_decision(decision="uygun", confidence=0.85):
    return ModelDecision(
        model_name="mock_model",
        decision=decision,
        confidence=confidence,
        birincil_profil_kodu="AUS-01",
        ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=["uygun"] if decision == "uygun" else [],
        uygunsuzluk_gerekceleri=["degil"] if decision == "uygun_degil" else [],
        zorunlu_kriter_sonuclari=[],
        eksik_kanitlar=[],
        kritik_belirsizlikler=[],
        kullanilan_chunk_idleri=["c1"],
        kaynak_disinda_bilgi_var_mi=False,
        insan_incelemesi_gerekcesi="",
        raw_response={}
    )


def test_gemma_trigger_and_compact_fallback():
    # The compact fallback logic is actually in OllamaDecisionModel.analyze, which we mock here
    # To truly test the fallback we need to test OllamaDecisionModel directly or mock httpx.
    # We will test if the fallback logic in pipeline catches TruncatedModelOutput.
    primary = MockModel("Qwen2.5:14b", returns_decision=make_decision("inceleme_gerekli"))
    secondary = MockModel("Gemma2:9b", returns_error=TruncatedModelOutput("JSON EOF"))
    validator = MockValidator(passed=True)

    pipeline = IsbakDecisionPipeline(
        primary_model=primary,
        validator=validator,
        secondary_model=secondary,
    )
    result = pipeline.run(
        tender_id="t1", ikn="123", tender_name="Tender 1", authority_name="Auth",
        category_code="AUS-01", primary_profile_code="AUS-01", secondary_profile_codes=[],
        tender_context="ctx", company_context="ctx", evaluation_rules={"ikinci_gorus_tetikleyicileri": ["karar_inceleme_gerekli"]},
        evidence_count=1, matching_mode="profile_to_tender", retrieval_score=0.9, score_breakdown={}, valid_chunk_ids=["c1"]
    )

    assert result.secondary_triggered is True
    assert result.secondary_succeeded is False
    assert result.secondary_error_type == "truncated_output"
    assert result.final_decision == "inceleme_gerekli"
    assert result.human_review_required is True
    assert "Gemma ikinci görüş çıktısı tamamlanamadı" in result.human_review_reason


def test_gemma_trigger_and_fallback_to_qwen():
    primary = MockModel("Qwen2.5:14b", returns_decision=make_decision("uygun_degil"))
    # A generic exception
    secondary = MockModel("Gemma2:9b", returns_error=Exception("Connection Error"))
    validator = MockValidator(passed=True)

    pipeline = IsbakDecisionPipeline(
        primary_model=primary,
        validator=validator,
        secondary_model=secondary,
    )

    # Needs a trigger, say validation failure
    fail_validator = MockValidator(passed=False, forced_decision="inceleme_gerekli")
    pipeline.validator = fail_validator

    result = pipeline.run(
        tender_id="t1", ikn="123", tender_name="Tender 1", authority_name="Auth",
        category_code="AUS-01", primary_profile_code="AUS-01", secondary_profile_codes=[],
        tender_context="ctx", company_context="ctx", evaluation_rules={},
        evidence_count=1, matching_mode="profile_to_tender", retrieval_score=0.9, score_breakdown={}, valid_chunk_ids=["c1"]
    )

    assert result.secondary_triggered is True
    assert result.secondary_succeeded is False
    assert result.secondary_error_type == "secondary_model_error"
    # Even if primary said uygun_degil, the validator forced inceleme_gerekli which triggered secondary
    # If secondary fails, the active validation is still primary's validation.
    # But with new rules, a forced inceleme_gerekli from participation does not blindly override uygun_degil.
    assert result.final_decision == "uygun_degil"


def test_model_agreement_merge():
    primary = MockModel("Qwen2.5:14b", returns_decision=make_decision("uygun", 0.90))
    secondary = MockModel("Gemma2:9b", returns_decision=make_decision("uygun", 0.80))
    # Trigger secondary by giving a rule
    pipeline = IsbakDecisionPipeline(
        primary_model=primary,
        validator=MockValidator(passed=True),
        secondary_model=secondary,
    )

    result = pipeline.run(
        tender_id="t1", ikn="123", tender_name="Tender 1", authority_name="Auth",
        category_code="AUS-01", primary_profile_code="AUS-01", secondary_profile_codes=[],
        tender_context="ctx", company_context="ctx", evaluation_rules={"ikinci_gorus_tetikleyicileri": ["dusuk_guven_duzeyi"]},
        evidence_count=1, matching_mode="profile_to_tender", retrieval_score=0.9, score_breakdown={}, valid_chunk_ids=["c1"]
    )
    # primary confidence is 0.9. Default threshold is 0.85, so it shouldn't trigger actually.
    # Let's force it.
    pipeline.secondary_confidence_threshold = 0.95

    result = pipeline.run(
        tender_id="t1", ikn="123", tender_name="Tender 1", authority_name="Auth",
        category_code="AUS-01", primary_profile_code="AUS-01", secondary_profile_codes=[],
        tender_context="ctx", company_context="ctx", evaluation_rules={"ikinci_gorus_tetikleyicileri": ["dusuk_guven_duzeyi"]},
        evidence_count=1, matching_mode="profile_to_tender", retrieval_score=0.9, score_breakdown={}, valid_chunk_ids=["c1"]
    )

    assert result.secondary_triggered is True
    assert result.secondary_succeeded is True
    assert result.model_agreement is True
    assert result.final_decision == "uygun"
    assert result.merge_rule == "agreement_uygun"


def test_model_conflict_merge():
    primary = MockModel("Qwen2.5:14b", returns_decision=make_decision("uygun", 0.80))
    secondary = MockModel("Gemma2:9b", returns_decision=make_decision("uygun_degil", 0.90))

    pipeline = IsbakDecisionPipeline(
        primary_model=primary,
        validator=MockValidator(passed=True),
        secondary_model=secondary,
    )
    pipeline.secondary_confidence_threshold = 0.95
    result = pipeline.run(
        tender_id="t1", ikn="123", tender_name="Tender 1", authority_name="Auth",
        category_code="AUS-01", primary_profile_code="AUS-01", secondary_profile_codes=[],
        tender_context="ctx", company_context="ctx", evaluation_rules={"ikinci_gorus_tetikleyicileri": ["dusuk_guven_duzeyi"]},
        evidence_count=1, matching_mode="profile_to_tender", retrieval_score=0.9, score_breakdown={}, valid_chunk_ids=["c1"]
    )

    assert result.secondary_triggered is True
    assert result.model_agreement is False
    assert result.final_decision == "inceleme_gerekli"
    assert result.merge_rule == "conflict_uygun_uygun_degil"
    assert result.human_review_required is True


def test_final_report_structure():
    primary = MockModel("Qwen2.5:14b", returns_decision=make_decision("inceleme_gerekli", 0.80))
    secondary = MockModel("Gemma2:9b", returns_decision=make_decision("uygun", 0.90))
    pipeline = IsbakDecisionPipeline(
        primary_model=primary,
        validator=MockValidator(passed=True),
        secondary_model=secondary,
    )
    result = pipeline.run(
        tender_id="t1", ikn="123", tender_name="Tender 1", authority_name="Auth",
        category_code="AUS-01", primary_profile_code="AUS-01", secondary_profile_codes=[],
        tender_context="ctx", company_context="ctx", evaluation_rules={"ikinci_gorus_tetikleyicileri": ["karar_inceleme_gerekli"]},
        evidence_count=1, matching_mode="profile_to_tender", retrieval_score=0.9, score_breakdown={}, valid_chunk_ids=["c1"]
    )

    aggregator = DecisionAggregator()
    tender_final = aggregator.aggregate_from_pipeline_decisions(
        tender_id="t1", ikn="123", tender_name="T", authority_name="A",
        best_retrieval_score=0.9, pipeline_decisions=[result]
    )

    # Check the fields in tender_final
    assert tender_final.final_decision == "inceleme_gerekli"
    assert tender_final.primary_model == "mock_model"
    assert tender_final.secondary_model == "mock_model"
    assert tender_final.secondary_triggered is True
    assert tender_final.secondary_succeeded is True
    assert tender_final.merge_rule == "conflict_inceleme_uygun"
    assert tender_final.model_agreement is False
    assert tender_final.human_review_required is True
