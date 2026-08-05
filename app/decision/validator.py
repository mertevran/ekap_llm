from __future__ import annotations

from app.decision.activity_scope import analyze_negative_scope
from app.decision.models import (
    DecisionValidationContext,
    ModelDecision,
    ValidationIssue,
    ValidationResult,
)


class IsbakDeterministicValidator:
    """Model kararını gerçek kaynaklar ve karar sözleşmesiyle doğrular."""

    def validate(
        self,
        *,
        tender_id: str,
        ikn: str,
        category_code: str,
        primary_decision: ModelDecision,
        evidence_count: int = 0,
        valid_chunk_ids: list[str] | None = None,
        decision_source: str = "primary",
    ) -> ValidationResult:
        """Geriye uyumlu doğrulama girişi."""

        return self._validate(
            tender_id=tender_id,
            ikn=ikn,
            category_code=category_code,
            primary_decision=primary_decision,
            evidence_count=evidence_count,
            valid_chunk_ids=valid_chunk_ids,
            decision_source=decision_source,
            validation_context=None,
        )

    def validate_with_context(
        self,
        *,
        tender_id: str,
        ikn: str,
        category_code: str,
        primary_decision: ModelDecision,
        evidence_count: int = 0,
        valid_chunk_ids: list[str] | None = None,
        decision_source: str = "primary",
        validation_context: DecisionValidationContext,
    ) -> ValidationResult:
        """Başlık, OKAS ve kanıt metinleriyle genişletilmiş doğrulama."""

        return self._validate(
            tender_id=tender_id,
            ikn=ikn,
            category_code=category_code,
            primary_decision=primary_decision,
            evidence_count=evidence_count,
            valid_chunk_ids=valid_chunk_ids,
            decision_source=decision_source,
            validation_context=validation_context,
        )

    def _validate(
        self,
        *,
        tender_id: str,
        ikn: str,
        category_code: str,
        primary_decision: ModelDecision,
        evidence_count: int,
        valid_chunk_ids: list[str] | None,
        decision_source: str,
        validation_context: DecisionValidationContext | None,
    ) -> ValidationResult:
        del tender_id, ikn, category_code, evidence_count
        valid = set(valid_chunk_ids or [])
        source = "secondary" if decision_source == "secondary" else "primary"
        issues: list[ValidationIssue] = []
        invalid_refs: list[str] = []
        warnings: list[str] = []
        rules = ["validate_structure_and_evidence"]

        used_ids = set(primary_decision.kullanilan_chunk_idleri)
        for criterion in primary_decision.zorunlu_kriter_sonuclari:
            used_ids.update(criterion.evidence_chunk_ids)

        if valid:
            invalid_refs = sorted(chunk_id for chunk_id in used_ids if chunk_id not in valid)
            if invalid_refs:
                issues.append(
                    ValidationIssue(
                        code="invalid_evidence_reference",
                        message=(
                            "Model geçerli ihale parçaları dışında kanıt kimliği kullandı."
                        ),
                        severity="blocking",
                        source=source,
                        related_chunk_ids=invalid_refs,
                    )
                )

        if primary_decision.kaynak_disinda_bilgi_var_mi:
            issues.append(
                ValidationIssue(
                    code="external_information_used",
                    message="Model kaynak dışında bilgi kullandığını bildirdi.",
                    severity="blocking",
                    source=source,
                )
            )

        negative_scope = analyze_negative_scope(validation_context)
        if validation_context is not None:
            rules.append("verify_profile_signals_against_tender_sources")

            if primary_decision.negatif_kapsam_cakismasi and not negative_scope.verified:
                issues.append(
                    ValidationIssue(
                        code="unverified_negative_scope_claim",
                        message=(
                            "Modelin negatif kapsam beyanı ihale başlığında veya "
                            "kanıt parçalarında doğrulanamadı."
                        ),
                        severity="blocking",
                        source=source,
                    )
                )

            if negative_scope.verified and not primary_decision.negatif_kapsam_cakismasi:
                issues.append(
                    ValidationIssue(
                        code="verified_negative_scope_omitted",
                        message=(
                            "Profilin negatif kapsam terimi gerçek ihale kaynaklarında "
                            "bulundu; model bu çakışmayı bildirmedi."
                        ),
                        severity="blocking",
                        source=source,
                        related_chunk_ids=negative_scope.evidence_chunk_ids,
                    )
                )

            if (
                primary_decision.decision == "uygun"
                and negative_scope.verified
            ):
                issues.append(
                    ValidationIssue(
                        code="suitable_with_verified_negative_scope",
                        message=(
                            "Uygun kararı, ihale kaynaklarında doğrulanan negatif "
                            "profil kapsamıyla çelişiyor."
                        ),
                        severity="blocking",
                        source=source,
                        related_chunk_ids=negative_scope.evidence_chunk_ids,
                    )
                )

        # Katılım şartları faaliyet kararından ayrı raporlanır; boş profil alanı
        # faaliyet kararını uygun_degil veya inceleme_gerekli sonucuna zorlamaz.
        if primary_decision.dogrulanamayan_katilim_sartlari:
            rules.append("separate_activity_from_participation")
            warnings.append(
                "Katılım yeterliliği ayrı doğrulanmalıdır; faaliyet kararı değiştirilmedi."
            )
            issues.append(
                ValidationIssue(
                    code="participation_not_verified",
                    message=(
                        "Katılım şartlarının bir bölümü profil kaynağından doğrulanamadı."
                    ),
                    severity="warning",
                    source=source,
                )
            )

        if primary_decision.decision == "uygun" and primary_decision.negatif_kapsam_cakismasi:
            issues.append(
                ValidationIssue(
                    code="decision_reason_conflict",
                    message=(
                        "Model uygun kararı verdiği hâlde negatif kapsam çakışması bildirdi."
                    ),
                    severity="blocking",
                    source=source,
                )
            )

        if (
            primary_decision.decision == "uygun_degil"
            and primary_decision.faaliyet_eslesmesi in {"guclu", "kismi"}
            and not negative_scope.verified
            and not primary_decision.negatif_kapsam_cakismasi
        ):
            issues.append(
                ValidationIssue(
                    code="activity_participation_decision_conflict",
                    message=(
                        "Faaliyet eşleşmesi güçlü/kısmi olduğu hâlde, doğrulanmış negatif "
                        "kapsam olmadan uygun_degil kararı üretildi."
                    ),
                    severity="blocking",
                    source=source,
                )
            )

        if (
            primary_decision.decision == "inceleme_gerekli"
            and not primary_decision.kritik_faaliyet_belirsizlikleri
        ):
            warnings.append(
                "İnceleme gerekli kararı için kritik faaliyet belirsizliği açıklanmamış."
            )
            issues.append(
                ValidationIssue(
                    code="review_without_activity_uncertainty",
                    message=(
                        "İnceleme gerekli kararı kritik faaliyet belirsizliğiyle desteklenmedi."
                    ),
                    severity="warning",
                    source=source,
                )
            )

        blocking = any(issue.severity == "blocking" for issue in issues)
        verified_rejection = bool(
            primary_decision.decision == "uygun_degil"
            and primary_decision.negatif_kapsam_cakismasi
            and negative_scope.verified
            and not blocking
        )
        return ValidationResult(
            passed=not blocking,
            forced_decision="inceleme_gerekli" if blocking else None,
            issues=issues,
            has_blocking_issue=blocking,
            human_review_required=blocking,
            verified_rejection=verified_rejection,
            missing_mandatory_evidence=False,
            source_external_information_used=primary_decision.kaynak_disinda_bilgi_var_mi,
            contradictions=[
                issue.message
                for issue in issues
                if issue.code
                in {
                    "decision_reason_conflict",
                    "activity_participation_decision_conflict",
                    "suitable_with_verified_negative_scope",
                    "verified_negative_scope_omitted",
                    "unverified_negative_scope_claim",
                }
            ],
            missing_required_evidence=list(
                primary_decision.dogrulanamayan_katilim_sartlari
            ),
            invalid_evidence_references=invalid_refs,
            deterministic_rules_applied=rules,
            warnings=warnings,
            negative_scope=negative_scope,
        )


__all__ = ["IsbakDeterministicValidator"]
