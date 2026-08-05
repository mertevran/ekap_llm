from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from app.classification.isbak_tender_profile_classifier import (
    IsbakTenderProfileClassifier,
)
from app.company_profiles import IsbakProfileLoader
from app.database.tender_repository import TenderRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aktif ihaleleri İSBAK alt profilleriyle eşleştirir. "
            "Veritabanında veya Qdrant'ta değişiklik yapmaz."
        )
    )
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--show", type=int, default=25)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/isbak_profile_classification"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        raise ValueError("--limit pozitif olmalıdır.")
    if args.batch_size <= 0:
        raise ValueError("--batch-size pozitif olmalıdır.")

    loader = IsbakProfileLoader()
    errors = loader.validate_all()
    if errors:
        raise RuntimeError("İSBAK profil doğrulaması başarısız:\n- " + "\n- ".join(errors))

    repository = TenderRepository()
    classifier = IsbakTenderProfileClassifier(loader)
    total_active = repository.count_active_tenders()
    results = []

    for batch_no, tenders in enumerate(
        repository.iter_active_tender_batches(
            batch_size=args.batch_size,
            limit=args.limit,
        ),
        start=1,
    ):
        for tender in tenders:
            results.append(classifier.classify(tender))
        print(f"Grup {batch_no} tamamlandı | işlenen={len(results)}")

    priority = {
        "guclu_eslesme": 0,
        "kosullu_eslesme": 1,
        "inceleme_gerekli": 2,
        "ilgisiz": 3,
    }
    results.sort(
        key=lambda item: (
            priority[item.overall_status],
            -(item.matches[0].raw_score if item.matches else 0.0),
            item.ikn,
        )
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "isbak_tender_profiles.json"
    csv_path = args.output_dir / "isbak_tender_profiles.csv"

    counts = {status: sum(item.overall_status == status for item in results) for status in priority}

    json_path.write_text(
        json.dumps(
            {
                "total_active_snapshot": total_active,
                "processed": len(results),
                "counts": counts,
                "results": [item.to_dict() for item in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        fieldnames = [
            "ikn",
            "tender_id",
            "title",
            "overall_status",
            "primary_profile_code",
            "profile_codes",
            "evaluation_profile_codes",
            "top_score",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "ikn": item.ikn,
                    "tender_id": item.tender_id,
                    "title": item.title,
                    "overall_status": item.overall_status,
                    "primary_profile_code": (item.primary_profile_code or ""),
                    "profile_codes": " | ".join(item.profile_codes),
                    "evaluation_profile_codes": " | ".join(item.evaluation_profile_codes),
                    "top_score": (item.matches[0].raw_score if item.matches else 0),
                }
            )

    print()
    print("=" * 90)
    print("İSBAK ÇOKLU PROFİL SINIFLANDIRMA ÖZETİ")
    print("=" * 90)
    print(f"Aktif ihale anlık görüntüsü : {total_active}")
    print(f"İşlenen kayıt               : {len(results)}")
    for status, count in counts.items():
        print(f"{status:27}: {count}")

    print()
    print("İlk adaylar")
    print("-" * 90)
    shown = 0
    for item in results:
        if item.overall_status == "ilgisiz":
            continue
        shown += 1
        print(f"{shown:>3}. [{item.overall_status}] {item.ikn} | {item.title}")
        print("     Profiller: " + ", ".join(item.profile_codes))
        if item.matches:
            print(f"     En yüksek puan: {item.matches[0].raw_score}")
        if shown >= args.show:
            break

    print()
    print(f"JSON çıktı: {json_path}")
    print(f"CSV çıktı : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
