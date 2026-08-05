"""Karar birleştirici — ihale odaklı modda birden fazla profil kararını birleştirir.

Kurallar:
- En az bir profil "uygun" → nihai karar "uygun"
- Hiç uygun yok, en az bir "inceleme_gerekli" → nihai karar "inceleme_gerekli"
- Tüm profiller "uygun_degil" → nihai karar "uygun_degil"
- En yüksek güvenli uygun profil primary_profile_code olur.
- Modeller çelişirse insan incelemesi zorunlu.
"""

from __future__ import annotations

from typing import Any

from app.decision.models import FinalTenderDecision
from app.matching.models import TenderFinalDecision


class DecisionAggregator:
    """İhale odaklı modda birden fazla profil kararını birleştirir."""

    def aggregate(
        self,
        *,
        tender_id: str,
        ikn: str,
        tender_name: str,
        authority_name: str,
        best_retrieval_score: float,
        profile_decisions: list[dict[str, Any]],
    ) -> TenderFinalDecision:
        """Profil kararlarını birleştirerek ihale nihai kararını üretir.

        Args:
            profile_decisions: Her biri aşağıdaki anahtarlara sahip dict listesi:
                - profile_code: str
                - profile_name: str
                - final_decision: str  (normalize edilmiş: uygun/uygun_degil/inceleme_gerekli)
                - final_confidence: float
                - positive_reasons: list[str]
                - negative_reasons: list[str]
                - missing_evidence: list[str]
                - human_review_required: bool
                - human_review_reason: str

        Returns:
            TenderFinalDecision — birleşik nihai karar.
        """
        if not profile_decisions:
            return TenderFinalDecision(
                tender_id=tender_id,
                ikn=ikn,
                tender_name=tender_name,
                authority_name=authority_name,
                final_decision="inceleme_gerekli",
                final_confidence=0.0,
                primary_profile_code="",
                supporting_profile_codes=[],
                evaluated_profile_codes=[],
                best_retrieval_score=best_retrieval_score,
                positive_reasons=["Değerlendirilen profil bulunamadı."],
                negative_reasons=[],
                missing_evidence=["Hiç profil değerlendirmesi yapılmadı."],
                human_review_required=True,
                human_review_reason="Değerlendirilen profil bulunamadı.",
            )

        evaluated_codes = [d["profile_code"] for d in profile_decisions]

        uygun = [d for d in profile_decisions if d["final_decision"] == "uygun"]
        inceleme = [
            d for d in profile_decisions if d["final_decision"] == "inceleme_gerekli"
        ]
        uygun_degil = [
            d for d in profile_decisions if d["final_decision"] == "uygun_degil"
        ]

        any_human_review = any(d.get("human_review_required", False) for d in profile_decisions)
        human_review_reasons = [
            d.get("human_review_reason", "")
            for d in profile_decisions
            if d.get("human_review_required") and d.get("human_review_reason")
        ]

        # Birleşik gerekçeler
        all_positive: list[str] = []
        all_negative: list[str] = []
        all_missing: list[str] = []
        for d in profile_decisions:
            all_positive.extend(d.get("positive_reasons", []))
            all_negative.extend(d.get("negative_reasons", []))
            all_missing.extend(d.get("missing_evidence", []))

        if uygun:
            # Uygun profilleri güven değerine göre sırala
            uygun_sorted = sorted(
                uygun, key=lambda d: float(d.get("final_confidence", 0.0)), reverse=True
            )
            primary = uygun_sorted[0]
            supporting = [d["profile_code"] for d in uygun_sorted[1:]]
            final_decision = "uygun"
            final_confidence = float(primary.get("final_confidence", 0.0))
            human_review_required = any_human_review
            human_review_reason = " | ".join(human_review_reasons) if human_review_reasons else ""

        elif inceleme:
            inceleme_sorted = sorted(
                inceleme, key=lambda d: float(d.get("final_confidence", 0.0)), reverse=True
            )
            primary = inceleme_sorted[0]
            supporting = []
            final_decision = "inceleme_gerekli"
            final_confidence = float(primary.get("final_confidence", 0.0))
            human_review_required = True
            human_review_reason = (
                " | ".join(human_review_reasons)
                if human_review_reasons
                else "Profil kararları inceleme gerektiriyor."
            )

        else:
            # Tüm profiller uygun_degil
            uygun_degil_sorted = sorted(
                uygun_degil,
                key=lambda d: float(d.get("final_confidence", 0.0)),
                reverse=True,
            )
            primary = uygun_degil_sorted[0]
            supporting = []
            final_decision = "uygun_degil"
            final_confidence = float(primary.get("final_confidence", 0.0))
            human_review_required = any_human_review
            human_review_reason = " | ".join(human_review_reasons) if human_review_reasons else ""

        return TenderFinalDecision(
            tender_id=tender_id,
            ikn=ikn,
            tender_name=tender_name,
            authority_name=authority_name,
            final_decision=final_decision,
            final_confidence=round(final_confidence, 4),
            primary_profile_code=primary.get("profile_code", ""),
            supporting_profile_codes=supporting,
            evaluated_profile_codes=evaluated_codes,
            best_retrieval_score=round(best_retrieval_score, 4),
            positive_reasons=list(dict.fromkeys(all_positive))[:10],
            negative_reasons=list(dict.fromkeys(all_negative))[:10],
            missing_evidence=list(dict.fromkeys(all_missing))[:10],
            human_review_required=human_review_required,
            human_review_reason=human_review_reason,
            primary_model=primary.get("primary_model", ""),
            primary_decision=primary.get("primary_decision", ""),
            primary_confidence=primary.get("primary_confidence", 0.0),
            primary_used_chunk_ids=primary.get("primary_used_chunk_ids", []),
            secondary_triggered=primary.get("secondary_triggered", False),
            secondary_trigger_reasons=primary.get("secondary_trigger_reasons", []),
            secondary_model=primary.get("secondary_model", ""),
            secondary_succeeded=primary.get("secondary_succeeded", False),
            secondary_decision=primary.get("secondary_decision", ""),
            secondary_confidence=primary.get("secondary_confidence", 0.0),
            secondary_used_chunk_ids=primary.get("secondary_used_chunk_ids", []),
            secondary_error_type=primary.get("secondary_error_type", ""),
            secondary_error_message=primary.get("secondary_error_message", ""),
            model_agreement=primary.get("model_agreement", False),
            merge_rule=primary.get("merge_rule", ""),
            agreement_status=primary.get("agreement_status", ""),
            validation_issues=primary.get("validation_issues", []),
            missing_mandatory_evidence=primary.get("missing_mandatory_evidence", False),
            mandatory_missing_evidence=primary.get("mandatory_missing_evidence", []),
            optional_missing_evidence=primary.get("optional_missing_evidence", [])
        )

    def aggregate_from_pipeline_decisions(
        self,
        *,
        tender_id: str,
        ikn: str,
        tender_name: str,
        authority_name: str,
        best_retrieval_score: float,
        pipeline_decisions: list[FinalTenderDecision],
    ) -> TenderFinalDecision:
        """FinalTenderDecision listesini TenderFinalDecision'a dönüştürür."""
        profile_decisions = []
        for pd in pipeline_decisions:
            profile_decisions.append(
                {
                    "profile_code": pd.primary_profile_code,
                    "profile_name": pd.primary_profile_code,
                    "final_decision": pd.final_decision,
                    "final_confidence": pd.final_confidence,
                    "positive_reasons": pd.primary_model.uygunluk_gerekceleri
                    if pd.primary_model
                    else [],
                    "negative_reasons": pd.primary_model.uygunsuzluk_gerekceleri
                    if pd.primary_model
                    else [],
                    "missing_evidence": pd.primary_model.eksik_kanitlar
                    if pd.primary_model
                    else [],
                    "human_review_required": pd.human_review_required,
                    "human_review_reason": pd.human_review_reason,
                    "primary_model": pd.primary_model.model_name if pd.primary_model else "",
                    "primary_decision": pd.primary_decision,
                    "primary_confidence": pd.primary_confidence,
                    "primary_used_chunk_ids": pd.primary_used_chunk_ids,
                    "secondary_triggered": pd.secondary_triggered,
                    "secondary_trigger_reasons": pd.secondary_trigger_reasons,
                    "secondary_model": pd.secondary_model.model_name if getattr(pd, "secondary_model", None) else "",
                    "secondary_succeeded": pd.secondary_succeeded,
                    "secondary_decision": pd.secondary_decision,
                    "secondary_confidence": pd.secondary_confidence,
                    "secondary_used_chunk_ids": pd.secondary_used_chunk_ids,
                    "secondary_error_type": pd.secondary_error_type,
                    "secondary_error_message": pd.secondary_error_message,
                    "model_agreement": pd.model_agreement,
                    "merge_rule": pd.merge_rule,
                    "agreement_status": pd.agreement_status,
                    "validation_issues": pd.validation_issues,
                    "missing_mandatory_evidence": pd.missing_mandatory_evidence,
                    "mandatory_missing_evidence": pd.mandatory_missing_evidence,
                    "optional_missing_evidence": pd.optional_missing_evidence,
                }
            )
        return self.aggregate(
            tender_id=tender_id,
            ikn=ikn,
            tender_name=tender_name,
            authority_name=authority_name,
            best_retrieval_score=best_retrieval_score,
            profile_decisions=profile_decisions,
        )


__all__ = ["DecisionAggregator"]
