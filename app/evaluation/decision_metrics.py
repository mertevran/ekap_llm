"""Üç sınıflı ihale kararları için saf sınıflandırma ölçümleri."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

DECISION_LABELS = ("uygun", "uygun_degil", "inceleme_gerekli")


def _validate_label(value: str, *, field_name: str) -> str:
    label = str(value or "").strip().casefold()
    if label not in DECISION_LABELS:
        raise ValueError(
            f"Geçersiz {field_name}: {value!r}. "
            f"İzin verilenler: {', '.join(DECISION_LABELS)}"
        )
    return label


def evaluate_decision_labels(
    actual_labels: Sequence[str],
    predicted_labels: Sequence[str],
) -> dict[str, Any]:
    """Sınıf bazında kesinlik, duyarlılık ve F1 değerlerini hesaplar."""

    if len(actual_labels) != len(predicted_labels):
        raise ValueError("Gerçek ve tahmin edilen etiket sayıları eşit olmalıdır.")
    if not actual_labels:
        raise ValueError("En az bir etiketli karar gereklidir.")

    actual = [
        _validate_label(value, field_name="insan etiketi")
        for value in actual_labels
    ]
    predicted = [
        _validate_label(value, field_name="model etiketi")
        for value in predicted_labels
    ]
    matrix = {
        label: {predicted_label: 0 for predicted_label in DECISION_LABELS}
        for label in DECISION_LABELS
    }
    for actual_label, predicted_label in zip(actual, predicted, strict=True):
        matrix[actual_label][predicted_label] += 1

    per_class: dict[str, dict[str, float | int]] = {}
    total_correct = 0
    for label in DECISION_LABELS:
        true_positive = matrix[label][label]
        false_positive = sum(
            matrix[other][label]
            for other in DECISION_LABELS
            if other != label
        )
        false_negative = sum(
            matrix[label][other]
            for other in DECISION_LABELS
            if other != label
        )
        support = sum(matrix[label].values())
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        total_correct += true_positive
        per_class[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
        }

    sample_count = len(actual)
    macro = {
        metric: round(
            sum(float(per_class[label][metric]) for label in DECISION_LABELS)
            / len(DECISION_LABELS),
            6,
        )
        for metric in ("precision", "recall", "f1")
    }
    weighted = {
        metric: round(
            sum(
                float(per_class[label][metric])
                * int(per_class[label]["support"])
                for label in DECISION_LABELS
            )
            / sample_count,
            6,
        )
        for metric in ("precision", "recall", "f1")
    }
    return {
        "sample_count": sample_count,
        "labels": list(DECISION_LABELS),
        "accuracy": round(total_correct / sample_count, 6),
        "per_class": per_class,
        "macro_average": macro,
        "weighted_average": weighted,
        "confusion_matrix": matrix,
    }


__all__ = ["DECISION_LABELS", "evaluate_decision_labels"]
