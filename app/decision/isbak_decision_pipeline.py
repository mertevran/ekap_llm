from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.decision.confidence_calibrator import calibrate_confidence
from app.decision.models import (
    DecisionValidationContext,
    DecisionLabel,
    FinalTenderDecision,
    ModelDecision,
    ValidationResult,
    combine_validation_results,
)


class DecisionModel(Protocol):
    name: str

    def analyze(
        self,
        *,
        tender_id: str,
        ikn: str,
        category_code: str,
        tender_context: str,
        company_context: str,
        matching_mode: str = "profile_to_tender",
        retrieval_score: float = 0.0,
        score_breakdown: dict[str, Any] | None = None,
        valid_chunk_ids: list[str] | None = None,
        primary_profile_code: str = "",
    ) -> ModelDecision: ...


class DeterministicValidator(Protocol):
    def validate(
        self,
        *,
        tender_id: str,
        ikn: str,
        category_code: str,
        primary_decision: ModelDecision,
        evidence_count: int,
        valid_chunk_ids: list[str] | None = None,
        decision_source: str = "primary",
    ) -> ValidationResult: ...


class IsbakDecisionPipeline:
    """Birincil model → Python doğrulama → isteğe bağlı ikinci model."""

    def __init__(
        self,
        *,
        primary_model: DecisionModel,
        validator: DeterministicValidator,
        secondary_model: DecisionModel | None = None,
        secondary_confidence_threshold: float = 0.75,
    ) -> None:
        self.primary_model = primary_model
        self.validator = validator
        self.secondary_model = secondary_model
        self.secondary_confidence_threshold = secondary_confidence_threshold

    def run(
        self,
        *,
        tender_id: str,
        ikn: str,
        tender_name: str,
        authority_name: str,
        category_code: str,
        primary_profile_code: str,
        secondary_profile_codes: list[str],
        tender_context: str,
        company_context: str,
        evaluation_rules: dict[str, Any],
        evidence_count: int,
        matching_mode: str = "profile_to_tender",
        retrieval_score: float = 0.0,
        score_breakdown: dict[str, Any] | None = None,
        valid_chunk_ids: list[str] | None = None,
        validation_context: DecisionValidationContext | None = None,
        evaluated_profile_codes: list[str] | None = None,
        profile_match_scores: dict[str, float] | None = None,
    ) -> FinalTenderDecision:
        valid_chunk_ids = valid_chunk_ids or []

        primary = self.primary_model.analyze(
            tender_id=tender_id,
            ikn=ikn,
            category_code=category_code,
            tender_context=tender_context,
            company_context=company_context,
            matching_mode=matching_mode,
            retrieval_score=retrieval_score,
            score_breakdown=score_breakdown,
            valid_chunk_ids=valid_chunk_ids,
            primary_profile_code=primary_profile_code,
        )
        validation_primary = self._validate_model_decision(
            primary,
            tender_id=tender_id,
            ikn=ikn,
            category_code=category_code,
            evidence_count=evidence_count,
            valid_chunk_ids=valid_chunk_ids,
            decision_source="primary",
            validation_context=validation_context,
        )

        triggers = evaluation_rules.get(
            "ikinci_gorus_tetikleyicileri",
            [],
        )
        needs_secondary = False
        review_reasons: list[str] = []

        if not validation_primary.passed:
            needs_secondary = True
            review_reasons.append(
                "Python doğrulama kuralları çelişki tespit etti."
            )

        if (
            primary.confidence < self.secondary_confidence_threshold
            and "dusuk_guven_duzeyi" in triggers
        ):
            needs_secondary = True
            review_reasons.append(
                f"Model güven düzeyi ({primary.confidence:.3f}) eşiğin altında."
            )

        if (
            primary.decision == "inceleme_gerekli"
            and "karar_inceleme_gerekli" in triggers
        ):
            needs_secondary = True
            review_reasons.append(
                "Ana model kararı inceleme_gerekli olduğu için "
                "ikinci model tetiklendi."
            )

        if (
            primary.kritik_belirsizlikler
            and "kritik_belirsizlik" in triggers
        ):
            needs_secondary = True
            review_reasons.append("Ana model kritik belirsizlik raporladı.")

        # Tek modelli ana akışta ikinci model tetikleyicileri karar durumunu
        # değiştirmez; inceleme ihtiyacı doğrudan model/Python sonucundan gelir.
        if self.secondary_model is None:
            needs_secondary = False
            review_reasons = []

        secondary = None
        final_decision: DecisionLabel = primary.decision
        final_confidence = primary.confidence
        human_review_required = False
        human_review_reason = ""
        validation_secondary = None

        secondary_succeeded = False
        secondary_error_type = ""
        secondary_error_message = ""
        model_agreement = False
        merge_rule = "primary_only"
        agreement_status = "secondary_skipped"

        if needs_secondary and self.secondary_model is not None:
            try:
                secondary = self.secondary_model.analyze(
                    tender_id=tender_id,
                    ikn=ikn,
                    category_code=category_code,
                    tender_context=tender_context,
                    company_context=company_context,
                    matching_mode=matching_mode,
                    retrieval_score=retrieval_score,
                    score_breakdown=score_breakdown,
                    valid_chunk_ids=valid_chunk_ids,
                    primary_profile_code=primary_profile_code,
                )
                secondary_succeeded = True
                validation_secondary = self._validate_model_decision(
                    secondary,
                    tender_id=tender_id,
                    ikn=ikn,
                    category_code=category_code,
                    evidence_count=evidence_count,
                    valid_chunk_ids=valid_chunk_ids,
                    decision_source="secondary",
                    validation_context=validation_context,
                )
            except Exception as e:
                from app.pipeline.exceptions import TruncatedModelOutput
                if isinstance(e, TruncatedModelOutput):
                    secondary_error_type = "truncated_output"
                else:
                    secondary_error_type = "secondary_model_error"
                secondary_error_message = str(e)
                secondary = None
                human_review_required = True
                human_review_reason = f"Gemma ikinci görüş çıktısı tamamlanamadı. ({secondary_error_type})"
                final_decision = "inceleme_gerekli"
                merge_rule = "secondary_failure_fallback"
                agreement_status = "secondary_failed"

        # Apply Decision Aggregation Rules
        if secondary and secondary_succeeded:
            pd = primary.decision
            sd = secondary.decision

            if pd == sd:
                model_agreement = True
                agreement_status = "same_decision"
                final_decision = pd
                merge_rule = f"agreement_{pd}"
            else:
                agreement_status = "conflicting_decision"
                model_agreement = False
                final_decision = "inceleme_gerekli"
                human_review_required = True
                if pd == "uygun" and sd == "uygun_degil":
                    merge_rule = "conflict_uygun_uygun_degil"
                    human_review_reason = "Modeller çelişti (uygun vs uygun_degil)."
                elif pd == "uygun_degil" and sd == "uygun":
                    merge_rule = "conflict_uygun_degil_uygun"
                    human_review_reason = "Modeller çelişti (uygun_degil vs uygun)."
                elif pd == "inceleme_gerekli" and sd == "uygun":
                    merge_rule = "conflict_inceleme_uygun"
                    human_review_reason = "Modeller çelişti (inceleme_gerekli vs uygun)."
                elif pd == "inceleme_gerekli" and sd == "uygun_degil":
                    merge_rule = "conflict_inceleme_uygun_degil"
                    human_review_reason = "Modeller çelişti (inceleme_gerekli vs uygun_degil)."
                else:
                    merge_rule = "model_disagreement"
                    human_review_reason = "Modeller çelişti."

            if final_confidence > 0:
                final_confidence = (primary.confidence + secondary.confidence) / 2.0

        combined_validation = combine_validation_results(validation_primary, validation_secondary)

        # Model(ler)in bu noktadaki kararı yalnızca faaliyet kapsamını temsil
        # eder. Katılım şartlarına ait Python geçersiz kılmaları aşağıda nihai
        # karara uygulanır; böylece faaliyet uygunluğu kaybolmaz.
        activity_decision = final_decision

        # --- VALIDATION OVERRIDE ---
        if combined_validation.source_external_information_used:
            final_decision = "inceleme_gerekli"
            human_review_required = True
            merge_rule = "validation_override_external_information"
            human_review_reason = "Model dış bilgi kullandı."
        elif combined_validation.forced_decision == "uygun_degil":
            final_decision = "uygun_degil"
            human_review_required = False
            merge_rule = "validation_override_mandatory_rejection"
            human_review_reason = ""
        elif combined_validation.missing_mandatory_evidence:
            final_decision = "inceleme_gerekli"
            human_review_required = True
            merge_rule = "validation_override_missing_evidence"
            human_review_reason = (
                "Gerçek zorunlu kriterlerin karşılandığı doğrulanamadı."
            )
        elif combined_validation.has_blocking_issue:
            final_decision = "inceleme_gerekli"
            human_review_required = True
            merge_rule = "validation_override_blocking_issue"
            human_review_reason = "Python doğrulama kuralları kritik hata (blocking issue) tespit etti."

        if combined_validation.human_review_required and not human_review_reason:
            issue_messages = [i.message for i in combined_validation.issues[:3]]
            human_review_reason = " | ".join(issue_messages)

        secondary_triggered = needs_secondary and self.secondary_model is not None

        if self.secondary_model is None:
            agreement_status = "single_model"

        if needs_secondary and self.secondary_model is None:
            human_review_required = True
            human_review_reason = " | ".join(review_reasons)

        if final_decision == "inceleme_gerekli":
            human_review_required = True

        calibration = calibrate_confidence(
            model_decision=primary,
            final_decision=final_decision,
            validation=combined_validation,
            evidence_count=evidence_count,
            retrieval_score=retrieval_score,
            raw_confidence=final_confidence,
            context_available=validation_context is not None,
        )
        final_confidence = calibration.calibrated_confidence
        participation_status = primary.katilim_yeterliligi_durumu
        if combined_validation.verified_rejection:
            participation_status = "karsilanmiyor"
        elif combined_validation.missing_mandatory_evidence:
            participation_status = "dogrulanmadi"

        participation_review_required = bool(
            participation_status == "dogrulanmadi"
            and (
                combined_validation.missing_mandatory_evidence
                or primary.dogrulanamayan_katilim_sartlari
            )
        )
        evaluated_codes = list(
            dict.fromkeys(
                code.strip().upper()
                for code in (
                    evaluated_profile_codes
                    or [primary_profile_code, *secondary_profile_codes]
                )
                if code and code.strip()
            )
        )
        resolved_human_review_reason = ""
        if human_review_required:
            resolved_human_review_reason = (
                human_review_reason
                or primary.insan_incelemesi_gerekcesi
                or " | ".join(review_reasons)
            )

        return FinalTenderDecision(
            tender_id=tender_id,
            ikn=ikn,
            tender_name=tender_name,
            authority_name=authority_name,
            primary_profile_code=primary_profile_code,
            secondary_profile_codes=secondary_profile_codes,
            final_decision=final_decision,
            final_confidence=final_confidence,
            primary_model=primary,
            validation=combined_validation,
            secondary_model=secondary,
            human_review_required=human_review_required,
            human_review_reason=resolved_human_review_reason,
            evaluated_at=datetime.now(UTC).isoformat(),
            primary_decision=primary.decision,
            primary_confidence=primary.confidence,
            primary_used_chunk_ids=primary.kullanilan_chunk_idleri,
            secondary_triggered=secondary_triggered,
            secondary_trigger_reasons=review_reasons,
            secondary_succeeded=secondary_succeeded,
            secondary_decision=secondary.decision if secondary else "",
            secondary_confidence=secondary.confidence if secondary else 0.0,
            secondary_used_chunk_ids=secondary.kullanilan_chunk_idleri if secondary else [],
            secondary_error_type=secondary_error_type,
            secondary_error_message=secondary_error_message,
            model_agreement=model_agreement,
            merge_rule=merge_rule,
            agreement_status=agreement_status,
            validation_issues=[
                {
                    "code": issue.code,
                    "message": issue.message,
                    "severity": issue.severity,
                    "source": issue.source,
                    "related_chunk_ids": issue.related_chunk_ids,
                }
                for issue in combined_validation.issues
            ],
            missing_mandatory_evidence=combined_validation.missing_mandatory_evidence,
            mandatory_missing_evidence=[
                i.message.removeprefix(
                    "Gerçek zorunlu kriterin şirket tarafından karşılandığı "
                    "doğrulanamadı: "
                )
                for i in combined_validation.issues
                if i.code == "missing_mandatory_evidence"
            ],
            optional_missing_evidence=list(primary.dogrulanamayan_katilim_sartlari),
            katilim_yeterliligi_durumu=participation_status,
            dogrulanamayan_katilim_sartlari=list(primary.dogrulanamayan_katilim_sartlari),
            participation_review_required=participation_review_required,
            activity_decision=activity_decision,
            activity_match=primary.faaliyet_eslesmesi,
            negative_scope_verified=combined_validation.negative_scope.verified,
            matched_negative_terms=list(
                combined_validation.negative_scope.matched_terms
            ),
            evaluated_profile_codes=evaluated_codes,
            profile_match_scores=dict(profile_match_scores or {}),
            confidence_calibration=calibration,
        )

    def _validate_model_decision(
        self,
        decision: ModelDecision,
        *,
        tender_id: str,
        ikn: str,
        category_code: str,
        evidence_count: int,
        valid_chunk_ids: list[str],
        decision_source: str,
        validation_context: DecisionValidationContext | None,
    ) -> ValidationResult:
        """Yeni bağlamlı doğrulamayı desteklerken eski doğrulayıcıları korur."""

        contextual_validator = getattr(
            self.validator,
            "validate_with_context",
            None,
        )
        if validation_context is not None and callable(contextual_validator):
            return contextual_validator(
                tender_id=tender_id,
                ikn=ikn,
                category_code=category_code,
                primary_decision=decision,
                evidence_count=evidence_count,
                valid_chunk_ids=valid_chunk_ids,
                decision_source=decision_source,
                validation_context=validation_context,
            )
        return self.validator.validate(
            tender_id=tender_id,
            ikn=ikn,
            category_code=category_code,
            primary_decision=decision,
            evidence_count=evidence_count,
            valid_chunk_ids=valid_chunk_ids,
            decision_source=decision_source,
        )
