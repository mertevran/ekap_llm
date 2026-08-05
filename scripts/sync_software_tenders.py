from __future__ import annotations

import argparse
from dataclasses import asdict
from pprint import pprint

from app.database.tender_repository import TenderRepository
from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder
from app.indexing.embedder import BgeM3Embedder
from app.indexing.index_state_repository import IndexStateRepository
from app.indexing.software_tender_sync import SoftwareTenderSynchronizer
from app.vector_store.qdrant_store import QdrantVectorStore

DEFAULT_COLLECTION = "ekap_software_tender_chunks_v1"
DEFAULT_MODEL = "BAAI/bge-m3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aktif yazılım ve bilişim ihalelerini PostgreSQL durum tablosu "
            "ile Qdrant arasında artımlı olarak eşitler."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="İşlenecek en fazla aktif ihale sayısı.",
    )
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="Aktif ihale listesinin başlangıç konumu.",
    )
    parser.add_argument(
        "--database-batch-size",
        type=int,
        default=25,
        help="Veritabanından bir grupta alınacak ihale sayısı.",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=8,
        help="Bir grupta gömme vektörü üretilecek parça sayısı.",
    )
    parser.add_argument(
        "--qdrant-batch-size",
        type=int,
        default=64,
        help="Qdrant'a bir grupta yazılacak kayıt sayısı.",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help="Qdrant koleksiyon adı.",
    )
    parser.add_argument(
        "--qdrant-path",
        default="storage/qdrant",
        help="Yerel Qdrant veri dizini.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Gömme vektörü modeli.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Model çalışma aygıtı. Örnek: cpu veya cuda.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Veritabanına ve Qdrant'a yazmadan akışı sınar. Bu kipte gerçek gömme modeli yüklenmez."
        ),
    )
    parser.add_argument(
        "--remove-missing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=("Tam taramada artık aktif olmayan ihalelerin Qdrant kayıtlarını kaldırır."),
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="İlk ihale hatasında çalışmayı durdurur.",
    )

    return parser


class DryRunEmbedder:
    def __init__(
        self,
        *,
        model_name: str,
        batch_size: int,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.vector_size = 1024


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit pozitif olmalıdır.")

    if args.start_offset < 0:
        parser.error("--start-offset negatif olamaz.")

    if args.database_batch_size <= 0:
        parser.error("--database-batch-size pozitif olmalıdır.")

    if args.embedding_batch_size <= 0:
        parser.error("--embedding-batch-size pozitif olmalıdır.")

    if args.qdrant_batch_size <= 0:
        parser.error("--qdrant-batch-size pozitif olmalıdır.")

    if args.dry_run:
        embedder = DryRunEmbedder(
            model_name=args.model,
            batch_size=args.embedding_batch_size,
        )
    else:
        embedder = BgeM3Embedder(
            model_name=args.model,
            device=args.device,
            batch_size=args.embedding_batch_size,
        )

    vector_store = QdrantVectorStore(
        path=args.qdrant_path,
        collection_name=args.collection,
    )

    synchronizer = SoftwareTenderSynchronizer(
        repository=TenderRepository(),
        state_repository=IndexStateRepository(),
        document_builder=TenderDocumentBuilder(),
        chunker=SectionAwareChunker(),
        embedder=embedder,
        vector_store=vector_store,
        database_batch_size=args.database_batch_size,
        qdrant_batch_size=args.qdrant_batch_size,
        continue_on_error=not args.stop_on_error,
    )

    try:
        stats = synchronizer.run(
            limit=args.limit,
            start_offset=args.start_offset,
            dry_run=args.dry_run,
            remove_missing=args.remove_missing,
        )
        pprint(asdict(stats))
    finally:
        vector_store.close()

    return 0 if stats.failed_tender_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
