from app.evaluation.decision_metrics import evaluate_decision_labels


def test_decision_metrics_calculates_per_class_values() -> None:
    result = evaluate_decision_labels(
        ["uygun", "uygun", "uygun_degil", "inceleme_gerekli"],
        ["uygun", "uygun_degil", "uygun_degil", "inceleme_gerekli"],
    )

    assert result["sample_count"] == 4
    assert result["accuracy"] == 0.75
    assert result["per_class"]["uygun"]["precision"] == 1.0
    assert result["per_class"]["uygun"]["recall"] == 0.5
    assert result["per_class"]["uygun_degil"]["precision"] == 0.5
    assert result["per_class"]["inceleme_gerekli"]["f1"] == 1.0
