from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.classification.isbak_tender_profile_classifier import (
    CLASSIFIER_VERSION,
    IsbakTenderProfileClassifier,
)
from app.company_profiles import IsbakProfileLoader
from app.database.tender_repository import TenderRepository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/isbak_profile_classification_v2.json"),
    )
    args = parser.parse_args()

    loader = IsbakProfileLoader()
    errors = loader.validate_all()
    if errors:
        raise RuntimeError("\n".join(errors))

    repository = TenderRepository()
    classifier = IsbakTenderProfileClassifier(loader)
    results = []

    for batch in repository.iter_active_tender_batches(
        batch_size=args.batch_size,
        limit=args.limit,
    ):
        results.extend(classifier.classify(tender) for tender in batch)

    counts = {}
    for result in results:
        counts[result.overall_status] = counts.get(result.overall_status, 0) + 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "classifier_version": CLASSIFIER_VERSION,
                "processed": len(results),
                "counts": counts,
                "results": [result.to_dict() for result in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("İşlenen:", len(results))
    print("Dağılım:", counts)
    print("Çıktı:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
