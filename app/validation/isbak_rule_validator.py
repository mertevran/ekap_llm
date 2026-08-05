"""İSBAK Python tabanlı kural doğrulayıcısı.

Yalnızca standart karar değerleri kabul edilir:
    uygun, uygun_degil, inceleme_gerekli

Eski değerler (doğrudan_uygun, ilgisiz) bu doğrulayıcıya ulaşmadan
önce decision_normalizer üzerinden dönüştürülmüş olmalıdır.
"""

from __future__ import annotations

from typing import Literal

from app.decision.decision_normalizer import normalize_decision
from app.decision.models import ModelDecision, ValidationIssue, ValidationResult

VALID_DECISIONS = frozenset({"uygun", "uygun_degil", "inceleme_gerekli"})


class IsbakRuleValidator:
    """Python tabanlı iş kuralı doğrulayıcısı.

    LLM'nin verdiği kararı katı iş kurallarına göre denetler.
    Yapısal hatalar (JSON parse, uydurma chunk_id) modelin kendisinde veya 
    diğer validator'da yakalanır. Burası sadece iş kurallarına bakar.
    """

    def validate(
        self,
        *,
        tender_id: str,
        ikn: str,
        category_code: str,
        primary_decision: ModelDecision,
        evidence_count: int,
        critical_context_omitted: bool = False,
        valid_chunk_ids: list[str] | None = None,
        decision_source: Literal["primary", "secondary", "pipeline"] = "primary",
    ) -> ValidationResult:

        passed = True
        forced_decision = None
        issues: list[ValidationIssue] = []
        has_blocking_issue = False
        human_review_required = False
        verified_rejection = False
        missing_mandatory_evidence = False
        source_external_information_used = False

        contradictions: list[str] = []
        missing_required_evidence: list[str] = []
        invalid_evidence_references: list[str] = []
        deterministic_rules_applied: list[str] = []
        warnings: list[str] = []

        try:
            normalized_decision = normalize_decision(primary_decision.decision)
        except ValueError:
            normalized_decision = None
            passed = False
            has_blocking_issue = True
            human_review_required = True
            deterministic_rules_applied.append("RULE_UNKNOWN_DECISION")
            issues.append(ValidationIssue(
                code="invalid_decision_value",
                message=f"Bilinmeyen karar değeri: {primary_decision.decision!r}",
                severity="blocking",
                source=decision_source,
            ))

        if normalized_decision not in VALID_DECISIONS and normalized_decision is not None:
            passed = False
            has_blocking_issue = True
            human_review_required = True
            deterministic_rules_applied.append("RULE_INVALID_DECISION")
            issues.append(ValidationIssue(
                code="invalid_decision_value",
                message=f"Bilinmeyen karar değeri: {normalized_decision}",
                severity="blocking",
                source=decision_source,
            ))

        # 1. Kaynak dışında bilgi kullanımı
        if primary_decision.kaynak_disinda_bilgi_var_mi:
            source_external_information_used = True
            has_blocking_issue = True
            human_review_required = True
            if normalized_decision != "inceleme_gerekli":
                forced_decision = "inceleme_gerekli"
            deterministic_rules_applied.append("RULE_EXTERNAL_INFO_FORCES_REVIEW")
            issues.append(ValidationIssue(
                code="external_information_used",
                message="Model kaynak dışı bilgi kullandı, karar 'inceleme_gerekli' olmalıdır.",
                severity="blocking",
                source=decision_source,
            ))

        # 2. Geçerli chunk_id boşluğu
        if valid_chunk_ids is not None and len(valid_chunk_ids) == 0:
            if normalized_decision in ["uygun", "uygun_degil"]:
                forced_decision = "inceleme_gerekli"
                has_blocking_issue = True
                human_review_required = True
                deterministic_rules_applied.append("RULE_EMPTY_EVIDENCE_FORCES_REVIEW")
                issues.append(ValidationIssue(
                    code="missing_evidence",
                    message="Hiç geçerli kanıt yokken kesin karar (uygun/uygun_degil) verilemez.",
                    severity="blocking",
                    source=decision_source,
                ))

        # 3. Gerekçesiz kararlar
        if normalized_decision == "uygun" and not primary_decision.uygunluk_gerekceleri:
            forced_decision = "inceleme_gerekli"
            has_blocking_issue = True
            human_review_required = True
            deterministic_rules_applied.append("RULE_UYGUN_REQUIRES_REASON")
            issues.append(ValidationIssue(
                code="missing_reason",
                message="Uygun kararı verilmiş ancak hiçbir uygunluk gerekçesi sunulmamış.",
                severity="blocking",
                source=decision_source,
            ))

        has_failed_criteria = any(c.status == "karsilanmiyor" for c in primary_decision.zorunlu_kriter_sonuclari)
        if normalized_decision == "uygun_degil" and not primary_decision.uygunsuzluk_gerekceleri and not has_failed_criteria:
            forced_decision = "inceleme_gerekli"
            has_blocking_issue = True
            human_review_required = True
            deterministic_rules_applied.append("RULE_UYGUNDEGIL_REQUIRES_REASON")
            issues.append(ValidationIssue(
                code="missing_reason",
                message="Uygun değil kararı verilmiş ancak ne gerekçe var ne de karşılanmayan kriter.",
                severity="blocking",
                source=decision_source,
            ))

        if normalized_decision == "uygun_degil" and (primary_decision.uygunsuzluk_gerekceleri or has_failed_criteria):
            verified_rejection = True

        if normalized_decision == "inceleme_gerekli":
            has_uncertainty = (
                primary_decision.eksik_kanitlar or 
                primary_decision.kritik_belirsizlikler or 
                primary_decision.insan_incelemesi_gerekcesi
            )
            if not has_uncertainty:
                warnings.append("İnceleme gerekli kararı verilmiş ancak belirsizlik detaylandırılmamış.")
                issues.append(ValidationIssue(
                    code="missing_review_reason",
                    message="İnceleme gerekli kararı verilmiş ancak belirsizlik detaylandırılmamış.",
                    severity="warning",
                    source=decision_source,
                ))

        # 4. Kriter çelişkileri
        if normalized_decision == "uygun" and has_failed_criteria:
            forced_decision = "uygun_degil"
            has_blocking_issue = True
            human_review_required = True
            deterministic_rules_applied.append("RULE_FAILED_CRITERIA_FORCES_UYGUNDEGIL")
            issues.append(ValidationIssue(
                code="decision_criteria_conflict",
                message="Karşılanmayan zorunlu kriter varken 'uygun' kararı verilemez.",
                severity="blocking",
                source=decision_source,
            ))

        has_unknown_criteria = any(c.status == "bilinmiyor" for c in primary_decision.zorunlu_kriter_sonuclari)
        if normalized_decision == "uygun" and has_unknown_criteria:
            forced_decision = "inceleme_gerekli"
            has_blocking_issue = True
            human_review_required = True
            deterministic_rules_applied.append("RULE_UNKNOWN_CRITERIA_FORCES_REVIEW")
            issues.append(ValidationIssue(
                code="decision_criteria_conflict",
                message="Bilinmeyen zorunlu kriter varken 'uygun' kararı verilemez.",
                severity="blocking",
                source=decision_source,
            ))

        if critical_context_omitted and normalized_decision == "uygun":
            forced_decision = "inceleme_gerekli"
            has_blocking_issue = True
            human_review_required = True
            deterministic_rules_applied.append("RULE_CRITICAL_CONTEXT_OMITTED")
            issues.append(ValidationIssue(
                code="critical_context_omitted",
                message="Kritik ihale şartları bağlam bütçesini aştığı için analiz dışı bırakıldı.",
                severity="blocking",
                source=decision_source,
            ))
            passed = False

        # 5. Eksik kanıt ayrımı (Mandatory vs Optional)
        mandatory_keywords = [
            "ts en", "iso", "sertifika", "belge", "iş deneyim", "is deneyim", 
            "performans analiz", "kapasite", "numune", "personel", "ekipman",
            "tescil", "marka", "yetki"
        ]
        for kanit in primary_decision.eksik_kanitlar:
            lower_kanit = kanit.lower()
            if any(k in lower_kanit for k in mandatory_keywords):
                missing_mandatory_evidence = True
                has_blocking_issue = True
                human_review_required = True
                if normalized_decision == "uygun":
                    forced_decision = "inceleme_gerekli"
                issues.append(ValidationIssue(
                    code="missing_mandatory_evidence",
                    message=f"Zorunlu yeterlilik veya sertifika/belge kanıtı eksik: {kanit}",
                    severity="blocking",
                    source=decision_source,
                ))

        # 6. Desteklenmeyen iddialar (Somut olmayan pozitif gerekçeler)
        unsupported_keywords = [
            "şirketin sahip olduğu iş deneyimi",
            "sirketin sahip oldugu is deneyimi",
            "herhangi bir negatif kriter bulunmamaktadır",
            "şirket bu belgeye sahiptir",
            "sirket bu belgeye sahiptir",
            "zorunlu koşullar karşılanmaktadır",
            "zorunlu kosullar karsilanmaktadir"
        ]
        for gerekce in primary_decision.uygunluk_gerekceleri:
            lower_gerekce = gerekce.lower()
            for k in unsupported_keywords:
                if k in lower_gerekce:
                    has_blocking_issue = True
                    human_review_required = True
                    if normalized_decision == "uygun":
                        forced_decision = "inceleme_gerekli"
                    issues.append(ValidationIssue(
                        code="unsupported_positive_claim",
                        message=f"Model somut kanıt olmaksızın kesin bir yargıda bulundu: '{k}'",
                        severity="blocking",
                        source=decision_source,
                    ))

            # Profil faaliyetinin iş deneyimi sayılması hatası
            if "iş deneyim" in lower_gerekce and ("faaliyet" in lower_gerekce or "yetkinlik" in lower_gerekce):
                has_blocking_issue = True
                human_review_required = True
                if normalized_decision == "uygun":
                    forced_decision = "inceleme_gerekli"
                issues.append(ValidationIssue(
                    code="capability_not_proven_as_experience",
                    message="Profilde faaliyet veya teknik yetkinlik bulunması, tamamlanmış iş deneyimi belgesi anlamına gelmez.",
                    severity="blocking",
                    source=decision_source,
                ))

        if forced_decision is not None and forced_decision != normalized_decision:
            passed = False
            has_blocking_issue = True

        return ValidationResult(
            passed=passed,
            forced_decision=forced_decision,
            issues=issues,
            has_blocking_issue=has_blocking_issue,
            human_review_required=human_review_required,
            verified_rejection=verified_rejection,
            missing_mandatory_evidence=missing_mandatory_evidence,
            source_external_information_used=source_external_information_used,
            contradictions=contradictions,
            missing_required_evidence=missing_required_evidence,
            invalid_evidence_references=invalid_evidence_references,
            deterministic_rules_applied=deterministic_rules_applied,
            warnings=warnings,
        )
