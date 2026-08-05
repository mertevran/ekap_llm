from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.database.tender_repository import TenderRepository
from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder
from app.indexing.embedder import BgeM3Embedder
from app.indexing.hash_generator import generate_source_hash
from app.indexing.index_state_repository import IndexStateRepository
from app.vector_store import (
    FaissVectorStore,
    VectorRecord,
    deterministic_point_id,
)
from app.vector_store.faiss_cache import FaissVectorCache

INDEX_VERSION = "1.0"


def is_isbak_tender(idare_adi: str | None) -> bool:
    if not idare_adi:
        return False
    norm = unicodedata.normalize("NFKC", idare_adi)
    norm = norm.lower()
    norm = (
        norm.replace("a.ş.", "")
        .replace("aş", "")
        .replace("anonim şirketi", "")
        .replace("sanayi ve ticaret", "")
    )
    norm = re.sub(r"[^\w\s]", "", norm)
    norm = " ".join(norm.split())

    if (
        "isbak" in norm
        or norm == "istanbul bilisim ve akilli kent teknolojileri"
        or norm == "istanbul bilişim ve akıllı kent teknolojileri"
    ):
        return True
    return False


def is_tender_empty(tender: Any) -> bool:
    has_adi = bool(tender.adi and tender.adi.strip())
    has_kapsam = bool(tender.kapsam and tender.kapsam.strip())
    has_icerik = any(a.icerik and a.icerik.strip() for a in tender.announcements)
    has_ozellik = any(c.ozellik and c.ozellik.strip() for c in tender.characteristics)
    has_okas = any(o.ad and o.ad.strip() for o in tender.okas_codes)
    return not (has_adi or has_kapsam or has_icerik or has_ozellik or has_okas)


@dataclass
class IndexingStats:
    status: str = "running"
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    active_snapshot_count: int = 0
    selected_tender_count: int = 0
    processed_tender_count: int = 0
    failed_tender_count: int = 0
    section_count: int = 0
    chunk_count: int = 0
    indexed_point_count: int = 0
    faiss_total_count: int = 0
    elapsed_seconds: float = 0.0
    failures: list[dict[str, str]] = field(default_factory=list)


