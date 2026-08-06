from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

DecisionLabel = Literal["uygun", "uygun_degil", "inceleme_gerekli"]
CriterionStatus = Literal["karsilaniyor", "karsilanmiyor", "bilinmiyor"]
CriterionEvidenceState = Literal[
    "mandatory",
    "non_blocking",
    "not_required",
    "unverified",
]
ActivityMatch = Literal["guclu", "kismi", "zayif", "belirsiz"]
ParticipationStatus = Literal[
    "dogrulandi",
    "dogrulanmadi",
    "karsilanmiyor",
    "uygulanamaz",
]
NegativeScopeType = Literal["none", "full", "mixed", "ambiguous"]
HumanApprovalStatus = Literal["bekliyor", "gerekli_degil", "onaylandi", "reddedildi"]


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    description: str
    status: CriterionStatus
    evidence_chunk_ids: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass(frozen=True)
class SuitableTenderPart:
    """Kısmi ihalede faaliyet kapsamıyla eşleşen kaynak izli kısım."""

    part_number: str
    part_name: str
    evidence_chunk_ids: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class CriterionEvidenceAssessment:
    """Modelin bildirdiği kriterin gerçek ihale kaynağındaki durumu."""

    criterion_id: str
    description: str
    model_status: CriterionStatus
    source_status: CriterionEvidenceState
    source_available: bool = False
    evidence_chunk_ids: list[str] = field(default_factory=list)
    matched_chunk_ids: list[str] = field(default_factory=list)
    matched_phrases: list[str] = field(default_factory=list)
    source_excerpt: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ModelDecision:
    model_name: str
    decision: DecisionLabel
    confidence: float
    birincil_profil_kodu: str
    ikincil_profil_kodlari: list[str]
    uygunluk_gerekceleri: list[str]
    uygunsuzluk_gerekceleri: list[str]
    zorunlu_kriter_sonuclari: list[CriterionResult]

    # Faaliyet uygunluğu ile katılım yeterliliği birbirinden ayrılır.
    faaliyet_eslesmesi: ActivityMatch = "belirsiz"
    negatif_kapsam_cakismasi: bool = False
    katilim_yeterliligi_durumu: ParticipationStatus = "dogrulanmadi"
    kritik_faaliyet_belirsizlikleri: list[str] = field(default_factory=list)
    dogrulanamayan_katilim_sartlari: list[str] = field(default_factory=list)
    uygun_kisimlar: list[SuitableTenderPart] = field(default_factory=list)

    # Geriye dönük alanlar. Yeni kararda belirleyici olan alanlar yukarıdakilerdir.
    eksik_kanitlar: list[str] = field(default_factory=list)
    kritik_belirsizlikler: list[str] = field(default_factory=list)
    kullanilan_chunk_idleri: list[str] = field(default_factory=list)
    kaynak_disinda_bilgi_var_mi: bool = False
    insan_incelemesi_gerekcesi: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: Literal["warning", "blocking"]
    source: Literal["primary", "secondary", "pipeline"]
    related_chunk_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionValidationContext:
    """Python doğrulamasında kullanılan, model çıktısından bağımsız ihale verisi."""

    tender_name: str = ""
    tender_type: str = ""
    tender_okas_codes: list[str] = field(default_factory=list)
    evidence_text_by_chunk: dict[str, str] = field(default_factory=dict)
    profile_signals: dict[str, Any] = field(default_factory=dict)
    retrieval_score: float = 0.0
    partial_offer: bool = False
    tender_parts: list[dict[str, str]] = field(default_factory=list)
    technical_specifications: list[str] = field(default_factory=list)
    source_origin: str = "faiss"
    source_complete: bool = True
    source_missing_fields: list[str] = field(default_factory=list)
    evidence_strategy: str = "legacy"
    selected_evidence_limit: int = 0


@dataclass(frozen=True)
class NegativeScopeAnalysis:
    """Profilin negatif kapsam terimlerinin gerçek ihale kaynaklarındaki karşılığı."""

    verified: bool = False
    matched_terms: list[str] = field(default_factory=list)
    title_matched_terms: list[str] = field(default_factory=list)
    evidence_matched_terms: list[str] = field(default_factory=list)
    evidence_chunk_ids: list[str] = field(default_factory=list)
    matched_okas_codes: list[str] = field(default_factory=list)
    profile_okas_supported: bool = False
    matched_positive_terms: list[str] = field(default_factory=list)
    scope_type: NegativeScopeType = "none"


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    forced_decision: DecisionLabel | None
    issues: list[ValidationIssue] = field(default_factory=list)
    has_blocking_issue: bool = False
    human_review_required: bool = False
    verified_rejection: bool = False
    mandatory_rejection_verified: bool = False
    missing_mandatory_evidence: bool = False
    source_external_information_used: bool = False
    contradictions: list[str] = field(default_factory=list)
    missing_required_evidence: list[str] = field(default_factory=list)
    invalid_evidence_references: list[str] = field(default_factory=list)
    deterministic_rules_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    criterion_assessments: list[CriterionEvidenceAssessment] = field(
        default_factory=list
    )
    negative_scope: NegativeScopeAnalysis = field(
        default_factory=NegativeScopeAnalysis
    )


