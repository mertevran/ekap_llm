from __future__ import annotations

import argparse
from pathlib import Path

from app.database.tender_repository import TenderRepository
from app.indexing.active_tender_indexer import ActiveTenderIndexer
from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder
from app.indexing.embedder import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MODEL_CACHE,
    BgeM3Embedder,
)
from app.vector_store.qdrant_store import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_QDRANT_PATH,
    QdrantVectorStore,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aktif ihaleleri PostgreSQL'den okuyup belge ve parçalara "
            "dönüştürür, BGE-M3 ile vektörleştirir ve Qdrant'a kaydeder."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="İşlenecek en fazla aktif ihale sayısı.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Aktif ihale listesinin başından atlanacak kayıt sayısı.",
    )
    parser.add_argument(
        "--db-batch-size",
        type=int,
        default=25,
        help="PostgreSQL ayrıntı sorgusu grup büyüklüğü.",
    )
    parser.add_argument(
        "--embed-batch-size",
        type=int,
        default=8,
        help="BGE-M3 gömme vektörü grup büyüklüğü.",
    )
    parser.add_argument(
        "--qdrant-batch-size",
        type=int,
        default=64,
        help="Qdrant toplu kayıt büyüklüğü.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1800,
        help="Hedef parça uzunluğu, karakter.",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=200,
        help="Parçalar arası örtüşme, karakter.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="Gömme vektörü modeli.",
    )
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=DEFAULT_MODEL_CACHE,
        help="Model önbellek dizini.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Model çalışma aygıtı: cpu veya cuda.",
    )
    parser.add_argument(
        "--qdrant-path",
        type=Path,
        default=DEFAULT_QDRANT_PATH,
        help="Qdrant yerel veri dizini.",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help="Qdrant koleksiyon adı.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("outputs/active_tender_index_manifest.json"),
        help="İndeksleme ilerleme ve sonuç dosyası.",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Koleksiyonu silerek yalnızca güncel aktif ihalelerle yeniden kurar.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Model ve Qdrant kullanmadan belge/parça üretimini denetler.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Tek bir ihale hata verdiğinde kalan ihalelere devam eder.",
    )
    parser.add_argument(
        "--show-embedding-progress",
        action="store_true",
        help="BGE-M3 ilerleme çubuğunu gösterir.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    repository = TenderRepository()
    builder = TenderDocumentBuilder()
    chunker = SectionAwareChunker(
        chunk_size_chars=args.chunk_size,
        overlap_chars=args.overlap,
    )

    embedder = None
    vector_store = None

    try:
        if not args.dry_run:
            print("BGE-M3 modeli yükleniyor...")
            embedder = BgeM3Embedder(
                model_name=args.model,
                device=args.device,
                cache_folder=args.model_cache,
                batch_size=args.embed_batch_size,
                show_progress_bar=args.show_embedding_progress,
            )
            vector_store = QdrantVectorStore(
                path=args.qdrant_path,
                collection_name=args.collection,
            )

        indexer = ActiveTenderIndexer(
            repository=repository,
            document_builder=builder,
            chunker=chunker,
            embedder=embedder,
            vector_store=vector_store,
            database_batch_size=args.db_batch_size,
            qdrant_batch_size=args.qdrant_batch_size,
            manifest_path=args.manifest,
            continue_on_error=args.continue_on_error,
        )

        stats = indexer.run(
            limit=args.limit,
            start_offset=args.offset,
            recreate=args.recreate,
            dry_run=args.dry_run,
            model_name=args.model,
            device=args.device,
            collection_name=args.collection,
            qdrant_path=str(args.qdrant_path),
        )

        print("\nAktif ihale indeksleme tamamlandı")
        print("-" * 70)
        print("Durum                 :", stats.status)
        print("Aktif ihale anlık sayı:", stats.active_snapshot_count)
        print("Seçilen ihale         :", stats.selected_tender_count)
        print("İşlenen ihale         :", stats.processed_tender_count)
        print("Hatalı ihale          :", stats.failed_tender_count)
        print("Bölüm sayısı          :", stats.section_count)
        print("Parça sayısı          :", stats.chunk_count)
        print("Kaydedilen nokta      :", stats.indexed_point_count)
        print("Qdrant toplamı        :", stats.qdrant_total_count)
        print("Toplam süre, saniye   :", stats.elapsed_seconds)
        print("Sonuç dosyası         :", args.manifest)

    finally:
        if vector_store is not None:
            vector_store.close()


if __name__ == "__main__":
    main()
