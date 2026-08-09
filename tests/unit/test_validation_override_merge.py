
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import ModelDecision, ValidationResult


class MockModel:
    def __init__(self, name, returns_decision="uygun", confidence=0.90):
        self.name = name
        self.returns_decision = returns_decision
        self.confidence = confidence

    def analyze(self, **kwargs):
        return ModelDecision(
            model_name=self.name,
            decision=self.returns_decision,
            confidence=self.confidence,
            birincil_profil_kodu="AUS-01",
            ikincil_profil_kodlari=[],
            uygunluk_gerekceleri=["x"] if self.returns_decision == "uygun" else [],
            uygunsuzluk_gerekceleri=["y"] if self.returns_decision == "uygun_degil" else [],
            zorunlu_kriter_sonuclari=[],
            eksik_kanitlar=[],
            kritik_belirsizlikler=[],
            kullanilan_chunk_idleri=["c1"],
            kaynak_disinda_bilgi_var_mi=False,
            insan_incelemesi_gerekcesi="",
            raw_response={}
        )

class MockValidator:
    def __init__(self, passed=True, forced_decision=None, issues=None, missing_mandatory=False, blocking=False, external_info=False, verified_rejection=False):
        self.passed = passed
        self.forced_decision = forced_decision
        self.issues = issues or []
        self.missing_mandatory = missing_mandatory
        self.blocking = blocking
        self.external_info = external_info
        self.verified_rejection = verified_rejection

    def validate(self, **kwargs):
        return ValidationResult(
            passed=self.passed,
            forced_decision=self.forced_decision,
            issues=self.issues,
            has_blocking_issue=self.blocking,
            human_review_required=self.blocking or self.missing_mandatory or self.external_info,
            verified_rejection=self.verified_rejection,
            missing_mandatory_evidence=self.missing_mandatory,
            source_external_information_used=self.external_info
        )

def run_pipeline(validator, primary_dec="uygun", secondary_dec="uygun"):
    primary = MockModel("Qwen", returns_decision=primary_dec, confidence=0.5)
    secondary = MockModel("Gemma", returns_decision=secondary_dec, confidence=0.9)

    pipeline = IsbakDecisionPipeline(
        primary_model=primary,
        validator=validator,
        secondary_model=secondary,
    )
    # Give a trigger so secondary runs
    return pipeline.run(
        tender_id="t1", ikn="123", tender_name="Tender 1", authority_name="Auth",
        category_code="AUS-01", primary_profile_code="AUS-01", secondary_profile_codes=[],
        tender_context="ctx", company_context="ctx", evaluation_rules={"ikinci_gorus_tetikleyicileri": ["dusuk_guven_duzeyi"]},
        evidence_count=1, matching_mode="profile_to_tender", retrieval_score=0.9, score_breakdown={}, valid_chunk_ids=["c1"]
    )

def test_primary_secondary_uygun_no_blocking_issue():
    val = MockValidator(passed=True)
    res = run_pipeline(val, "uygun", "uygun")
    assert res.final_decision == "uygun"
    assert res.merge_rule == "agreement_uygun"

def test_primary_secondary_uygun_missing_mandatory():
    val = MockValidator(passed=False, forced_decision="inceleme_gerekli", missing_mandatory=True)
    res = run_pipeline(val, "uygun", "uygun")
    assert res.final_decision == "uygun"
    assert res.merge_rule == "validation_override_missing_evidence"
    assert res.human_review_required is False

def test_primary_secondary_uygun_blocking_issue():
    val = MockValidator(passed=False, forced_decision="inceleme_gerekli", blocking=True)
    res = run_pipeline(val, "uygun", "uygun")
    assert res.final_decision == "inceleme_gerekli"
    assert res.merge_rule == "validation_override_blocking_issue"
    assert res.human_review_required is True

def test_primary_secondary_uygun_external_info():
    val = MockValidator(passed=False, forced_decision="inceleme_gerekli", external_info=True)
    res = run_pipeline(val, "uygun", "uygun")
    assert res.final_decision == "inceleme_gerekli"
    assert res.merge_rule == "validation_override_external_information"
    assert res.human_review_required is True

def test_verified_rejection():
    val = MockValidator(passed=True, verified_rejection=True)
    res = run_pipeline(val, "uygun_degil", "uygun_degil")
    assert res.final_decision == "uygun_degil"
    assert res.merge_rule == "validated_rejection"

def test_model_disagreement():
    val = MockValidator(passed=True)
    res = run_pipeline(val, "uygun", "uygun_degil")
    assert res.final_decision == "inceleme_gerekli"
    assert res.merge_rule == "conflict_uygun_uygun_degil"
    assert res.human_review_required is True