@dataclass(frozen=True)
class ConfidenceCalibration:
    """Model güveninin Python tarafından uygulanan üst sınırlarını açıklar."""

    raw_confidence: float = 0.0
    calibrated_confidence: float = 0.0
    applied_cap: float = 1.0
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FinalTenderDecision:
    tender_id: str
    ikn: str
    tender_name: str
    authority_name: str
    primary_profile_code: str
    secondary_profile_codes: list[str]
    final_decision: DecisionLabel
    final_confidence: float
    primary_model: ModelDecision
    validation: ValidationResult
    secondary_model: ModelDecision | None
    human_review_required: bool
    human_review_reason: str
    evaluated_at: str
    primary_decision: str = ""
    primary_confidence: float = 0.0
    primary_used_chunk_ids: list[str] = field(default_factory=list)
    secondary_triggered: bool = False
    secondary_trigger_reasons: list[str] = field(default_factory=list)
    secondary_succeeded: bool = False
    secondary_decision: str = ""
    secondary_confidence: float = 0.0
    secondary_used_chunk_ids: list[str] = field(default_factory=list)
    secondary_error_type: str = ""
    secondary_error_message: str = ""
    model_agreement: bool = False
    merge_rule: str = ""
    agreement_status: str = ""
    validation_issues: list[dict[str, Any]] = field(default_factory=list)
    missing_mandatory_evidence: bool = False
    mandatory_missing_evidence: list[str] = field(default_factory=list)
    optional_missing_evidence: list[str] = field(default_factory=list)
    # Üç katmanlı karar sözleşmesi:
    # - activity_decision: yalnızca faaliyet kapsamı kararı
    # - katilim_yeterliligi_durumu: gerçek ihale şartlarının durumu
    # - final_decision: Python doğrulaması sonrası nihai yönlendirme
    katilim_yeterliligi_durumu: ParticipationStatus = "dogrulanmadi"
    dogrulanamayan_katilim_sartlari: list[str] = field(default_factory=list)
    participation_review_required: bool = False
    activity_decision: DecisionLabel = "inceleme_gerekli"
    activity_match: ActivityMatch = "belirsiz"
    partial_offer: bool = False
    suitable_parts: list[SuitableTenderPart] = field(default_factory=list)
    negative_scope_verified: bool = False
    matched_negative_terms: list[str] = field(default_factory=list)
    evaluated_profile_codes: list[str] = field(default_factory=list)
    profile_match_scores: dict[str, float] = field(default_factory=dict)
    confidence_calibration: ConfidenceCalibration = field(
        default_factory=ConfidenceCalibration
    )
    validation_context: DecisionValidationContext | None = None
    # ``uygun`` bir model sonucu olsa bile hiçbir otomatik aksiyon alınmaz.
    human_approval_required: bool = False
    human_approval_status: HumanApprovalStatus = "gerekli_degil"
    automatic_action_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        """Arka uç ve ön yüz için sınırlı, insan onay kapılı sözleşme."""
        from app.decision.public_response import build_public_decision_response

        return build_public_decision_response(self).to_dict()


def combine_validation_results(primary: ValidationResult, secondary: ValidationResult | None) -> ValidationResult:
    if secondary is None:
        return primary

    issues_map: dict[tuple[Any, ...], ValidationIssue] = {}
    for issue in [*primary.issues, *secondary.issues]:
        key = (issue.code, issue.source, issue.message, tuple(sorted(issue.related_chunk_ids)))
        issues_map.setdefault(key, issue)

    issues = list(issues_map.values())
    invalid_refs = sorted(set(primary.invalid_evidence_references + secondary.invalid_evidence_references))
    external = primary.source_external_information_used or secondary.source_external_information_used
    blocking = primary.has_blocking_issue or secondary.has_blocking_issue or bool(invalid_refs) or external
    missing_mandatory = (
        primary.missing_mandatory_evidence
        or secondary.missing_mandatory_evidence
    )
    criterion_assessments_map: dict[tuple[Any, ...], CriterionEvidenceAssessment] = {}
    for assessment in [
        *primary.criterion_assessments,
        *secondary.criterion_assessments,
    ]:
        key = (
            assessment.criterion_id,
            assessment.description,
            assessment.model_status,
            assessment.source_status,
            tuple(assessment.evidence_chunk_ids),
            tuple(assessment.matched_chunk_ids),
        )
        criterion_assessments_map.setdefault(key, assessment)

    # Python yalnızca yapısal/kanıtsal güvenlik hatalarında güvenli geri dönüş uygular.
    forced_decisions = {
        result.forced_decision
        for result in (primary, secondary)
        if result.forced_decision is not None
    }
    forced: DecisionLabel | None = None
    if blocking:
        forced = (
            "uygun_degil"
            if forced_decisions == {"uygun_degil"} and not invalid_refs and not external
            else "inceleme_gerekli"
        )
    return ValidationResult(
        passed=not blocking,
        forced_decision=forced,
        issues=issues,
        has_blocking_issue=blocking,
        human_review_required=(
            forced == "inceleme_gerekli"
            or primary.human_review_required
            or secondary.human_review_required
        ),
        verified_rejection=(
            forced == "uygun_degil"
            or primary.verified_rejection
            or secondary.verified_rejection
        ),
        mandatory_rejection_verified=(
            primary.mandatory_rejection_verified
            or secondary.mandatory_rejection_verified
        ),
        missing_mandatory_evidence=missing_mandatory,
        source_external_information_used=external,
        contradictions=sorted(set(primary.contradictions + secondary.contradictions)),
        missing_required_evidence=sorted(set(primary.missing_required_evidence + secondary.missing_required_evidence)),
        invalid_evidence_references=invalid_refs,
        deterministic_rules_applied=sorted(set(primary.deterministic_rules_applied + secondary.deterministic_rules_applied)),
        warnings=sorted(set(primary.warnings + secondary.warnings)),
        criterion_assessments=list(criterion_assessments_map.values()),
        negative_scope=(
            primary.negative_scope
            if primary.negative_scope.verified
            else secondary.negative_scope
        ),
    )
