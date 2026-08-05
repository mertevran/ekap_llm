#!/usr/bin/env python
"""
scripts/audit_chunking.py

Mevcut parçalama çıktısını analiz eder.
Boyut dağılımı, kısa/uzun parçalar ve deduplication raporlar.

Kullanım:
  python scripts/audit_chunking.py [--limit N]

Çıktılar:
  reports/chunking_summary.json
  reports/chunking_distribution.csv
  reports/chunking_oversized_chunks.csv
  reports/chunking_short_chunks.csv
  reports/chunking_samples.md
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPORTS = Path("reports")
REPORTS.mkdir(exist_ok=True)


def _check_env() -> bool:
    required = ["DATABASE_HOST", "DATABASE_NAME", "DATABASE_USER", "DATABASE_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v, "")]
    if missing:
        print(f"ORTAM BAĞIMLILIĞI NEDENİYLE DOĞRULANAMADI. Eksik: {missing}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Parçalama denetimi")
    parser.add_argument("--limit", type=int, default=50, help="Test edilecek ihale sayısı")
    args = parser.parse_args()

    if not _check_env():
        return 2

    try:
        from app.database.tender_repository import TenderRepository
        from app.indexing.active_tender_indexer import is_isbak_tender, is_tender_empty
        from app.indexing.chunker import SectionAwareChunker
        from app.indexing.document_builder import TenderDocumentBuilder

        repo = TenderRepository()
        builder = TenderDocumentBuilder()
        chunker = SectionAwareChunker()

        all_chunks = []
        section_type_counts: Counter = Counter()
        tender_chunk_counts: list[int] = []
        short_chunks = []
        oversized_chunks = []
        samples: list[dict] = []

        active = repo.get_active_tenders(limit=args.limit)
        processed = 0
        isbak_excluded = 0
        empty_excluded = 0

        for tender in active:
            if is_isbak_tender(tender.idare_adi):
                isbak_excluded += 1
                continue
            if is_tender_empty(tender):
                empty_excluded += 1
                continue

            doc = builder.build(tender)
            chunks = chunker.chunk_document(doc)

            tender_chunk_counts.append(len(chunks))
            processed += 1

            for chunk in chunks:
                text = chunk.get("text", "")
                stype = chunk.get("section_type", "unknown")
                all_chunks.append(len(text))
                section_type_counts[stype] += 1

                if len(text) < chunker.chunk_min_chars:
                    short_chunks.append(
                        {"ikn": tender.ikn, "section_type": stype, "length": len(text)}
                    )
                if len(text) > chunker.chunk_hard_max_chars:
                    oversized_chunks.append(
                        {"ikn": tender.ikn, "section_type": stype, "length": len(text)}
                    )

            if len(samples) < 5 and chunks:
                samples.append(
                    {
                        "ikn": tender.ikn,
                        "tender_name": tender.adi,
                        "chunk_count": len(chunks),
                        "first_chunk_preview": chunks[0].get("text", "")[:300],
                    }
                )

        # Özet
        total_chunks = len(all_chunks)
        avg_len = int(sum(all_chunks) / max(total_chunks, 1))
        summary = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "ihale_sayisi": processed,
            "isbak_excluded": isbak_excluded,
            "empty_excluded": empty_excluded,
            "toplam_parca": total_chunks,
            "ortalama_parca_uzunlugu": avg_len,
            "min_parca_uzunlugu": min(all_chunks) if all_chunks else 0,
            "max_parca_uzunlugu": max(all_chunks) if all_chunks else 0,
            "kisa_parca_sayisi": len(short_chunks),
            "buyuk_parca_sayisi": len(oversized_chunks),
            "bolum_dagilimi": dict(section_type_counts),
        }
        with open(REPORTS / "chunking_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        # CSV: Dağılım
        with open(REPORTS / "chunking_distribution.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["section_type", "chunk_sayisi"])
            for stype, count in section_type_counts.most_common():
                w.writerow([stype, count])

        # CSV: Kısa parçalar
        with open(REPORTS / "chunking_short_chunks.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ikn", "section_type", "length"])
            for item in short_chunks[:100]:
                w.writerow([item["ikn"], item["section_type"], item["length"]])

        # CSV: Büyük parçalar
        with open(
            REPORTS / "chunking_oversized_chunks.csv", "w", newline="", encoding="utf-8"
        ) as f:
            w = csv.writer(f)
            w.writerow(["ikn", "section_type", "length"])
            for item in oversized_chunks[:100]:
                w.writerow([item["ikn"], item["section_type"], item["length"]])

        # MD: Örnekler
        with open(REPORTS / "chunking_samples.md", "w", encoding="utf-8") as f:
            f.write("# Parçalama Örnekleri\n\n")
            for s in samples:
                f.write(f"## {s['ikn']}: {s['tender_name']}\n")
                f.write(f"**Parça sayısı:** {s['chunk_count']}\n\n")
                f.write(f"**İlk parça önizlemesi:**\n```\n{s['first_chunk_preview']}\n```\n\n")

        print(f"İşlenen: {processed} ihale, {total_chunks} parça")
        print(f"Kısa: {len(short_chunks)}, Büyük: {len(oversized_chunks)}")
        print(f"Raporlar: {REPORTS}/")

    except Exception as exc:
        print(f"HATA: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
