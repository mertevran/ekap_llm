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

        unknown_mandatory_criteria = [
            criterion
            for criterion in primary_decision.zorunlu_kriter_sonuclari
            if criterion.status == "bilinmiyor"
        ]
        failed_mandatory_criteria = [
            criterion
            for criterion in primary_decision.zorunlu_kriter_sonuclari
            if criterion.status == "karsilanmiyor"
        ]

        def criterion_label(criterion: object) -> str:
            criterion_id = str(getattr(criterion, "criterion_id", "")).strip()
            description = str(getattr(criterion, "description", "")).strip()
            if criterion_id and description and criterion_id != description:
                return f"{criterion_id}: {description}"
            return description or criterion_id or "Tanımsız zorunlu kriter"

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

        # Yalnızca modelin ihale kaynağından çıkardığı yapılandırılmış zorunlu
        # kriterler değerlendirilir. Profildeki genel boş alanlar veya ihalenin
        # açıkça istemediği bilgiler bu kurala girmez.
        if unknown_mandatory_criteria:
            rules.append("verify_unknown_mandatory_criteria")
            for criterion in unknown_mandatory_criteria:
                issues.append(
                    ValidationIssue(
                        code="missing_mandatory_evidence",
                        message=(
                            "Gerçek zorunlu kriterin şirket tarafından karşılandığı "
                            f"doğrulanamadı: {criterion_label(criterion)}"
                        ),
                        severity="blocking",
                        source=source,
                        related_chunk_ids=list(criterion.evidence_chunk_ids),
                    )
                )

        if failed_mandatory_criteria:
            rules.append("verify_failed_mandatory_criteria")
            for criterion in failed_mandatory_criteria:
                issues.append(
                    ValidationIssue(
                        code="mandatory_criterion_not_met",
                        message=(
                            "Gerçek zorunlu kriterin karşılanmadığı bildirildi: "
                            f"{criterion_label(criterion)}"
                        ),
                        severity="blocking",
                        source=source,
                        related_chunk_ids=list(criterion.evidence_chunk_ids),
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
        safety_blocking_codes = {
            "invalid_evidence_reference",
            "external_information_used",
            "unverified_negative_scope_claim",
            "verified_negative_scope_omitted",
            "suitable_with_verified_negative_scope",
            "decision_reason_conflict",
            "activity_participation_decision_conflict",
        }
        has_safety_blocker = any(
            issue.severity == "blocking" and issue.code in safety_blocking_codes
            for issue in issues
        )
        forced_decision = None
        if blocking:
            # Kaynak/kanıt güvenliği bozulmuşsa kesin ret üretme. Açıkça
            # karşılanmayan zorunlu kriter tek bloklayıcı türüyse uygun_degil;
            # bilinmeyen veya çelişkili durumda insan incelemesi gerekir.
            if (
                failed_mandatory_criteria
                and not unknown_mandatory_criteria
                and not has_safety_blocker
            ):
                forced_decision = "uygun_degil"
            else:
                forced_decision = "inceleme_gerekli"
        verified_rejection = bool(
            forced_decision == "uygun_degil"
            or (
                primary_decision.decision == "uygun_degil"
                and primary_decision.negatif_kapsam_cakismasi
                and negative_scope.verified
            )
            and not blocking
        )
        return ValidationResult(
            passed=not blocking,
            forced_decision=forced_decision,
            issues=issues,
            has_blocking_issue=blocking,
            human_review_required=forced_decision == "inceleme_gerekli",
            verified_rejection=verified_rejection,
            missing_mandatory_evidence=bool(unknown_mandatory_criteria),
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
                dict.fromkeys(
                    [
                        *(criterion_label(item) for item in unknown_mandatory_criteria),
                        *primary_decision.dogrulanamayan_katilim_sartlari,
                    ]
                )
            ),
            invalid_evidence_references=invalid_refs,
            deterministic_rules_applied=rules,
            warnings=warnings,
            negative_scope=negative_scope,
        )


__all__ = ["IsbakDeterministicValidator"]
