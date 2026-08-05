#!/usr/bin/env python
"""
scripts/build_active_tenders_faiss.py

Aktif ihaleleri PostgreSQL'den okuyarak FAISS indeksini oluşturur.
Bölüm farkındalıklı parçalama ve BGE-M3 gömme kullanır.

Kullanım:
  python scripts/build_active_tenders_faiss.py [--limit N] [--dry-run] [--recreate]

Çıktılar:
  storage/faiss/ekap_tender_chunks.index
  storage/faiss/ekap_tender_chunks_payloads.pkl
  reports/faiss_build_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPORTS = Path("reports")
REPORTS.mkdir(exist_ok=True)


def _check_env() -> dict[str, str]:
    """Çevre değişkenlerini kontrol et. Eksik varsa isim bazlı raporla."""
    required = ["DATABASE_HOST", "DATABASE_NAME", "DATABASE_USER", "DATABASE_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v, "")]
    return {"missing": missing, "ok": len(missing) == 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="Aktif ihaleleri FAISS'e indeksle")
    parser.add_argument("--limit", type=int, default=None, help="İşlenecek maksimum ihale sayısı")
    parser.add_argument("--dry-run", action="store_true", help="FAISS'e yazmadan test çalışması")
    parser.add_argument("--recreate", action="store_true", help="Mevcut indeksi sıfırdan oluştur")
    parser.add_argument("--start-offset", type=int, default=0, help="Başlangıç ofseti")
    parser.add_argument("--collection", default="ekap_tender_chunks", help="FAISS koleksiyon adı")
    parser.add_argument("--faiss-path", default="storage/faiss", help="FAISS depolama dizini")
    args = parser.parse_args()

    # Çevre kontrolü
    env_check = _check_env()
    if not env_check["ok"]:
        print("ORTAM BAĞIMLILIĞI NEDENİYLE DOĞRULANAMADI")
        print(f"Eksik değişkenler: {env_check['missing']}")
        report = {
            "status": "env_missing",
            "missing_vars": env_check["missing"],
            "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        }
        with open(REPORTS / "faiss_build_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return 2

    try:
        from app.config import get_settings
        from app.database.tender_repository import TenderRepository
        from app.indexing.active_tender_indexer import ActiveTenderIndexer
        from app.indexing.chunker import SectionAwareChunker
        from app.indexing.document_builder import TenderDocumentBuilder
        from app.indexing.embedder import BgeM3Embedder
        from app.vector_store.faiss_store import FaissVectorStore

        settings = get_settings()
        model_name = settings.embedding_model
        device = settings.embedding_device

        repository = TenderRepository()
        document_builder = TenderDocumentBuilder()
        chunker = SectionAwareChunker()

        embedder = None if args.dry_run else BgeM3Embedder(model_name=model_name, device=device)
        vector_store = (
            None
            if args.dry_run
            else FaissVectorStore(path=args.faiss_path, collection_name=args.collection)
        )

        indexer = ActiveTenderIndexer(
            repository=repository,
            document_builder=document_builder,
            chunker=chunker,
            embedder=embedder,
            vector_store=vector_store,
            continue_on_error=True,
        )

        print(f"[{datetime.now().isoformat()}] FAISS indeksleme başlıyor...")
        print(f"  Limit: {args.limit}, Dry-run: {args.dry_run}, Recreate: {args.recreate}")

        stats = indexer.run(
            limit=args.limit,
            start_offset=args.start_offset,
            recreate=args.recreate,
            dry_run=args.dry_run,
            model_name=model_name,
            device=device,
            collection_name=args.collection,
            faiss_path=args.faiss_path,
        )

        report = {
            "status": stats.status,
            "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
            "dry_run": args.dry_run,
            "active_snapshot_count": stats.active_snapshot_count,
            "selected_tender_count": stats.selected_tender_count,
            "processed_tender_count": stats.processed_tender_count,
            "failed_tender_count": stats.failed_tender_count,
            "chunk_count": stats.chunk_count,
            "indexed_point_count": stats.indexed_point_count,
            "faiss_total_count": stats.faiss_total_count,
            "elapsed_seconds": stats.elapsed_seconds,
        }

    except Exception as exc:
        import traceback

        traceback.print_exc()
        report = {
            "status": "failed",
            "error": str(exc),
            "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        }
        print(f"HATA: {exc}")
        with open(REPORTS / "faiss_build_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return 1

    with open(REPORTS / "faiss_build_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(
        f"[TAMAMLANDI] İşlenen: {report.get('processed_tender_count', 0)}, "
        f"Parça: {report.get('chunk_count', 0)}, "
        f"FAISS toplam: {report.get('faiss_total_count', 0)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
