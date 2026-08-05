from __future__ import annotations

import argparse
import json

from app.classification.isbak_tender_profile_classifier import (
    IsbakTenderProfileClassifier,
)
from app.company_profiles import IsbakProfileLoader
from app.database.tender_repository import TenderRepository
from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder
from app.indexing.embedder import BgeM3Embedder
from app.indexing.index_state_repository import IndexStateRepository
from app.indexing.isbak_tender_indexer import IsbakTenderIndexer
from app.vector_store.qdrant_store import QdrantVectorStore

DEFAULT_COLLECTION = "ekap_isbak_tender_chunks_v1"
DEFAULT_QDRANT_PATH = "storage/qdrant_isbak"
DEFAULT_STATE_TABLE = "llm_rag.isbak_tender_index_state"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("İSBAK ile güçlü veya koşullu eşleşen aktif ihaleleri Qdrant'a indeksler.")
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=None,
        help=("Taranacak azami aktif ihale sayısı. Belirtilmezse bütün aktif ihaleler taranır."),
    )
    parser.add_argument("--candidate-limit", type=int, default=None)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument("--database-batch-size", type=int, default=25)
    parser.add_argument("--embedding-batch-size", type=int, default=4)
    parser.add_argument("--qdrant-batch-size", type=int, default=32)
    parser.add_argument("--chunk-size", type=int, default=1800)
    parser.add_argument("--overlap", type=int, default=200)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--model-name", default="BAAI/bge-m3")
    parser.add_argument("--collection-name", default=DEFAULT_COLLECTION)
    parser.add_argument("--qdrant-path", default=DEFAULT_QDRANT_PATH)
    parser.add_argument("--state-table", default=DEFAULT_STATE_TABLE)
    parser.add_argument(
        "--manifest",
        default="outputs/isbak_tender_index_manifest.json",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Verilirse BGE-M3, Qdrant ve durum tablosu çalışır. "
            "Verilmezse yalnızca deneme çalışması yapılır."
        ),
    )
    parser.add_argument("--recreate", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    loader = IsbakProfileLoader("config/isbak")
    errors = loader.validate_all()
    if errors:
        raise RuntimeError("İSBAK profil doğrulaması başarısız:\n- " + "\n- ".join(errors))

    repository = TenderRepository()
    classifier = IsbakTenderProfileClassifier(loader)
    document_builder = TenderDocumentBuilder()
    chunker = SectionAwareChunker(
        chunk_size_chars=args.chunk_size,
        overlap_chars=args.overlap,
    )

    embedder = None
    vector_store = None
    state_repository = None

    if args.write:
        embedder = BgeM3Embedder(
            model_name=args.model_name,
            device=args.device,
            batch_size=args.embedding_batch_size,
            show_progress_bar=True,
        )
        vector_store = QdrantVectorStore(
            path=args.qdrant_path,
            collection_name=args.collection_name,
        )
        state_repository = IndexStateRepository(
            table_name=args.state_table,
        )

    indexer = IsbakTenderIndexer(
        repository=repository,
        classifier=classifier,
        document_builder=document_builder,
        chunker=chunker,
        embedder=embedder,
        vector_store=vector_store,
        state_repository=state_repository,
        database_batch_size=args.database_batch_size,
        qdrant_batch_size=args.qdrant_batch_size,
        manifest_path=args.manifest,
    )

    try:
        stats = indexer.run(
            scan_limit=args.scan_limit,
            candidate_limit=args.candidate_limit,
            start_offset=args.start_offset,
            recreate=args.recreate,
            dry_run=not args.write,
        )

        print()
        print("=" * 72)
        print("İSBAK QDRANT İNDEKSLEME SONUCU")
        print("=" * 72)
        print(
            json.dumps(
                {
                    "status": stats.status,
                    "scanned_tender_count": stats.scanned_tender_count,
                    "candidate_tender_count": stats.candidate_tender_count,
                    "indexed_tender_count": stats.indexed_tender_count,
                    "unchanged_tender_count": stats.unchanged_tender_count,
                    "skipped_tender_count": stats.skipped_tender_count,
                    "failed_tender_count": stats.failed_tender_count,
                    "chunk_count": stats.chunk_count,
                    "indexed_point_count": stats.indexed_point_count,
                    "qdrant_total_count": stats.qdrant_total_count,
                    "elapsed_seconds": stats.elapsed_seconds,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    finally:
        if vector_store is not None:
            vector_store.close()


if __name__ == "__main__":
    raise SystemExit(main())
