from __future__ import annotations

from typing import Any

from app.classification.isbak_tender_profile_classifier import (
    TenderProfileClassification,
)


def attach_isbak_classification(
    chunks: list[dict[str, Any]],
    classification: TenderProfileClassification,
) -> list[dict[str, Any]]:
    """Parça veri alanlarına İSBAK profil sınıflandırmasını ekler."""

    score_map = {match.profile_code: match.normalized_score for match in classification.matches}

    enriched: list[dict[str, Any]] = []
    for chunk in chunks:
        payload = dict(chunk)
        metadata = dict(payload.get("metadata", {}))
        metadata.update(
            {
                "primary_profile_code": classification.primary_profile_code,
                "profile_codes": classification.profile_codes,
                "evaluation_profile_codes": (classification.evaluation_profile_codes),
                "profile_scores": score_map,
                "classification_status": (classification.overall_status),
                "classifier_version": (classification.classifier_version),
            }
        )
        payload["metadata"] = metadata
        enriched.append(payload)

    return enriched
