"""Hazır profil FAISS vektörleriyle ihale adaylarını getiren eşleştirici."""

from __future__ import annotations

from typing import Any

from app.retrieval.isbak_tender_retriever import (
    IsbakTenderRetriever,
    TenderSearchResult,
)
from app.vector_store.faiss_store import FaissVectorStore


class ProfileVectorTenderMatcher(IsbakTenderRetriever):
    """
    Profil FAISS indeksindeki hazır vektörleri kullanarak ihale FAISS
    indeksinde arama yapar ve sonuçları ihale bazında birleştirir.

    Profil metinleri yeniden gömme işleminden geçirilmez.
    """

    def __init__(
        self,
        *,
        tender_vector_store: FaissVectorStore,
        profile_vector_store: FaissVectorStore,
        settings: Any | None = None,
    ) -> None:
        # Üst sınıfın metin tabanlı retrieve() yöntemi bu sınıfta kullanılmaz.
        # Protocol gereksinimi için embedder=None verilmiyor; bunun yerine
        # yalnızca kullanılan alanlar açıkça atanıyor.
        self.embedder = None
        self.vector_store = tender_vector_store
        self.profile_vector_store = profile_vector_store

        if settings is None:
            from app.config.isbak_rag_settings import get_isbak_rag_settings

            settings = get_isbak_rag_settings()
        self.settings = settings

    def retrieve_profile(
        self,
        *,
        profile_code: str,
        limit: int | None = None,
        allowed_ikns: set[str] | None = None,
    ) -> list[TenderSearchResult]:
        normalized_code = str(profile_code).strip().upper()
        if not normalized_code:
            raise ValueError("Profil kodu boş olamaz.")

        self._validate_stores()

        profile_entries = self._profile_entries(normalized_code)
        if not profile_entries:
            raise KeyError(
                f"Profil FAISS indeksinde profil bulunamadı: {normalized_code}"
            )

        composite_query = "\n\n".join(
            str(payload.get("text") or "").strip()
            for _, payload in profile_entries
            if str(payload.get("text") or "").strip()
        )
        if not composite_query:
            raise ValueError(
                f"Profil FAISS yük verisinde metin bulunamadı: {normalized_code}"
            )

        subset_ids: list[int] | None = None
        if allowed_ikns is not None:
            normalized_allowed = {
                self._normalize_ikn(value)
                for value in allowed_ikns
                if self._normalize_ikn(value)
            }
            if not normalized_allowed:
                return []
            subset_ids = [
                int(faiss_id)
                for faiss_id, payload in self.vector_store.payloads.items()
                if self._normalize_ikn(payload.get("ikn")) in normalized_allowed
            ]
            if not subset_ids:
                return []

        raw_by_chunk: dict[str, dict[str, Any]] = {}
        for faiss_id, _payload in profile_entries:
            vector = self._reconstruct_profile_vector(faiss_id)
            if subset_ids is None:
                raw_results = self.vector_store.search(
                    query_vector=vector,
                    limit=self.settings.faiss_search_top_k,
                    score_threshold=None,
                )
            else:
                raw_results = self.vector_store.search_subset(
                    query_vector=vector,
                    allowed_ids=subset_ids,
                    limit=len(subset_ids),
                    score_threshold=None,
                )
            for result in raw_results:
                chunk_key = self._chunk_key(result)
                current = raw_by_chunk.get(chunk_key)
                if current is None or float(result.get("score", 0.0)) > float(
                    current.get("score", 0.0)
                ):
                    raw_by_chunk[chunk_key] = result

        groups = self._group_by_tender(
            raw_chunks=list(raw_by_chunk.values()),
            max_chunks_per_tender=self.settings.faiss_max_chunks_per_tender,
        )

        query_terms = self._query_terms_for_subclass(composite_query)
        candidates: list[TenderSearchResult] = []

        for tender_key, chunks in groups.items():
            candidate = self._build_result(
                tender_key=tender_key,
                chunks=chunks,
                query=composite_query,
                query_terms=query_terms,
            )
            if (
                candidate is not None
                and (
                    subset_ids is not None
                    or candidate.scores.final >= self.settings.minimum_final_score
                )
            ):
                candidates.append(candidate)

        candidates.sort(key=lambda item: item.scores.final, reverse=True)
        effective_limit = (
            limit
            if limit is not None
            else self.settings.faiss_max_tenders_per_profile
        )
        return candidates[:effective_limit]

    @staticmethod
    def _normalize_ikn(value: Any) -> str:
        return "".join(str(value or "").upper().split())

    def _validate_stores(self) -> None:
        if not self.vector_store.collection_exists():
            raise FileNotFoundError(
                f"İhale FAISS indeksi bulunamadı: {self.vector_store.index_file}"
            )
        if not self.profile_vector_store.collection_exists():
            raise FileNotFoundError(
                "Profil FAISS indeksi bulunamadı: "
                f"{self.profile_vector_store.index_file}"
            )
        if self.vector_store.index is None:
            raise RuntimeError("İhale FAISS indeksi belleğe yüklenemedi.")
        if self.profile_vector_store.index is None:
            raise RuntimeError("Profil FAISS indeksi belleğe yüklenemedi.")
        if self.vector_store.index.d != self.profile_vector_store.index.d:
            raise ValueError(
                "İhale ve profil vektör boyutları uyuşmuyor: "
                f"ihale={self.vector_store.index.d}, "
                f"profil={self.profile_vector_store.index.d}"
            )

    def _profile_entries(
        self,
        profile_code: str,
    ) -> list[tuple[int, dict[str, Any]]]:
        entries: list[tuple[int, dict[str, Any]]] = []

        for faiss_id, payload in self.profile_vector_store.payloads.items():
            payload_code = str(payload.get("profile_code") or "").strip().upper()
            if payload_code == profile_code:
                entries.append((int(faiss_id), payload))

        entries.sort(
            key=lambda item: (
                int(item[1].get("section_order") or 0),
                str(item[1].get("section") or ""),
            )
        )
        return entries

    def _reconstruct_profile_vector(
        self,
        faiss_id: int,
    ) -> list[float]:
        """
        IndexIDMap içindeki dış FAISS kimliğini iç sıra numarasına
        çevirerek profil vektörünü temel indeksten okur.
        """
        import faiss
        import numpy as np

        index = self.profile_vector_store.index
        if index is None:
            raise RuntimeError(
                "Profil FAISS indeksi belleğe yüklenemedi."
            )

        # Düz indekslerde doğrudan yeniden oluşturma denenebilir.
        if not hasattr(index, "id_map"):
            try:
                vector = index.reconstruct(faiss_id)
            except RuntimeError as exc:
                raise RuntimeError(
                    "Profil vektörü FAISS indeksinden okunamadı: "
                    f"id={faiss_id}"
                ) from exc

            return np.asarray(
                vector,
                dtype=np.float32,
            ).tolist()

        # IndexIDMap dış kimlikleri id_map içinde tutar.
        external_ids = faiss.vector_to_array(index.id_map)
        positions = np.flatnonzero(external_ids == faiss_id)

        if positions.size == 0:
            raise KeyError(
                "Profil vektör kimliği FAISS id_map içinde bulunamadı: "
                f"id={faiss_id}"
            )

        internal_position = int(positions[0])
        base_index = index.index

        try:
            vector = base_index.reconstruct(internal_position)
        except RuntimeError as exc:
            raise RuntimeError(
                "Profil vektörü temel FAISS indeksinden okunamadı: "
                f"dış_id={faiss_id}, iç_sıra={internal_position}"
            ) from exc

        return np.asarray(
            vector,
            dtype=np.float32,
        ).tolist()

    @staticmethod
    def _chunk_key(result: dict[str, Any]) -> str:
        payload = dict(result.get("payload") or {})
        chunk_id = str(payload.get("chunk_id") or "").strip()
        if chunk_id:
            return chunk_id

        tender_id = str(
            payload.get("tender_id") or payload.get("ikn") or ""
        ).strip()
        section_id = str(
            payload.get("section_id") or payload.get("section_type") or ""
        ).strip()
        title = str(payload.get("title") or "").strip()
        return f"{tender_id}|{section_id}|{title}"

    @staticmethod
    def _query_terms_for_subclass(query: str) -> tuple[str, ...]:
        # isbak_tender_retriever içindeki özel yardımcıyı tek noktadan kullanır.
        from app.retrieval.isbak_tender_retriever import _query_terms

        return _query_terms(query)


__all__ = ["ProfileVectorTenderMatcher"]
