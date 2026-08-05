#!/usr/bin/env python
"""
scripts/test_faiss_retrieval.py

FAISS indeksini mevcut profil sorguları ile test eder.
Her profil için retrieval çalıştırır ve sonuçları raporlar.

Kullanım:
  python scripts/test_faiss_retrieval.py [--profile-code AUS-01] [--limit 5]

Çıktılar:
  reports/faiss_retrieval_test_results.json
  reports/faiss_tender_ranking.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
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
    parser = argparse.ArgumentParser(description="FAISS retrieval testi")
    parser.add_argument(
        "--profile-code", default=None, help="Test edilecek profil kodu (yoksa tüm aktif)"
    )
    parser.add_argument("--limit", type=int, default=5, help="Profil başına maksimum sonuç")
    parser.add_argument("--faiss-path", default="storage/faiss", help="FAISS dizini")
    parser.add_argument("--collection", default="ekap_tender_chunks", help="Koleksiyon adı")
    args = parser.parse_args()

    faiss_index = Path(args.faiss_path) / f"{args.collection}.index"
    if not faiss_index.exists():
        print(f"FAISS indeksi bulunamadı: {faiss_index}")
        print("Önce 'python scripts/build_active_tenders_faiss.py' çalıştırın.")
        report = {
            "status": "index_missing",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "index_path": str(faiss_index),
        }
        with open(REPORTS / "faiss_retrieval_test_results.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return 2

    try:
        from app.company_profiles.isbak_profile import IsbakProfile, build_profile_semantic_text
        from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
        from app.config.isbak_rag_settings import get_isbak_rag_settings
        from app.indexing.embedder import BgeM3Embedder
        from app.retrieval.isbak_tender_retriever import IsbakTenderRetriever
        from app.vector_store.faiss_store import FaissVectorStore

        loader = IsbakProfileLoader()
        settings = get_isbak_rag_settings()
        active_profiles = loader.list_profiles(active_only=True)

        if args.profile_code:
            active_profiles = [
                p
                for p in active_profiles
                if p.get("profil_kodu", "").upper() == args.profile_code.upper()
            ]

        embedder = BgeM3Embedder()
        vector_store = FaissVectorStore(path=args.faiss_path, collection_name=args.collection)
        retriever = IsbakTenderRetriever(
            embedder=embedder, vector_store=vector_store, settings=settings
        )

        all_results = []
        ranking_rows = []

        for entry in active_profiles:
            code = entry["profil_kodu"].upper()
            try:
                profile_data = loader.load_profile(code)
                profile = IsbakProfile.model_validate(profile_data)
                query = build_profile_semantic_text(profile)

                results = retriever.retrieve(query)[: args.limit]

                profile_result = {
                    "profile_code": code,
                    "profile_name": profile.profil_adi,
                    "result_count": len(results),
                    "top_tenders": [
                        {
                            "ikn": r.ikn,
                            "tender_name": r.tender_name,
                            "final_score": r.scores.final,
                            "max_chunk": r.scores.max_chunk,
                            "section_diversity": r.scores.section_diversity,
                        }
                        for r in results
                    ],
                }
                all_results.append(profile_result)

                for r in results:
                    ranking_rows.append(
                        [
                            code,
                            r.ikn,
                            r.tender_name,
                            r.scores.final,
                            r.scores.max_chunk,
                            r.scores.top_chunks_mean,
                            r.scores.section_diversity,
                        ]
                    )
            except Exception as exc:
                all_results.append({"profile_code": code, "error": str(exc)})

        with open(REPORTS / "faiss_retrieval_test_results.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "profiles_tested": len(active_profiles),
                    "results": all_results,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        with open(REPORTS / "faiss_tender_ranking.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "profile_code",
                    "ikn",
                    "tender_name",
                    "final_score",
                    "max_chunk",
                    "top_chunks_mean",
                    "section_diversity",
                ]
            )
            w.writerows(ranking_rows)

        print(f"Test tamamlandı. {len(active_profiles)} profil, {len(ranking_rows)} sonuç.")

    except Exception as exc:
        print(f"HATA: {exc}")
        with open(REPORTS / "faiss_retrieval_test_results.json", "w", encoding="utf-8") as f:
            json.dump({"status": "failed", "error": str(exc)}, f, ensure_ascii=False, indent=2)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
