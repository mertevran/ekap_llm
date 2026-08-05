#!/usr/bin/env python3
"""Yazılım ihalesi keşif çıktısından indeksleme manifesti üretir."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_CLASSES = ("doğrudan_uygun", "koşullu_uygun")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "software_tender_discovery.json dosyasından seçilen "
            "sınıflar için indeksleme manifesti üretir."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("benchmark_results/software_tender_discovery.json"),
        help="Keşif JSON dosyasının yolu.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_results/software_index_manifest"),
        help="Çıktı klasörü.",
    )
    parser.add_argument(
        "--classes",
        nargs="+",
        default=list(DEFAULT_CLASSES),
        help="Manifest içine alınacak sınıflar.",
    )
    return parser.parse_args()


def load_discovery(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Girdi dosyası bulunamadı: {path}")

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError("Keşif dosyasının kökü nesne olmalıdır.")

    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("Keşif dosyasında 'results' listesi bulunamadı.")

    return payload


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    required = ("ikn", "tender_id", "title", "classification")
    missing = [field for field in required if not str(record.get(field, "")).strip()]
    if missing:
        raise ValueError("Zorunlu alanları eksik kayıt bulundu: " + ", ".join(missing))

    return {
        "ikn": str(record["ikn"]).strip(),
        "tender_id": str(record["tender_id"]).strip(),
        "title": str(record["title"]).strip(),
        "classification": str(record["classification"]).strip(),
        "evidence_score": int(record.get("evidence_score", 0) or 0),
        "tender_date": str(record.get("tender_date", "") or "").strip(),
        "tender_type": str(record.get("tender_type", "") or "").strip(),
        "authority": str(record.get("authority", "") or "").strip(),
        "matched_sources": list(record.get("matched_sources", []) or []),
        "direct_matches": list(record.get("direct_matches", []) or []),
        "conditional_matches": list(record.get("conditional_matches", []) or []),
        "support_matches": list(record.get("support_matches", []) or []),
        "direct_okas_matches": list(record.get("direct_okas_matches", []) or []),
        "conditional_okas_matches": list(record.get("conditional_okas_matches", []) or []),
    }


def build_manifest(
    payload: dict[str, Any],
    accepted_classes: set[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_ikns: set[str] = set()
    seen_tender_ids: set[str] = set()

    for raw_record in payload["results"]:
        if not isinstance(raw_record, dict):
            continue

        classification = str(raw_record.get("classification", "")).strip()

        if classification not in accepted_classes:
            continue

        record = normalize_record(raw_record)

        if record["ikn"] in seen_ikns:
            raise ValueError(f"Tekrarlanan İKN bulundu: {record['ikn']}")
        if record["tender_id"] in seen_tender_ids:
            raise ValueError(f"Tekrarlanan tender_id bulundu: {record['tender_id']}")

        seen_ikns.add(record["ikn"])
        seen_tender_ids.add(record["tender_id"])
        selected.append(record)

    selected.sort(
        key=lambda item: (
            0 if item["classification"] == "doğrudan_uygun" else 1,
            -item["evidence_score"],
            item["ikn"],
        )
    )
    return selected


def write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    fieldnames = [
        "ikn",
        "tender_id",
        "title",
        "classification",
        "evidence_score",
        "tender_date",
        "tender_type",
        "authority",
        "matched_sources",
        "direct_matches",
        "conditional_matches",
        "support_matches",
        "direct_okas_matches",
        "conditional_okas_matches",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for record in records:
            row = dict(record)
            for field in (
                "matched_sources",
                "direct_matches",
                "conditional_matches",
                "support_matches",
                "direct_okas_matches",
                "conditional_okas_matches",
            ):
                row[field] = " | ".join(row[field])
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    accepted_classes = {str(value).strip() for value in args.classes if str(value).strip()}

    if not accepted_classes:
        raise ValueError("En az bir sınıf seçilmelidir.")

    payload = load_discovery(args.input)
    records = build_manifest(payload, accepted_classes)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    class_counts = Counter(record["classification"] for record in records)

    manifest_payload = {
        "source_file": str(args.input),
        "selected_classes": sorted(accepted_classes),
        "total_selected": len(records),
        "class_counts": dict(class_counts),
        "ikns": [record["ikn"] for record in records],
        "records": records,
    }

    json_path = args.output_dir / "software_index_manifest.json"
    csv_path = args.output_dir / "software_index_manifest.csv"
    ikn_path = args.output_dir / "software_index_ikns.txt"

    write_json(json_path, manifest_payload)
    write_csv(csv_path, records)
    ikn_path.write_text(
        "\n".join(record["ikn"] for record in records) + "\n",
        encoding="utf-8",
    )

    print("Yazılım ihalesi indeksleme manifesti hazır.")
    print(f"Seçilen toplam ihale : {len(records)}")
    for classification, count in sorted(class_counts.items()):
        print(f"- {classification}: {count}")
    print(f"JSON çıktı : {json_path}")
    print(f"CSV çıktı  : {csv_path}")
    print(f"İKN listesi: {ikn_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