class ActiveTenderIndexer:
    def __init__(
        self,
        *,
        repository: TenderRepository,
        document_builder: TenderDocumentBuilder,
        chunker: SectionAwareChunker,
        embedder: BgeM3Embedder | None,
        vector_store: FaissVectorStore | None,
        database_batch_size: int = 25,
        faiss_batch_size: int = 64,
        manifest_path: str | Path = ("outputs/active_tender_index_manifest.json"),
        continue_on_error: bool = False,
    ) -> None:
        if database_batch_size <= 0:
            raise ValueError("database_batch_size pozitif olmalıdır.")
        if faiss_batch_size <= 0:
            raise ValueError("faiss_batch_size pozitif olmalıdır.")

        self.repository = repository
        self.document_builder = document_builder
        self.chunker = chunker
        self.embedder = embedder
        self.vector_store = vector_store
        self.database_batch_size = database_batch_size
        self.faiss_batch_size = faiss_batch_size
        self.manifest_path = Path(manifest_path)
        self.continue_on_error = continue_on_error

    def run(
        self,
        *,
        limit: int | None = None,
        start_offset: int = 0,
        recreate: bool = False,
        dry_run: bool = False,
        model_name: str = "BAAI/bge-m3",
        device: str = "cpu",
        collection_name: str = "ekap_tender_chunks",
        faiss_path: str = "storage/faiss",
    ) -> IndexingStats:
        if limit is not None and limit <= 0:
            raise ValueError("limit pozitif olmalıdır.")
        if start_offset < 0:
            raise ValueError("start_offset negatif olamaz.")
        if not dry_run and (self.embedder is None or self.vector_store is None):
            raise ValueError("Gerçek indeksleme için embedder ve vector_store gereklidir.")

        started = time.perf_counter()
        stats = IndexingStats()

        state_repo = IndexStateRepository()
        vector_cache = FaissVectorCache()
        successful_tenders_to_mark = []
        all_records_for_faiss: list[VectorRecord] = []

        try:
            active_snapshot_count = self.repository.count_active_tenders()
            stats.active_snapshot_count = active_snapshot_count

            selected_count = max(
                0,
                active_snapshot_count - start_offset,
            )
            if limit is not None:
                selected_count = min(selected_count, limit)
            stats.selected_tender_count = selected_count

            self._write_manifest(
                stats=stats,
                extra={
                    "dry_run": dry_run,
                    "recreate": recreate,
                    "model_name": model_name,
                    "device": device,
                    "collection_name": collection_name,
                    "faiss_path": faiss_path,
                    "database_batch_size": self.database_batch_size,
                    "faiss_batch_size": self.faiss_batch_size,
                    "chunk_size_chars": self.chunker.chunk_target_chars,
                    "overlap_chars": self.chunker.chunk_overlap_chars,
                    "start_offset": start_offset,
                    "limit": limit,
                },
            )

            batch_number = 0

            for tenders in self.repository.iter_active_tender_batches(
                batch_size=self.database_batch_size,
                limit=limit,
                start_offset=start_offset,
            ):
                batch_number += 1

                for tender in tenders:
                    source_hash = generate_source_hash(tender)
                    state = state_repo.get_by_tender_id(tender.id)

                    if is_isbak_tender(tender.idare_adi):
                        if not dry_run:
                            state_repo.mark_skipped(
                                tender_id=tender.id,
                                classification=state.classification if state else None,
                                evidence_score=0,
                                source_hash=source_hash,
                                classifier_version="1.0",
                                tender_updated_at=tender.updated_at,
                                skip_reason="skipped_own_tender",
                            )
                        self._log_exclusion(
                            tender, "reports/excluded_own_tenders.csv", "own_authority_tender"
                        )
                        continue

                    if is_tender_empty(tender):
                        if not dry_run:
                            state_repo.mark_skipped(
                                tender_id=tender.id,
                                classification=state.classification if state else None,
                                evidence_score=0,
                                source_hash=source_hash,
                                classifier_version="1.0",
                                tender_updated_at=tender.updated_at,
                                skip_reason="skipped_empty",
                            )
                        self._log_exclusion(
                            tender, "reports/excluded_empty_tenders.csv", "empty_content"
                        )
                        continue

                    needs_processing = True
                    if (
                        not recreate
                        and state
                        and state.index_status == "indexed"
                        and state.source_hash == source_hash
                    ):
                        if (
                            state.chunking_version == self.chunker.chunking_version
                            and state.embedding_model == model_name
                            and state.embedding_version == "1.0"
                            and state.index_version == INDEX_VERSION
                            and state.cache_version == "1.0"
                        ):
                            cache_data = vector_cache.load(tender.id)
                            if cache_data:
                                manifest, chunks, vectors = cache_data
                                if len(chunks) == len(vectors):
                                    for chunk, vector in zip(chunks, vectors):
                                        payload = {
                                            **chunk,
                                            "embedding_model": model_name,
                                            "indexed_at": state.indexed_at.isoformat()
                                            if state.indexed_at
                                            else datetime.now(UTC).isoformat(),
                                        }
                                        all_records_for_faiss.append(
                                            VectorRecord(
                                                point_id=deterministic_point_id(
                                                    "ekap_tender_chunk", chunk["chunk_id"]
                                                ),
                                                vector=vector.tolist(),
                                                payload=payload,
                                            )
                                        )
                                    stats.processed_tender_count += 1
                                    stats.chunk_count += len(chunks)
                                    needs_processing = False

                    if needs_processing:
                        try:
                            if not dry_run:
                                state_repo.upsert_pending(
                                    tender_id=tender.id,
                                    ikn=tender.ikn,
                                    classification=state.classification if state else None,
                                    evidence_score=0,
                                    source_hash=source_hash,
                                    classifier_version="1.0",
                                    tender_updated_at=tender.updated_at,
                                    chunking_version=self.chunker.chunking_version,
                                    embedding_model=model_name,
                                    embedding_version="1.0",
                                    vector_backend="faiss",
                                    index_version=INDEX_VERSION,
                                    metadata_hash="",
                                    cache_version="1.0",
                                )
                                state_repo.mark_processing(tender.id)

                            document = self.document_builder.build(tender)
                            chunks = self.chunker.chunk_document(document)

                            vectors = []
                            if chunks and not dry_run:
                                assert self.embedder is not None
                                texts = [self._embedding_text(chunk) for chunk in chunks]
                                vectors = self.embedder.embed(texts)

                                vector_cache.save(
                                    tender_id=tender.id,
                                    ikn=tender.ikn,
                                    source_hash=source_hash,
                                    chunking_version=self.chunker.chunking_version,
                                    embedding_model=model_name,
                                    embedding_version="1.0",
                                    vector_dimension=self.embedder.vector_size,
                                    chunks=chunks,
                                    vectors=vectors,
                                )

                            successful_tenders_to_mark.append(
                                (tender.id, len(chunks), model_name, collection_name)
                            )

                            for chunk, vector in zip(chunks, vectors):
                                payload = {
                                    **chunk,
                                    "embedding_model": model_name,
                                    "indexed_at": datetime.now(UTC).isoformat(),
                                }
                                all_records_for_faiss.append(
                                    VectorRecord(
                                        point_id=deterministic_point_id(
                                            "ekap_tender_chunk", chunk["chunk_id"]
                                        ),
                                        vector=vector
                                        if isinstance(vector, list)
                                        else vector.tolist(),
                                        payload=payload,
                                    )
                                )

                            stats.processed_tender_count += 1
                            stats.chunk_count += len(chunks)
                        except Exception as exc:
                            import traceback

                            print("İhale indeksleme hatası:", tender.ikn)
                            traceback.print_exc()
                            if not dry_run:
                                state_repo.mark_failed(tender_id=tender.id, error_message=str(exc))
                            stats.failed_tender_count += 1
                            if not self.continue_on_error:
                                raise

                stats.elapsed_seconds = round(time.perf_counter() - started, 3)
                self._write_manifest(
                    stats=stats,
                    extra={
                        "dry_run": dry_run,
                        "recreate": recreate,
                        "model_name": model_name,
                        "device": device,
                        "collection_name": collection_name,
                        "faiss_path": faiss_path,
                        "database_batch_size": self.database_batch_size,
                        "faiss_batch_size": self.faiss_batch_size,
                        "chunk_size_chars": self.chunker.chunk_target_chars,
                        "overlap_chars": self.chunker.chunk_overlap_chars,
                        "start_offset": start_offset,
                        "limit": limit,
                        "last_completed_batch": batch_number,
                    },
                )
                print(
                    "İşlenen ihale: "
                    f"{stats.processed_tender_count}/{stats.selected_tender_count} | "
                    f"Parça: {stats.chunk_count}"
                )

            if not dry_run:
                assert self.vector_store is not None
                if all_records_for_faiss:
                    self.vector_store.ensure_collection(
                        vector_size=self.embedder.vector_size,
                        recreate=recreate,
                    )
                    stats.indexed_point_count = self.vector_store.upsert_records(
                        all_records_for_faiss, batch_size=self.faiss_batch_size
                    )
                    for t_id, c_count, e_model, v_coll in successful_tenders_to_mark:
                        state_repo.mark_indexed(
                            tender_id=t_id,
                            chunk_count=c_count,
                            embedding_model=e_model,
                            vector_collection=v_coll,
                        )
                self.vector_store.close()
                stats.faiss_total_count = self.vector_store.count()

            stats.status = "completed"
            stats.finished_at = datetime.now(UTC).isoformat()
            stats.elapsed_seconds = round(time.perf_counter() - started, 3)

            self._write_manifest(
                stats=stats,
                extra={
                    "dry_run": dry_run,
                    "recreate": recreate,
                    "model_name": model_name,
                    "device": device,
                    "collection_name": collection_name,
                    "faiss_path": faiss_path,
                    "database_batch_size": self.database_batch_size,
                    "faiss_batch_size": self.faiss_batch_size,
                    "chunk_size_chars": self.chunker.chunk_target_chars,
                    "overlap_chars": self.chunker.chunk_overlap_chars,
                    "start_offset": start_offset,
                    "limit": limit,
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
                    "model_name": model_name,
                    "device": device,
                    "collection_name": collection_name,
                    "faiss_path": faiss_path,
                    "database_batch_size": self.database_batch_size,
                    "faiss_batch_size": self.faiss_batch_size,
                    "chunk_size_chars": self.chunker.chunk_target_chars,
                    "overlap_chars": self.chunker.chunk_overlap_chars,
                    "start_offset": start_offset,
                    "limit": limit,
                },
            )
            raise

    def _log_exclusion(self, tender: Any, path_str: str, reason: str) -> None:
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if path.exists() else "w"
        with open(path, mode, encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if mode == "w":
                w.writerow(
                    ["tender_id", "ikn", "adi", "idare_adi", "exclusion_reason", "detected_at"]
                )
            w.writerow(
                [
                    tender.id,
                    getattr(tender, "ikn", ""),
                    getattr(tender, "adi", ""),
                    getattr(tender, "idare_adi", ""),
                    reason,
                    datetime.now(UTC).isoformat(),
                ]
            )

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
        stats: IndexingStats,
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

        temporary_path = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary_path.replace(self.manifest_path)
