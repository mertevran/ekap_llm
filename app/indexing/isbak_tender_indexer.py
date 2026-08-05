from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.classification.isbak_tender_profile_classifier import (
    IsbakTenderProfileClassifier,
)
from app.database.tender_repository import TenderRepository
from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder
from app.indexing.embedder import BgeM3Embedder
from app.indexing.index_state_repository import IndexStateRepository
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
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None

    scanned_tender_count: int = 0
    candidate_tender_count: int = 0
    indexed_tender_count: int = 0
    unchanged_tender_count: int = 0
    skipped_tender_count: int = 0
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
        state_repository: IndexStateRepository | None = None,
        database_batch_size: int = 25,
        qdrant_batch_size: int = 32,
        manifest_path: str | Path = "outputs/isbak_tender_index_manifest.json",
        continue_on_error: bool = True,
    ) -> None:
        if database_batch_size <= 0:
            raise ValueError("database_batch_size pozitif olmalıdır.")
        if qdrant_batch_size <= 0:
            raise ValueError("qdrant_batch_size pozitif olmalıdır.")

        self.repository = repository
        self.classifier = classifier
        self.document_builder = document_builder
        self.chunker = chunker
        self.embedder = embedder
        self.vector_store = vector_store
        self.state_repository = state_repository
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
            raise ValueError("scan_limit pozitif olmalıdır.")
        if candidate_limit is not None and candidate_limit <= 0:
            raise ValueError("candidate_limit pozitif olmalıdır.")
        if start_offset < 0:
            raise ValueError("start_offset negatif olamaz.")
        if not dry_run and (self.embedder is None or self.vector_store is None):
            raise ValueError("Gerçek indeksleme için gömme modeli ve Qdrant bağlantısı gereklidir.")
        if not dry_run and self.state_repository is None:
            raise ValueError("Gerçek indeksleme için indeks durum deposu gereklidir.")

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
                for tender in tenders:
                    stats.scanned_tender_count += 1
                    tender_id_for_error = str(getattr(tender, "id", "")).strip()

                    try:
                        classification = self.classifier.classify(tender)
                        tender_id = str(classification.tender_id)
                        tender_id_for_error = tender_id
                        document = self.document_builder.build(tender)
                        source_hash = self._source_hash(document)
                        top_score = self._top_score(classification)
                        evidence_score = int(round(top_score))
                        classifier_version = str(classification.classifier_version)
                        tender_updated_at = self._tender_updated_at(tender)
                        existing_state = None

                        if not dry_run:
                            assert self.state_repository is not None
                            existing_state = self.state_repository.get_by_tender_id(tender_id)

                        is_candidate = classification.overall_status in ALLOWED_STATUSES

                        if not is_candidate:
                            stats.skipped_tender_count += 1

                            if not dry_run:
                                assert self.state_repository is not None
                                assert self.vector_store is not None

                                if (
                                    existing_state is not None
                                    and existing_state.index_status == "indexed"
                                ):
                                    self.vector_store.delete_by_tender_id(tender_id)

                                self.state_repository.upsert_pending(
                                    tender_id=tender_id,
                                    ikn=classification.ikn,
                                    classification=classification.overall_status,
                                    evidence_score=evidence_score,
                                    source_hash=source_hash,
                                    classifier_version=classifier_version,
                                    tender_updated_at=tender_updated_at,
                                )
                                self.state_repository.mark_skipped(
                                    tender_id=tender_id,
                                    classification=classification.overall_status,
                                    evidence_score=evidence_score,
                                    source_hash=source_hash,
                                    classifier_version=classifier_version,
                                    tender_updated_at=tender_updated_at,
                                )
                            continue

                        stats.candidate_tender_count += 1
                        stats.candidates.append(
                            {
                                "tender_id": tender_id,
                                "ikn": classification.ikn,
                                "title": classification.title,
                                "status": classification.overall_status,
                                "primary_profile_code": (classification.primary_profile_code),
                                "profile_codes": classification.profile_codes,
                                "review_profile_codes": (classification.review_profile_codes),
                                "top_score": top_score,
                            }
                        )

                        unchanged = (
                            not recreate
                            and not dry_run
                            and existing_state is not None
                            and existing_state.index_status == "indexed"
                            and existing_state.source_hash == source_hash
                            and existing_state.classifier_version == classifier_version
                            and existing_state.embedding_model == self.embedder.model_name
                            and existing_state.vector_collection
                            == self.vector_store.collection_name
                        )

                        if unchanged:
                            assert self.state_repository is not None
                            self.state_repository.touch_seen([tender_id])
                            stats.unchanged_tender_count += 1
                        else:
                            chunks = self.chunker.chunk_document(document)
                            enriched_chunks = [
                                self._enrich_chunk(
                                    chunk=chunk,
                                    classification=classification,
                                    source_hash=source_hash,
                                )
                                for chunk in chunks
                            ]

                            stats.section_count += len(document.get("sections", []))
                            stats.chunk_count += len(enriched_chunks)

                            if not dry_run:
                                assert self.state_repository is not None
                                assert self.vector_store is not None
                                assert self.embedder is not None

                                self.state_repository.upsert_pending(
                                    tender_id=tender_id,
                                    ikn=classification.ikn,
                                    classification=classification.overall_status,
                                    evidence_score=evidence_score,
                                    source_hash=source_hash,
                                    classifier_version=classifier_version,
                                    tender_updated_at=tender_updated_at,
                                )
                                self.vector_store.delete_by_tender_id(tender_id)
                                indexed_points = self._embed_and_upsert(enriched_chunks)
                                self.state_repository.mark_indexed(
                                    tender_id=tender_id,
                                    chunk_count=len(enriched_chunks),
                                    embedding_model=self.embedder.model_name,
                                    vector_collection=(self.vector_store.collection_name),
                                )
                                stats.indexed_point_count += indexed_points

                            stats.indexed_tender_count += 1

                        if (
                            candidate_limit is not None
                            and stats.candidate_tender_count >= candidate_limit
                        ):
                            stop_requested = True
                            break

                    except Exception as exc:
                        stats.failed_tender_count += 1
                        stats.failures.append(
                            {
                                "ikn": str(getattr(tender, "ikn", "-")),
                                "error": str(exc),
                            }
                        )

                        if (
                            not dry_run
                            and self.state_repository is not None
                            and tender_id_for_error
                        ):
                            try:
                                self.state_repository.mark_failed(
                                    tender_id=tender_id_for_error,
                                    error_message=str(exc),
                                )
                            except Exception:
                                pass

                        if not self.continue_on_error:
                            raise

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
                    f"yeni={stats.indexed_tender_count} | "
                    f"değişmedi={stats.unchanged_tender_count} | "
                    f"atlandı={stats.skipped_tender_count} | "
                    f"parça={stats.chunk_count} | "
                    f"Qdrant={stats.indexed_point_count}",
                    flush=True,
                )

                if stop_requested:
                    break

            if not dry_run:
                assert self.vector_store is not None
                stats.qdrant_total_count = self.vector_store.count()

            stats.status = "completed"
            stats.finished_at = datetime.now(UTC).isoformat()
            stats.elapsed_seconds = round(time.perf_counter() - started, 3)
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
            stats.finished_at = datetime.now(UTC).isoformat()
            stats.elapsed_seconds = round(time.perf_counter() - started, 3)
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
    def _source_hash(document: dict[str, Any]) -> str:
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _top_score(classification: Any) -> float:
        if not classification.matches:
            return 0.0
        return float(classification.matches[0].raw_score)

    @staticmethod
    def _tender_updated_at(tender: Any) -> datetime | None:
        for attribute in (
            "updated_at",
            "last_updated_at",
            "modified_at",
            "guncellenme_tarihi",
        ):
            value = getattr(tender, attribute, None)
            if isinstance(value, datetime):
                return value
        return None

    @staticmethod
    def _enrich_chunk(
        *,
        chunk: dict[str, Any],
        classification: Any,
        source_hash: str,
    ) -> dict[str, Any]:
        profile_scores = {
            match.profile_code: match.normalized_score for match in classification.matches
        }

        enriched = dict(chunk)
        metadata = dict(enriched.get("metadata", {}))
        profile_fields = {
            "primary_profile_code": classification.primary_profile_code,
            "profile_codes": classification.profile_codes,
            "review_profile_codes": classification.review_profile_codes,
            "evaluation_profile_codes": (classification.evaluation_profile_codes),
            "profile_scores": profile_scores,
            "classification_status": classification.overall_status,
            "classifier_version": classification.classifier_version,
            "source_hash": source_hash,
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

        for start in range(0, len(chunks), embed_batch_size):
            group = chunks[start : start + embed_batch_size]
            texts = [self._embedding_text(chunk) for chunk in group]
            vectors = self.embedder.embed(texts)

            if len(vectors) != len(group):
                raise RuntimeError("Gömme vektörü sayısı ile parça sayısı uyuşmuyor.")

            records: list[VectorRecord] = []
            for chunk, vector in zip(group, vectors):
                payload = {
                    **chunk,
                    "embedding_model": self.embedder.model_name,
                    "indexed_at": datetime.now(UTC).isoformat(),
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
    def _embedding_text(chunk: dict[str, Any]) -> str:
        title = str(chunk.get("title", "")).strip()
        text = str(chunk.get("text", "")).strip()
        if title and title not in text[:200]:
            return f"{title}\n{text}"
        return text

    def _write_manifest(
        self,
        *,
        stats: IsbakIndexingStats,
        extra: dict[str, Any],
    ) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        data = {**asdict(stats), **extra}
        temporary = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)
