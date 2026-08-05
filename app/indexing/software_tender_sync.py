from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.classification.software_tender_classifier import (
    CLASSIFIER_VERSION,
    classify_tender,
)
from app.database.tender_repository import TenderRepository
from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder
from app.indexing.embedder import BgeM3Embedder
from app.indexing.index_state_repository import IndexStateRepository
from app.indexing.tender_source_hash import calculate_tender_source_hash
from app.vector_store.qdrant_store import (
    QdrantVectorStore,
    VectorRecord,
    deterministic_point_id,
)

DEFAULT_INDEXABLE_CLASSIFICATIONS = frozenset(
    {
        "doğrudan_uygun",
        "koşullu_uygun",
        "inceleme_gerekli",
    }
)


@dataclass
class SoftwareTenderSyncStats:
    status: str = "running"
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None

    scanned_count: int = 0
    unchanged_count: int = 0
    indexed_tender_count: int = 0
    indexed_chunk_count: int = 0
    skipped_tender_count: int = 0
    deleted_tender_count: int = 0
    failed_tender_count: int = 0

    failures: list[dict[str, str]] = field(default_factory=list)


class SoftwareTenderSynchronizer:
    """Yazılım/bilişim ihalelerini artımlı olarak Qdrant ile eşitler."""

    def __init__(
        self,
        *,
        repository: TenderRepository,
        state_repository: IndexStateRepository,
        document_builder: TenderDocumentBuilder,
        chunker: SectionAwareChunker,
        embedder: BgeM3Embedder,
        vector_store: QdrantVectorStore,
        database_batch_size: int = 25,
        qdrant_batch_size: int = 64,
        indexable_classifications: Iterable[str] = (DEFAULT_INDEXABLE_CLASSIFICATIONS),
        continue_on_error: bool = True,
    ) -> None:
        if database_batch_size <= 0:
            raise ValueError("database_batch_size pozitif olmalıdır.")

        if qdrant_batch_size <= 0:
            raise ValueError("qdrant_batch_size pozitif olmalıdır.")

        normalized_classifications = frozenset(
            str(value).strip() for value in indexable_classifications if str(value).strip()
        )

        if not normalized_classifications:
            raise ValueError("En az bir indekslenebilir sınıflandırma belirtilmelidir.")

        self.repository = repository
        self.state_repository = state_repository
        self.document_builder = document_builder
        self.chunker = chunker
        self.embedder = embedder
        self.vector_store = vector_store
        self.database_batch_size = database_batch_size
        self.qdrant_batch_size = qdrant_batch_size
        self.indexable_classifications = normalized_classifications
        self.continue_on_error = continue_on_error

    def run(
        self,
        *,
        limit: int | None = None,
        start_offset: int = 0,
        dry_run: bool = False,
        remove_missing: bool = True,
    ) -> SoftwareTenderSyncStats:
        if limit is not None and limit <= 0:
            raise ValueError("limit pozitif olmalıdır.")

        if start_offset < 0:
            raise ValueError("start_offset negatif olamaz.")

        stats = SoftwareTenderSyncStats()
        active_tender_ids: set[str] = set()

        if not dry_run:
            self.vector_store.ensure_collection(
                vector_size=self.embedder.vector_size,
                recreate=False,
            )

        try:
            for tenders in self.repository.iter_active_tender_batches(
                batch_size=self.database_batch_size,
                limit=limit,
                start_offset=start_offset,
            ):
                tender_ids = [str(getattr(tender, "id")) for tender in tenders]
                active_tender_ids.update(tender_ids)

                existing_states = self.state_repository.get_many_by_tender_ids(tender_ids)

                for tender in tenders:
                    stats.scanned_count += 1
                    tender_id = str(getattr(tender, "id"))
                    ikn = str(getattr(tender, "ikn"))

                    try:
                        result = classify_tender(tender)

                        source_hash = calculate_tender_source_hash(
                            tender,
                            classifier_version=CLASSIFIER_VERSION,
                        )

                        existing = existing_states.get(tender_id)

                        if self._is_unchanged(
                            existing=existing,
                            source_hash=source_hash,
                            classification=result.classification,
                        ):
                            stats.unchanged_count += 1

                            if not dry_run:
                                self.state_repository.touch_seen([tender_id])
                            continue

                        if result.classification not in (self.indexable_classifications):
                            if not dry_run:
                                if existing is not None and existing.index_status == "indexed":
                                    self.vector_store.delete_by_tender_id(tender_id)

                                self.state_repository.upsert_pending(
                                    tender_id=tender_id,
                                    ikn=ikn,
                                    classification=result.classification,
                                    evidence_score=result.evidence_score,
                                    source_hash=source_hash,
                                    classifier_version=(CLASSIFIER_VERSION),
                                    tender_updated_at=getattr(
                                        tender,
                                        "updated_at",
                                        None,
                                    ),
                                )

                                self.state_repository.mark_skipped(
                                    tender_id=tender_id,
                                    classification=result.classification,
                                    evidence_score=result.evidence_score,
                                    source_hash=source_hash,
                                    classifier_version=(CLASSIFIER_VERSION),
                                    tender_updated_at=getattr(
                                        tender,
                                        "updated_at",
                                        None,
                                    ),
                                )

                            stats.skipped_tender_count += 1
                            continue

                        document = self.document_builder.build(tender)
                        chunks = self.chunker.chunk_document(document)

                        if dry_run:
                            stats.indexed_tender_count += 1
                            stats.indexed_chunk_count += len(chunks)
                            continue

                        self.state_repository.upsert_pending(
                            tender_id=tender_id,
                            ikn=ikn,
                            classification=result.classification,
                            evidence_score=result.evidence_score,
                            source_hash=source_hash,
                            classifier_version=CLASSIFIER_VERSION,
                            tender_updated_at=getattr(
                                tender,
                                "updated_at",
                                None,
                            ),
                        )

                        # Aynı ihalenin eski parça kayıtlarını temizle.
                        self.vector_store.delete_by_tender_id(tender_id)

                        indexed_count = self._embed_and_upsert(chunks)

                        self.state_repository.mark_indexed(
                            tender_id=tender_id,
                            chunk_count=indexed_count,
                            embedding_model=self.embedder.model_name,
                            vector_collection=(self.vector_store.collection_name),
                        )

                        stats.indexed_tender_count += 1
                        stats.indexed_chunk_count += indexed_count

                    except Exception as exc:
                        stats.failed_tender_count += 1
                        stats.failures.append(
                            {
                                "tender_id": tender_id,
                                "ikn": ikn,
                                "error": str(exc),
                            }
                        )

                        if not dry_run:
                            existing = existing_states.get(tender_id)

                            if existing is not None:
                                self.state_repository.mark_failed(
                                    tender_id=tender_id,
                                    error_message=str(exc),
                                )

                        if not self.continue_on_error:
                            raise

            # Tam tarama yapılmadığında eksik kayıt silme güvenli değildir.
            full_snapshot = limit is None and start_offset == 0

            if remove_missing and full_snapshot and not dry_run:
                missing_states = self.state_repository.find_missing_from_active_snapshot(
                    active_tender_ids
                )

                for state in missing_states:
                    if state.index_status == "indexed":
                        self.vector_store.delete_by_tender_id(state.tender_id)

                    self.state_repository.mark_deleted(
                        tender_id=state.tender_id,
                        is_active=False,
                    )
                    stats.deleted_tender_count += 1

            stats.status = "completed"
            stats.finished_at = datetime.now(UTC).isoformat()
            return stats

        except Exception:
            stats.status = "failed"
            stats.finished_at = datetime.now(UTC).isoformat()
            raise

    @staticmethod
    def _is_unchanged(
        *,
        existing: Any,
        source_hash: str,
        classification: str,
    ) -> bool:
        if existing is None:
            return False

        if existing.source_hash != source_hash:
            return False

        if existing.classifier_version != CLASSIFIER_VERSION:
            return False

        if existing.classification != classification:
            return False

        if existing.index_status not in {
            "indexed",
            "skipped",
        }:
            return False

        return True

    def _embed_and_upsert(
        self,
        chunks: list[dict[str, Any]],
    ) -> int:
        total = 0
        embed_batch_size = self.embedder.batch_size

        for start in range(
            0,
            len(chunks),
            embed_batch_size,
        ):
            group = chunks[start : start + embed_batch_size]
            texts = [self._embedding_text(chunk) for chunk in group]
            vectors = self.embedder.embed(texts)

            if len(vectors) != len(group):
                raise RuntimeError("Gömme vektörü sayısı parça sayısıyla uyuşmuyor.")

            records: list[VectorRecord] = []

            for chunk, vector in zip(group, vectors):
                payload = {
                    **chunk,
                    "embedding_model": self.embedder.model_name,
                    "classifier_version": CLASSIFIER_VERSION,
                    "indexed_at": datetime.now(UTC).isoformat(),
                }

                records.append(
                    VectorRecord(
                        point_id=deterministic_point_id(
                            "ekap_software_tender_chunk",
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
        title = str(chunk.get("title", "")).strip()
        text = str(chunk.get("text", "")).strip()

        if title and title not in text[:200]:
            return f"{title}\n{text}"

        return text


__all__ = [
    "DEFAULT_INDEXABLE_CLASSIFICATIONS",
    "SoftwareTenderSynchronizer",
    "SoftwareTenderSyncStats",
]
