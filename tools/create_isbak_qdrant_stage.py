from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()

FILES = {
    "app/indexing/isbak_tender_indexer.py": r'''
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.classification.isbak_tender_profile_classifier import (
    IsbakTenderProfileClassifier,
)
from app.database.tender_repository import TenderRepository
from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder
from app.indexing.embedder import BgeM3Embedder
from app.vector_store.qdrant_store import (
    QdrantVectorStore,
    VectorRecord,
    deterministic_point_id,
)


ALLOWED_STATUSES = {
    "guclu_eslesme",
    "kosullu_eslesme",
}


@dataclass
class IsbakIndexingStats:
    status: str = "running"
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None

    scanned_tender_count: int = 0
    candidate_tender_count: int = 0
    indexed_tender_count: int = 0
    failed_tender_count: int = 0

    section_count: int = 0
    chunk_count: int = 0
    indexed_point_count: int = 0
    qdrant_total_count: int = 0

    elapsed_seconds: float = 0.0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)


class IsbakTenderIndexer:
    def __init__(
        self,
        *,
        repository: TenderRepository,
        classifier: IsbakTenderProfileClassifier,
        document_builder: TenderDocumentBuilder,
        chunker: SectionAwareChunker,
        embedder: BgeM3Embedder | None,
        vector_store: QdrantVectorStore | None,
        database_batch_size: int = 25,
        qdrant_batch_size: int = 32,
        manifest_path: str | Path = (
            "outputs/isbak_tender_index_manifest.json"
        ),
        continue_on_error: bool = True,
    ) -> None:
        if database_batch_size <= 0:
            raise ValueError(
                "database_batch_size pozitif olmalıdır."
            )

        if qdrant_batch_size <= 0:
            raise ValueError(
                "qdrant_batch_size pozitif olmalıdır."
            )

        self.repository = repository
        self.classifier = classifier
        self.document_builder = document_builder
        self.chunker = chunker
        self.embedder = embedder
        self.vector_store = vector_store
        self.database_batch_size = database_batch_size
        self.qdrant_batch_size = qdrant_batch_size
        self.manifest_path = Path(manifest_path)
        self.continue_on_error = continue_on_error

    def run(
        self,
        *,
        scan_limit: int | None = None,
        candidate_limit: int | None = None,
        start_offset: int = 0,
        recreate: bool = False,
        dry_run: bool = True,
    ) -> IsbakIndexingStats:
        if scan_limit is not None and scan_limit <= 0:
            raise ValueError(
                "scan_limit pozitif olmalıdır."
            )

        if candidate_limit is not None and candidate_limit <= 0:
            raise ValueError(
                "candidate_limit pozitif olmalıdır."
            )

        if start_offset < 0:
            raise ValueError(
                "start_offset negatif olamaz."
            )

        if not dry_run and (
            self.embedder is None
            or self.vector_store is None
        ):
            raise ValueError(
                "Gerçek indeksleme için gömme modeli ve "
                "Qdrant bağlantısı gereklidir."
            )

        started = time.perf_counter()
        stats = IsbakIndexingStats()

        try:
            if not dry_run:
                assert self.embedder is not None
                assert self.vector_store is not None

                self.vector_store.ensure_collection(
                    vector_size=self.embedder.vector_size,
                    recreate=recreate,
                )

            stop_requested = False

            for batch_no, tenders in enumerate(
                self.repository.iter_active_tender_batches(
                    batch_size=self.database_batch_size,
                    limit=scan_limit,
                    start_offset=start_offset,
                ),
                start=1,
            ):
                batch_chunks: list[dict[str, Any]] = []

                for tender in tenders:
                    stats.scanned_tender_count += 1

                    try:
                        classification = self.classifier.classify(
                            tender
                        )

                        if (
                            classification.overall_status
                            not in ALLOWED_STATUSES
                        ):
                            continue

                        stats.candidate_tender_count += 1

                        top_score = (
                            classification.matches[0].raw_score
                            if classification.matches
                            else 0.0
                        )

                        stats.candidates.append(
                            {
                                "tender_id": (
                                    classification.tender_id
                                ),
                                "ikn": classification.ikn,
                                "title": classification.title,
                                "status": (
                                    classification.overall_status
                                ),
                                "primary_profile_code": (
                                    classification
                                    .primary_profile_code
                                ),
                                "profile_codes": (
                                    classification.profile_codes
                                ),
                                "review_profile_codes": (
                                    classification
                                    .review_profile_codes
                                ),
                                "top_score": top_score,
                            }
                        )

                        document = self.document_builder.build(
                            tender
                        )

                        chunks = self.chunker.chunk_document(
                            document
                        )

                        enriched_chunks = [
                            self._enrich_chunk(
                                chunk=chunk,
                                classification=classification,
                            )
                            for chunk in chunks
                        ]

                        stats.section_count += len(
                            document.get("sections", [])
                        )
                        stats.chunk_count += len(
                            enriched_chunks
                        )

                        if not dry_run:
                            assert self.vector_store is not None

                            self.vector_store.delete_by_tender_id(
                                classification.tender_id
                            )

                            batch_chunks.extend(
                                enriched_chunks
                            )

                        stats.indexed_tender_count += 1

                        if (
                            candidate_limit is not None
                            and stats.candidate_tender_count
                            >= candidate_limit
                        ):
                            stop_requested = True
                            break

                    except Exception as exc:
                        stats.failed_tender_count += 1
                        stats.failures.append(
                            {
                                "ikn": str(
                                    getattr(
                                        tender,
                                        "ikn",
                                        "-",
                                    )
                                ),
                                "error": str(exc),
                            }
                        )

                        if not self.continue_on_error:
                            raise

                if not dry_run and batch_chunks:
                    stats.indexed_point_count += (
                        self._embed_and_upsert(
                            batch_chunks
                        )
                    )

                stats.elapsed_seconds = round(
                    time.perf_counter() - started,
                    3,
                )

                self._write_manifest(
                    stats=stats,
                    extra={
                        "dry_run": dry_run,
                        "recreate": recreate,
                        "scan_limit": scan_limit,
                        "candidate_limit": candidate_limit,
                        "start_offset": start_offset,
                        "last_completed_batch": batch_no,
                    },
                )

                print(
                    f"Grup {batch_no} | "
                    f"taranan={stats.scanned_tender_count} | "
                    f"aday={stats.candidate_tender_count} | "
                    f"parça={stats.chunk_count} | "
                    f"Qdrant={stats.indexed_point_count}",
                    flush=True,
                )

                if stop_requested:
                    break

            if not dry_run:
                assert self.vector_store is not None
                stats.qdrant_total_count = (
                    self.vector_store.count()
                )

            stats.status = "completed"
            stats.finished_at = (
                datetime.now(timezone.utc).isoformat()
            )
            stats.elapsed_seconds = round(
                time.perf_counter() - started,
                3,
            )

            self._write_manifest(
                stats=stats,
                extra={
                    "dry_run": dry_run,
                    "recreate": recreate,
                    "scan_limit": scan_limit,
                    "candidate_limit": candidate_limit,
                    "start_offset": start_offset,
                },
            )

            return stats

        except Exception:
            stats.status = "failed"
            stats.finished_at = (
                datetime.now(timezone.utc).isoformat()
            )
            stats.elapsed_seconds = round(
                time.perf_counter() - started,
                3,
            )

            self._write_manifest(
                stats=stats,
                extra={
                    "dry_run": dry_run,
                    "recreate": recreate,
                    "scan_limit": scan_limit,
                    "candidate_limit": candidate_limit,
                    "start_offset": start_offset,
                },
            )

            raise

    @staticmethod
    def _enrich_chunk(
        *,
        chunk: dict[str, Any],
        classification: Any,
    ) -> dict[str, Any]:
        profile_scores = {
            match.profile_code: match.normalized_score
            for match in classification.matches
        }

        enriched = dict(chunk)
        metadata = dict(
            enriched.get("metadata", {})
        )

        profile_fields = {
            "primary_profile_code": (
                classification.primary_profile_code
            ),
            "profile_codes": (
                classification.profile_codes
            ),
            "review_profile_codes": (
                classification.review_profile_codes
            ),
            "evaluation_profile_codes": (
                classification.evaluation_profile_codes
            ),
            "profile_scores": profile_scores,
            "classification_status": (
                classification.overall_status
            ),
            "classifier_version": (
                classification.classifier_version
            ),
            "is_active": True,
        }

        enriched.update(profile_fields)
        metadata.update(profile_fields)
        enriched["metadata"] = metadata

        return enriched

    def _embed_and_upsert(
        self,
        chunks: list[dict[str, Any]],
    ) -> int:
        assert self.embedder is not None
        assert self.vector_store is not None

        total = 0
        embed_batch_size = self.embedder.batch_size

        for start in range(
            0,
            len(chunks),
            embed_batch_size,
        ):
            group = chunks[
                start:start + embed_batch_size
            ]

            texts = [
                self._embedding_text(chunk)
                for chunk in group
            ]

            vectors = self.embedder.embed(texts)

            if len(vectors) != len(group):
                raise RuntimeError(
                    "Gömme vektörü sayısı ile "
                    "parça sayısı uyuşmuyor."
                )

            records: list[VectorRecord] = []

            for chunk, vector in zip(
                group,
                vectors,
            ):
                payload = {
                    **chunk,
                    "embedding_model": (
                        self.embedder.model_name
                    ),
                    "indexed_at": (
                        datetime.now(
                            timezone.utc
                        ).isoformat()
                    ),
                }

                records.append(
                    VectorRecord(
                        point_id=deterministic_point_id(
                            "ekap_isbak_tender_chunk",
                            chunk["chunk_id"],
                        ),
                        vector=vector,
                        payload=payload,
                    )
                )

            total += self.vector_store.upsert_records(
                records,
                batch_size=self.qdrant_batch_size,
            )

        return total

    @staticmethod
    def _embedding_text(
        chunk: dict[str, Any],
    ) -> str:
        title = str(
            chunk.get("title", "")
        ).strip()

        text = str(
            chunk.get("text", "")
        ).strip()

        if title and title not in text[:200]:
            return f"{title}\n{text}"

        return text

    def _write_manifest(
        self,
        *,
        stats: IsbakIndexingStats,
        extra: dict[str, Any],
    ) -> None:
        self.manifest_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        data = {
            **asdict(stats),
            **extra,
        }

        temporary = self.manifest_path.with_suffix(
            self.manifest_path.suffix + ".tmp"
        )

        temporary.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temporary.replace(
            self.manifest_path
        )
''',

    "scripts/index_isbak_tenders.py": r'''
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
from app.indexing.isbak_tender_indexer import (
    IsbakTenderIndexer,
)
from app.vector_store.qdrant_store import (
    QdrantVectorStore,
)


DEFAULT_COLLECTION = (
    "ekap_isbak_tender_chunks_v1"
)

DEFAULT_QDRANT_PATH = (
    "storage/qdrant_isbak"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "İSBAK ile güçlü veya koşullu eşleşen "
            "aktif ihaleleri Qdrant'a indeksler."
        )
    )

    parser.add_argument(
        "--scan-limit",
        type=int,
        default=100,
    )

    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--database-batch-size",
        type=int,
        default=25,
    )

    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--qdrant-batch-size",
        type=int,
        default=32,
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1800,
    )

    parser.add_argument(
        "--overlap",
        type=int,
        default=200,
    )

    parser.add_argument(
        "--device",
        default="cpu",
    )

    parser.add_argument(
        "--model-name",
        default="BAAI/bge-m3",
    )

    parser.add_argument(
        "--collection-name",
        default=DEFAULT_COLLECTION,
    )

    parser.add_argument(
        "--qdrant-path",
        default=DEFAULT_QDRANT_PATH,
    )

    parser.add_argument(
        "--manifest",
        default=(
            "outputs/"
            "isbak_tender_index_manifest.json"
        ),
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Verilirse BGE-M3 ve Qdrant çalışır. "
            "Verilmezse yalnızca deneme çalışması yapılır."
        ),
    )

    parser.add_argument(
        "--recreate",
        action="store_true",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    loader = IsbakProfileLoader(
        "config/isbak"
    )

    errors = loader.validate_all()

    if errors:
        raise RuntimeError(
            "İSBAK profil doğrulaması başarısız:\n- "
            + "\n- ".join(errors)
        )

    repository = TenderRepository()

    classifier = (
        IsbakTenderProfileClassifier(
            loader
        )
    )

    document_builder = (
        TenderDocumentBuilder()
    )

    chunker = SectionAwareChunker(
        chunk_size_chars=args.chunk_size,
        overlap_chars=args.overlap,
    )

    embedder = None
    vector_store = None

    if args.write:
        embedder = BgeM3Embedder(
            model_name=args.model_name,
            device=args.device,
            batch_size=(
                args.embedding_batch_size
            ),
            show_progress_bar=True,
        )

        vector_store = QdrantVectorStore(
            path=args.qdrant_path,
            collection_name=(
                args.collection_name
            ),
        )

    indexer = IsbakTenderIndexer(
        repository=repository,
        classifier=classifier,
        document_builder=document_builder,
        chunker=chunker,
        embedder=embedder,
        vector_store=vector_store,
        database_batch_size=(
            args.database_batch_size
        ),
        qdrant_batch_size=(
            args.qdrant_batch_size
        ),
        manifest_path=args.manifest,
    )

    try:
        stats = indexer.run(
            scan_limit=args.scan_limit,
            candidate_limit=(
                args.candidate_limit
            ),
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
                    "scanned_tender_count": (
                        stats.scanned_tender_count
                    ),
                    "candidate_tender_count": (
                        stats.candidate_tender_count
                    ),
                    "indexed_tender_count": (
                        stats.indexed_tender_count
                    ),
                    "failed_tender_count": (
                        stats.failed_tender_count
                    ),
                    "chunk_count": (
                        stats.chunk_count
                    ),
                    "indexed_point_count": (
                        stats.indexed_point_count
                    ),
                    "qdrant_total_count": (
                        stats.qdrant_total_count
                    ),
                    "elapsed_seconds": (
                        stats.elapsed_seconds
                    ),
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
''',

    "scripts/inspect_isbak_qdrant.py": r'''
from __future__ import annotations

import argparse
import json

from app.vector_store.qdrant_store import (
    QdrantVectorStore,
)


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--qdrant-path",
        default="storage/qdrant_isbak",
    )

    parser.add_argument(
        "--collection-name",
        default="ekap_isbak_tender_chunks_v1",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    args = parser.parse_args()

    store = QdrantVectorStore(
        path=args.qdrant_path,
        collection_name=args.collection_name,
    )

    try:
        print(
            "Koleksiyon mevcut:",
            store.collection_exists(),
        )

        if not store.collection_exists():
            return 1

        print(
            "Toplam kayıt:",
            store.count(),
        )

        print()
        print("Örnek kayıtlar:")

        for item in store.scroll(
            limit=args.limit
        ):
            payload = item["payload"]

            print(
                json.dumps(
                    {
                        "id": item["id"],
                        "ikn": payload.get("ikn"),
                        "title": payload.get("title"),
                        "section_id": (
                            payload.get("section_id")
                        ),
                        "primary_profile_code": (
                            payload.get(
                                "primary_profile_code"
                            )
                        ),
                        "profile_codes": (
                            payload.get(
                                "profile_codes"
                            )
                        ),
                        "classification_status": (
                            payload.get(
                                "classification_status"
                            )
                        ),
                        "text_preview": str(
                            payload.get("text", "")
                        )[:180],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        return 0

    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
''',

    "tests/unit/test_isbak_tender_indexer.py": r'''
from types import SimpleNamespace

from app.indexing.isbak_tender_indexer import (
    ALLOWED_STATUSES,
    IsbakTenderIndexer,
)


def test_allowed_statuses_are_strict():
    assert ALLOWED_STATUSES == {
        "guclu_eslesme",
        "kosullu_eslesme",
    }


def test_enrich_chunk_adds_profile_fields():
    classification = SimpleNamespace(
        primary_profile_code="TEK-01",
        profile_codes=[
            "TEK-01",
            "OPS-02",
        ],
        review_profile_codes=[],
        evaluation_profile_codes=[
            "TEK-01",
            "OPS-02",
            "TEK-03",
        ],
        overall_status="guclu_eslesme",
        classifier_version=(
            "isbak_profile_classifier_v2_2"
        ),
        matches=[
            SimpleNamespace(
                profile_code="TEK-01",
                normalized_score=0.9,
            ),
            SimpleNamespace(
                profile_code="OPS-02",
                normalized_score=0.8,
            ),
        ],
    )

    chunk = {
        "chunk_id": "chunk-1",
        "metadata": {
            "section_type": "main",
        },
    }

    result = IsbakTenderIndexer._enrich_chunk(
        chunk=chunk,
        classification=classification,
    )

    assert (
        result["primary_profile_code"]
        == "TEK-01"
    )

    assert result["profile_codes"] == [
        "TEK-01",
        "OPS-02",
    ]

    assert (
        result["profile_scores"]["TEK-01"]
        == 0.9
    )

    assert (
        result["metadata"][
            "classification_status"
        ]
        == "guclu_eslesme"
    )


def test_embedding_text_includes_title():
    chunk = {
        "title": "Başlık",
        "text": "İhale açıklaması",
    }

    result = (
        IsbakTenderIndexer
        ._embedding_text(chunk)
    )

    assert result.startswith(
        "Başlık\n"
    )
''',
}


def main() -> int:
    for relative_path, content in FILES.items():
        destination = ROOT / relative_path
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination.write_text(
            content.lstrip(),
            encoding="utf-8",
        )

        print(
            "Oluşturuldu:",
            relative_path,
        )

    print()
    print(
        "İSBAK Qdrant aşaması dosyaları oluşturuldu."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
