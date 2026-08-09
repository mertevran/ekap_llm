"""Hazır profil FAISS vektörleriyle ihale adaylarını getiren eşleştirici."""

from __future__ import annotations

from typing import Any

from app.retrieval.isbak_tender_retriever import (
    IsbakTenderRetriever,
    TenderSearchResult,
    ChunkEvidence,
    ScoreBreakdown,
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

        from app.matching.score_aggregator import ScoreAggregator
        from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
        self._scorer = ScoreAggregator(settings)
        self._profile_loader = IsbakProfileLoader()

    def retrieve_profile(
        self,
        *,
        profile_code: str,
        limit: int | None = None,
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

        raw_by_chunk: dict[str, dict[str, Any]] = {}
        for faiss_id, _payload in profile_entries:
            vector = self._reconstruct_profile_vector(faiss_id)
            raw_results = self.vector_store.search(
                query_vector=vector,
                limit=self.settings.faiss_search_top_k,
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

        profile_meta = self._load_profile_meta(normalized_code)
        strong_terms = profile_meta.get("strong_terms", [])
        negative_terms = profile_meta.get("negative_terms", [])
        okas_prefixes = profile_meta.get("okas_prefixes", [])

        query_terms = self._query_terms_for_subclass(composite_query)
        candidates: list[TenderSearchResult] = []

        for tender_key, chunks in groups.items():
            candidate = self._build_candidate(
                tender_key=tender_key,
                chunks=chunks,
                query_terms=query_terms,
                profile_code=normalized_code,
                strong_terms=strong_terms,
                negative_terms=negative_terms,
                okas_prefixes=okas_prefixes,
            )
            if (
                candidate is not None
                and candidate.scores.final >= self.settings.minimum_final_score
            ):
                candidates.append(candidate)

        candidates.sort(key=lambda item: item.scores.final, reverse=True)
        effective_limit = (
            limit
            if limit is not None
            else self.settings.faiss_max_tenders_per_profile
        )
        return candidates[:effective_limit]

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

    def _load_profile_meta(self, profile_code: str) -> dict[str, Any]:
        """Profil metaverilerini yükler."""
        try:
            from app.company_profiles.isbak_profile import IsbakProfile
            profile_data = self._profile_loader.load_profile(profile_code)
            profile_obj = IsbakProfile.model_validate(profile_data)
            signals = profile_obj.ihale_kategori_sinyalleri
            return {
                "name": profile_obj.profil_adi,
                "strong_terms": signals.guclu_terimler,
                "negative_terms": signals.negatif_terimler,
                "okas_prefixes": signals.okas_kod_on_ekleri,
            }
        except Exception:
            return {}

    def _build_candidate(
        self,
        *,
        tender_key: str,
        chunks: list[dict[str, Any]],
        query_terms: tuple[str, ...],
        profile_code: str,
        strong_terms: list[str],
        negative_terms: list[str],
        okas_prefixes: list[str],
    ) -> TenderSearchResult | None:
        if not chunks:
            return None

        from app.vector_store.faiss_vector_reader import FaissVectorReader
        reader = FaissVectorReader()

        top_payload = chunks[0].get("payload", {})
        raw_scores = [float(c.get("score", 0.0)) for c in chunks]

        # İhale bilgileri
        tender_id = str(top_payload.get("tender_id") or tender_key)
        ikn = str(top_payload.get("ikn") or tender_key)

        # Resolve tender name like in IsbakTenderRetriever
        meta = top_payload.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("document_title"), str):
            tender_name = meta["document_title"].strip()
        else:
            title = top_payload.get("title")
            if isinstance(title, str) and title.strip():
                tender_name = title.strip()
            else:
                tender_name = str(top_payload.get("ikn") or "—")

        # Section types
        section_types = []
        for c in chunks:
            st = reader.resolve_section_type(c.get("payload", {}))
            section_types.append(st or "")

        # OKAS kodları
        okas_codes = []
        for c in chunks:
            okas_codes.extend(reader.resolve_okas_codes(c.get("payload", {})))

        evidence_texts = [
            str(c.get("payload", {}).get("text") or "")[:500] for c in chunks
        ]

        # ScoreAggregator hesaplaması
        breakdown = self._scorer.compute(
            raw_scores=raw_scores,
            section_types=section_types,
            okas_codes=okas_codes,
            query_terms=query_terms,
            tender_name=tender_name,
            profile_okas_prefixes=okas_prefixes,
            strong_terms=strong_terms,
            negative_terms=negative_terms,
            evidence_texts=evidence_texts,
        )

        scores = ScoreBreakdown(
            max_chunk=breakdown.max_similarity,
            top_chunks_mean=breakdown.top_similarity_mean,
            section_diversity=breakdown.section_diversity,
            okas_support=breakdown.okas_support,
            title_support=breakdown.title_support,
            final=breakdown.final_score,
            negative_penalty=breakdown.negative_term_penalty,
        )

        evidence_chunks = []
        for c in chunks:
            p = c.get("payload", {})
            full_text = str(p.get("text") or "")
            evidence_chunks.append(
                ChunkEvidence(
                    chunk_id=str(p.get("chunk_id") or ""),
                    section_id=str(p.get("section_id") or ""),
                    chunk_title=str(p.get("title") or ""),
                    semantic_score=round(float(c.get("score", 0.0)), 4),
                    text=full_text,
                    text_preview=full_text[:200],
                )
            )

        def _first_text(*values: Any) -> str:
            for value in values:
                text = str(value or "").strip()
                if text:
                    return text
            return ""

        metadata = top_payload.get("metadata") if isinstance(top_payload.get("metadata"), dict) else {}

        return TenderSearchResult(
            tender_id=tender_id,
            ikn=ikn,
            tender_name=tender_name,
            chunk_title=evidence_chunks[0].chunk_title if evidence_chunks else "",
            primary_profile_code=str(top_payload.get("primary_profile_code") or profile_code),
            profile_codes=[str(x) for x in top_payload.get("profile_codes", [])] if isinstance(top_payload.get("profile_codes"), list) else [],
            classification_status=str(top_payload.get("classification_status") or ""),
            idare_adi=_first_text(
                top_payload.get("authority_name"),
                top_payload.get("idare_adi"),
                metadata.get("authority_name"),
                metadata.get("idare_adi"),
            ),
            il=_first_text(top_payload.get("il"), metadata.get("il")),
            ihale_tarihi=_first_text(
                top_payload.get("ihale_tarihi"),
                top_payload.get("tender_date"),
                metadata.get("ihale_tarihi"),
                metadata.get("tender_date"),
            ),
            ihale_turu=_first_text(
                top_payload.get("ihale_turu"),
                top_payload.get("announcement_type"),
                metadata.get("ihale_turu"),
                metadata.get("announcement_type"),
            ),
            okas_codes=list(dict.fromkeys(okas_codes)),
            section_ids=list(set(st for st in section_types if st)),
            scores=scores,
            evidence_chunks=evidence_chunks,
        )


__all__ = ["ProfileVectorTenderMatcher"]
