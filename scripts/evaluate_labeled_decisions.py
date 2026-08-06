#!/usr/bin/env python
"""İnsan etiketleri ile karar raporunu İKN üzerinden karşılaştırır."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from app.evaluation.decision_metrics import evaluate_decision_labels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "İnsan etiketli en az 100 ihale üzerinde sınıf bazında kesinlik, "
            "duyarlılık ve F1 hesaplar."
        )
    )
    parser.add_argument("--labels", required=True, help="Etiket CSV veya JSONL dosyası.")
    parser.add_argument(
        "--predictions",
        required=True,
        help="tender_model_decisions.jsonl veya eşdeğer karar dosyası.",
    )
    parser.add_argument("--output-dir", required=True, help="Ölçüm raporu klasörü.")
    parser.add_argument("--minimum-labeled", type=int, default=100)
    parser.add_argument(
        "--allow-small-sample",
        action="store_true",
        help="Yalnız geliştirme testi için asgari örnek kapısını atlar.",
    )
    return parser.parse_args()


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]
    if path.suffix.casefold() == ".jsonl":
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(
                        f"JSONL satırı nesne değil: {path}:{line_number}"
                    )
                rows.append(value)
        return rows
    raise ValueError("Yalnız .csv ve .jsonl dosyaları desteklenir.")


def _normalized_ikn(value: Any) -> str:
    return "".join(str(value or "").upper().split())


def _unique_by_ikn(
    rows: list[dict[str, Any]],
    *,
    source_name: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        ikn = _normalized_ikn(row.get("ikn"))
        if not ikn:
            raise ValueError(f"{source_name} içinde boş İKN bulundu.")
        if ikn in result:
            raise ValueError(f"{source_name} içinde tekrarlı İKN bulundu: {ikn}")
        result[ikn] = row
    return result


def main() -> int:
    args = parse_args()
    if args.minimum_labeled <= 0:
        raise ValueError("--minimum-labeled pozitif olmalıdır.")

    labels_by_ikn = _unique_by_ikn(
        _read_rows(Path(args.labels)),
        source_name="etiket dosyası",
    )
    predictions_by_ikn = _unique_by_ikn(
        _read_rows(Path(args.predictions)),
        source_name="karar dosyası",
    )
    matched_ikns = sorted(set(labels_by_ikn) & set(predictions_by_ikn))
    actual = [
        str(labels_by_ikn[ikn].get("human_label") or "")
        for ikn in matched_ikns
    ]
    predicted = [
        str(
            predictions_by_ikn[ikn].get("final_decision")
            or predictions_by_ikn[ikn].get("decision")
            or ""
        )
        for ikn in matched_ikns
    ]
    metrics = evaluate_decision_labels(actual, predicted)
    minimum_met = len(matched_ikns) >= args.minimum_labeled
    report = {
        "minimum_labeled_required": args.minimum_labeled,
        "minimum_labeled_met": minimum_met,
        "valid_for_acceptance": minimum_met,
        "matched_ikns": len(matched_ikns),
        "labels_without_prediction": sorted(set(labels_by_ikn) - set(predictions_by_ikn)),
        "predictions_without_label": sorted(set(predictions_by_ikn) - set(labels_by_ikn)),
        "metrics": metrics,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "decision_classification_metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "decision_classification_metrics.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "class",
                "precision",
                "recall",
                "f1",
                "support",
                "true_positive",
                "false_positive",
                "false_negative",
            ]
        )
        for label, values in metrics["per_class"].items():
            writer.writerow(
                [
                    label,
                    values["precision"],
                    values["recall"],
                    values["f1"],
                    values["support"],
                    values["true_positive"],
                    values["false_positive"],
                    values["false_negative"],
                ]
            )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if minimum_met or args.allow_small_sample else 2


if __name__ == "__main__":
    raise SystemExit(main())
