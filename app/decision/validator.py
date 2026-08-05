from __future__ import annotations

from app.decision.activity_scope import analyze_negative_scope
from app.decision.criterion_evidence import assess_criterion_evidence
from app.decision.models import (
    CriterionEvidenceAssessment,
    CriterionResult,
    DecisionValidationContext,
    ModelDecision,
    ValidationIssue,
    ValidationResult,
)

_VALID_DECISIONS = frozenset({"uygun", "uygun_degil", "inceleme_gerekli"})


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
        """Geriye uyumlu doğrulama girişi.

        Kaynak metni verilmediğinde modelin yapılandırılmış kriterleri eski
        davranış korunarak gerçek kriter kabul edilir. Canlı karar zinciri
        ``validate_with_context`` yolunu kullanır ve kriterleri kaynakta doğrular.
        """

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
        """Başlık, ihale türü, OKAS ve kanıt metinleriyle doğrulama yapar."""

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
        del tender_id, ikn, category_code
        valid_ids_were_provided = valid_chunk_ids is not None
        valid = set(valid_chunk_ids or [])
        source = "secondary" if decision_source == "secondary" else "primary"
        issues: list[ValidationIssue] = []
        invalid_refs: list[str] = []
        warnings: list[str] = []
        rules = ["validate_structure_and_evidence"]

        decision = str(primary_decision.decision).strip()
        if decision not in _VALID_DECISIONS:
            issues.append(
                ValidationIssue(
                    code="invalid_decision_value",
                    message=f"Model geçersiz karar değeri üretti: {decision!r}",
                    severity="blocking",
                    source=source,
                )
            )

        criteria = list(primary_decision.zorunlu_kriter_sonuclari)
        criterion_assessments = self._assess_criteria(
            criteria,
            validation_context=validation_context,
        )
        mandatory_pairs = [
            (criterion, assessment)
            for criterion, assessment in zip(
                criteria,
                criterion_assessments,
                strict=True,
            )
            if assessment.source_status == "mandatory"
        ]
        unknown_mandatory_criteria = [
            criterion
            for criterion, _assessment in mandatory_pairs
            if criterion.status == "bilinmiyor"
        ]
        failed_mandatory_criteria = [
            criterion
            for criterion, _assessment in mandatory_pairs
            if criterion.status == "karsilanmiyor"
        ]

        used_ids = set(primary_decision.kullanilan_chunk_idleri)
        for criterion in criteria:
            used_ids.update(criterion.evidence_chunk_ids)

        if valid_ids_were_provided:
            invalid_refs = sorted(
                chunk_id
                for chunk_id in used_ids
                if chunk_id not in valid
            )
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

        if (
            decision in {"uygun", "uygun_degil"}
            and (
                (valid_ids_were_provided and not valid)
                or (not valid_ids_were_provided and evidence_count <= 0)
            )
        ):
            issues.append(
                ValidationIssue(
                    code="missing_evidence",
                    message="Geçerli ihale kanıtı olmadan kesin karar verilemez.",
                    severity="blocking",
                    source=source,
                )
            )

        if decision in {"uygun", "uygun_degil"} and not used_ids:
            issues.append(
                ValidationIssue(
                    code="decision_without_evidence_reference",
                    message="Kesin karar hiçbir ihale parçasına bağlanmamış.",
                    severity="blocking",
                    source=source,
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

        if (
            validation_context is None
            and decision == "uygun"
            and primary_decision.eksik_kanitlar
        ):
            issues.append(
                ValidationIssue(
                    code="legacy_missing_evidence",
                    message=(
                        "Kaynak bağlamı olmayan eski akışta model eksik kanıt "
                        "bildirdi; kesin uygun kararı doğrulanamadı."
                    ),
                    severity="blocking",
                    source=source,
                )
            )

        self._append_criterion_source_issues(
            criterion_assessments,
            invalid_refs=invalid_refs,
            source=source,
            issues=issues,
            warnings=warnings,
        )

        if unknown_mandatory_criteria:
            rules.append("verify_unknown_mandatory_criteria")
            for criterion in unknown_mandatory_criteria:
                issues.append(
                    ValidationIssue(
                        code="missing_mandatory_evidence",
                        message=(
                            "Gerçek zorunlu kriterin şirket tarafından karşılandığı "
                            f"doğrulanamadı: {self._criterion_label(criterion)}"
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
                            f"{self._criterion_label(criterion)}"
                        ),
                        severity="blocking",
                        source=source,
                        related_chunk_ids=list(criterion.evidence_chunk_ids),
                    )
                )

        if decision == "uygun" and not primary_decision.uygunluk_gerekceleri:
            issues.append(
                ValidationIssue(
                    code="missing_suitability_reason",
                    message="Uygun kararı için gerekçe üretilmedi.",
                    severity="blocking",
                    source=source,
                )
            )
        if (
            decision == "uygun_degil"
            and not primary_decision.uygunsuzluk_gerekceleri
            and not failed_mandatory_criteria
        ):
            issues.append(
                ValidationIssue(
                    code="missing_unsuitability_reason",
                    message="Uygun değil kararı için doğrulanabilir gerekçe üretilmedi.",
                    severity="blocking",
                    source=source,
                )
            )
        if decision == "uygun" and primary_decision.uygunsuzluk_gerekceleri:
            issues.append(
                ValidationIssue(
                    code="suitable_with_unsuitable_reasons",
                    message=(
                        "Model uygun kararı verdiği hâlde uygunsuzluk gerekçeleri "
                        "de bildirdi."
                    ),
                    severity="blocking",
                    source=source,
                )
            )

        negative_scope = analyze_negative_scope(validation_context)
        if validation_context is not None:
            rules.append("verify_profile_signals_against_tender_sources")

            if negative_scope.scope_type == "full":
                rules.append("reject_full_negative_scope")
                issues.append(
                    ValidationIssue(
                        code="full_negative_scope_verified",
                        message=(
                            "Negatif kapsam ihale başlığında açıkça doğrulandı ve "
                            "karşıt olumlu faaliyet sinyali bulunmadı."
                        ),
                        severity="blocking",
                        source=source,
                        related_chunk_ids=negative_scope.evidence_chunk_ids,
                    )
                )
            elif negative_scope.scope_type == "mixed":
                rules.append("review_mixed_activity_scope")
                issues.append(
                    ValidationIssue(
                        code="mixed_activity_scope_verified",
                        message=(
                            "İhale kaynaklarında olumlu ve negatif profil kapsamları "
                            "birlikte bulundu."
                        ),
                        severity="blocking",
                        source=source,
                        related_chunk_ids=negative_scope.evidence_chunk_ids,
                    )
                )

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

            if decision == "uygun" and negative_scope.verified:
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

        # Profildeki genel boşluklar faaliyet kararını değiştirmez. Yalnızca
        # kaynakta doğrulanmış gerçek zorunlu kriterler yukarıdaki kapıya girer.
        if primary_decision.dogrulanamayan_katilim_sartlari:
            rules.append("separate_activity_from_participation")
            warnings.append(
                "Model katılım boşluğu bildirdi; kaynakta doğrulanmayan kayıtlar "
                "faaliyet kararını değiştirmedi."
            )
            issues.append(
                ValidationIssue(
                    code="participation_gap_reported_by_model",
                    message=(
                        "Model katılım şartlarının bir bölümünü profil kaynağından "
                        "doğrulayamadığını bildirdi."
                    ),
                    severity="warning",
                    source=source,
                )
            )

        if decision == "uygun" and primary_decision.negatif_kapsam_cakismasi:
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
            decision == "uygun_degil"
            and primary_decision.faaliyet_eslesmesi in {"guclu", "kismi"}
            and not negative_scope.verified
            and not primary_decision.negatif_kapsam_cakismasi
            and not failed_mandatory_criteria
        ):
            issues.append(
                ValidationIssue(
                    code="activity_participation_decision_conflict",
                    message=(
                        "Faaliyet eşleşmesi güçlü/kısmi olduğu hâlde, doğrulanmış negatif "
                        "kapsam veya karşılanmayan gerçek kriter olmadan uygun_degil "
                        "kararı üretildi."
                    ),
                    severity="blocking",
                    source=source,
                )
            )

        if (
            decision == "inceleme_gerekli"
            and not primary_decision.kritik_faaliyet_belirsizlikleri
        ):
            warnings.append(
                "İnceleme gerekli kararı için kritik faaliyet belirsizliği açıklanmamış."
            )
            issues.append(
                ValidationIssue(
                    code="review_without_activity_uncertainty",
                    message=(
                        "İnceleme gerekli kararı kritik faaliyet belirsizliğiyle "
                        "desteklenmedi."
                    ),
                    severity="warning",
                    source=source,
                )
            )

        blocking = any(issue.severity == "blocking" for issue in issues)
        safety_blocking_codes = {
            "invalid_decision_value",
            "invalid_evidence_reference",
            "missing_evidence",
            "decision_without_evidence_reference",
            "external_information_used",
            "legacy_missing_evidence",
            "criterion_source_unavailable",
            "unverified_negative_scope_claim",
            "verified_negative_scope_omitted",
            "suitable_with_verified_negative_scope",
            "decision_reason_conflict",
            "suitable_with_unsuitable_reasons",
            "activity_participation_decision_conflict",
            "missing_suitability_reason",
            "missing_unsuitability_reason",
        }
        has_safety_blocker = any(
            issue.severity == "blocking" and issue.code in safety_blocking_codes
            for issue in issues
        )
        mandatory_rejection_verified = bool(
            failed_mandatory_criteria
            and not unknown_mandatory_criteria
            and not has_safety_blocker
        )

        forced_decision = None
        if negative_scope.scope_type == "full":
            forced_decision = "uygun_degil"
        elif negative_scope.scope_type == "mixed":
            forced_decision = "inceleme_gerekli"
        elif blocking:
            # Yalnız kaynakta doğrulanmış ve şirketçe karşılanmadığı doğrulanmış
            # zorunlu kriter kesin ret üretir. Diğer güvenlik sorunları incelemedir.
            forced_decision = (
                "uygun_degil"
                if mandatory_rejection_verified
                else "inceleme_gerekli"
            )

        activity_rejection_verified = bool(
            negative_scope.scope_type == "full"
            or (
                decision == "uygun_degil"
                and primary_decision.negatif_kapsam_cakismasi
                and negative_scope.verified
                and negative_scope.scope_type != "mixed"
                and not blocking
            )
        )
        source_missing_labels = [
            self._assessment_label(assessment)
            for assessment in criterion_assessments
            if (
                validation_context is not None
                and not assessment.source_available
                and not any(
                    chunk_id in invalid_refs
                    for chunk_id in assessment.evidence_chunk_ids
                )
            )
        ]

        return ValidationResult(
            passed=not blocking,
            forced_decision=forced_decision,
            issues=issues,
            has_blocking_issue=blocking,
            human_review_required=forced_decision == "inceleme_gerekli",
            verified_rejection=(
                mandatory_rejection_verified or activity_rejection_verified
            ),
            mandatory_rejection_verified=mandatory_rejection_verified,
            missing_mandatory_evidence=bool(unknown_mandatory_criteria),
            source_external_information_used=primary_decision.kaynak_disinda_bilgi_var_mi,
            contradictions=[
                issue.message
                for issue in issues
                if issue.code
                in {
                    "decision_reason_conflict",
                    "suitable_with_unsuitable_reasons",
                    "activity_participation_decision_conflict",
                    "suitable_with_verified_negative_scope",
                    "verified_negative_scope_omitted",
                    "unverified_negative_scope_claim",
                }
            ],
            missing_required_evidence=list(
                dict.fromkeys(
                    [
                        *(
                            self._criterion_label(item)
                            for item in unknown_mandatory_criteria
                        ),
                        *(
                            primary_decision.dogrulanamayan_katilim_sartlari
                            if validation_context is None
                            else []
                        ),
                        *source_missing_labels,
                    ]
                )
            ),
            invalid_evidence_references=invalid_refs,
            deterministic_rules_applied=rules,
            warnings=warnings,
            criterion_assessments=criterion_assessments,
            negative_scope=negative_scope,
        )

    def _assess_criteria(
        self,
        criteria: list[CriterionResult],
        *,
        validation_context: DecisionValidationContext | None,
    ) -> list[CriterionEvidenceAssessment]:
        if validation_context is None:
            return [
                CriterionEvidenceAssessment(
                    criterion_id=criterion.criterion_id,
                    description=criterion.description,
                    model_status=criterion.status,
                    source_status="mandatory",
                    source_available=False,
                    evidence_chunk_ids=list(criterion.evidence_chunk_ids),
                    reason=(
                        "Kaynak bağlamı verilmedi; geriye uyumluluk için model kriteri "
                        "zorunlu kabul edildi."
                    ),
                )
                for criterion in criteria
            ]
        return [
            assess_criterion_evidence(
                criterion,
                validation_context.evidence_text_by_chunk,
            )
            for criterion in criteria
        ]

    @staticmethod
    def _append_criterion_source_issues(
        assessments: list[CriterionEvidenceAssessment],
        *,
        invalid_refs: list[str],
        source: str,
        issues: list[ValidationIssue],
        warnings: list[str],
    ) -> None:
        for assessment in assessments:
            label = IsbakDeterministicValidator._assessment_label(assessment)
            if assessment.source_status == "not_required":
                message = f"Kaynakta istenmediği belirtilen kriter engelleyici yapılmadı: {label}"
                warnings.append(message)
                issues.append(
                    ValidationIssue(
                        code="criterion_explicitly_not_required",
                        message=message,
                        severity="warning",
                        source=source,
                        related_chunk_ids=assessment.matched_chunk_ids,
                    )
                )
            elif assessment.source_status == "non_blocking":
                message = f"Standart idari/teklif süreci kriteri engelleyici yapılmadı: {label}"
                warnings.append(message)
                issues.append(
                    ValidationIssue(
                        code="non_blocking_participation_criterion",
                        message=message,
                        severity="warning",
                        source=source,
                        related_chunk_ids=assessment.matched_chunk_ids,
                    )
                )
            elif assessment.source_status == "unverified":
                source_is_invalid = any(
                    chunk_id in invalid_refs
                    for chunk_id in assessment.evidence_chunk_ids
                )
                if not assessment.source_available and not source_is_invalid:
                    issues.append(
                        ValidationIssue(
                            code="criterion_source_unavailable",
                            message=f"Kriterin kaynak metni doğrulama bağlamında yok: {label}",
                            severity="blocking",
                            source=source,
                            related_chunk_ids=assessment.evidence_chunk_ids,
                        )
                    )
                elif assessment.source_available:
                    message = f"Modelin zorunlu kriter iddiası kaynakta doğrulanmadı: {label}"
                    warnings.append(message)
                    issues.append(
                        ValidationIssue(
                            code="criterion_not_proven_mandatory",
                            message=message,
                            severity="warning",
                            source=source,
                            related_chunk_ids=assessment.evidence_chunk_ids,
                        )
                    )

    @staticmethod
    def _criterion_label(criterion: CriterionResult) -> str:
        criterion_id = str(criterion.criterion_id).strip()
        description = str(criterion.description).strip()
        if criterion_id and description and criterion_id != description:
            return f"{criterion_id}: {description}"
        return description or criterion_id or "Tanımsız zorunlu kriter"

    @staticmethod
    def _assessment_label(assessment: CriterionEvidenceAssessment) -> str:
        criterion_id = assessment.criterion_id.strip()
        description = assessment.description.strip()
        if criterion_id and description and criterion_id != description:
            return f"{criterion_id}: {description}"
        return description or criterion_id or "Tanımsız kriter"


__all__ = ["IsbakDeterministicValidator"]
