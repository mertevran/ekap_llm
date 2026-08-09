"""Profil → İhale eşleştirici.

Profil FAISS indeksindeki hazır vektörleri kullanarak ihale FAISS indeksinde
arama yapar. Profil metinleri yeniden gömme işleminden geçirilmez.

Mevcut ProfileVectorTenderMatcher'ı geriye uyumlu ince sarmalayıcı olarak bırakır.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from app.company_profiles.isbak_profile import IsbakProfile
from app.matching.models import ProfileTenderMatch
from app.matching.score_aggregator import ScoreAggregator, build_query_terms
from app.vector_store.faiss_store import FaissVectorStore
from app.vector_store.faiss_vector_reader import FaissVectorReader

logger = logging.getLogger(__name__)


def _normalize_authority(name: str) -> str:
    """İdare adını karşılaştırma için normalize eder."""
    norm = unicodedata.normalize("NFKC", str(name or ""))
    norm = norm.translate(str.maketrans({"I": "ı", "İ": "i"}))
    norm = norm.lower()
    norm = (
        norm.replace("a.ş.", "")
        .replace("aş", "")
        .replace("anonim şirketi", "")
        .replace("sanayi ve ticaret", "")
    )
    norm = re.sub(r"[^\w\s]", "", norm)
    return " ".join(norm.split())


def _is_excluded_authority(
    authority_name: str, excluded: tuple[str, ...]
) -> bool:
    """İdare adı dışlananlar listesinde mi?"""
    norm = _normalize_authority(authority_name)
    return any(_normalize_authority(exc) == norm for exc in excluded)


class ProfileToTenderMatcher:
    """Profil FAISS vektörleriyle ihale FAISS indeksinde arama yapar.

    Her profile_code için FAISS'ten hazır vektörler okunur; yeniden
    embedding yapılmaz.
    """

    def __init__(
        self,
        *,
        tender_store: FaissVectorStore,
        profile_store: FaissVectorStore,
        settings: Any | None = None,
        profile_loader: Any | None = None,
    ) -> None:
        if settings is None:
            from app.config.isbak_rag_settings import get_isbak_rag_settings
            settings = get_isbak_rag_settings()
        self._settings = settings
        self._tender_store = tender_store
        self._profile_store = profile_store
        self._profile_loader = profile_loader
        self._reader = FaissVectorReader()
        self._scorer = ScoreAggregator(settings)

    # ------------------------------------------------------------------
    # Ana API
    # ------------------------------------------------------------------

    def match(
        self,
        *,
        profile_code: str,
        top_k: int,
        minimum_score: float | None = None,
    ) -> list[ProfileTenderMatch]:
        """Belirtilen profil için en uygun ihaleleri döndürür.

        Args:
            profile_code: Profil kodu (örn. "AUS-01").
            top_k: Döndürülecek ihale sayısı.
            minimum_score: Bu değerin altındaki adayları ele.

        Returns:
            ProfileTenderMatch listesi, final_score'a göre azalan sırada.
        """
        norm_code = str(profile_code).strip().upper()
        if not norm_code:
            raise ValueError("Profil kodu boş olamaz.")

        min_score = (
            minimum_score
            if minimum_score is not None
            else self._settings.minimum_final_score
        )

        # 1. Profil parçalarını bul
        profile_entries = self._reader.find_profile_entries(
            self._profile_store, norm_code
        )
        if not profile_entries:
            raise KeyError(
                f"Profil FAISS indeksinde profil bulunamadı: {norm_code}"
            )

        # 2. Profil bilgilerini yükle (opsiyonel)
        profile_meta = self._load_profile_meta(norm_code)
        profile_name = profile_meta.get("name", "")
        strong_terms = profile_meta.get("strong_terms", [])
        negative_terms = profile_meta.get("negative_terms", [])
        okas_prefixes = profile_meta.get("okas_prefixes", [])

        # 3. Kompozit sorgu metni (term overlap için)
        composite_text = "\n\n".join(
            str(payload.get("text") or "").strip()
            for _, payload in profile_entries
            if str(payload.get("text") or "").strip()
        )
        query_terms = build_query_terms(composite_text)

        # 4. Her profil vektörüyle ihale indeksinde ara → chunk bazlı tekilleştir
        raw_by_chunk: dict[str, dict[str, Any]] = {}
        for faiss_id, _payload in profile_entries:
            try:
                vector = self._reader.reconstruct_vector_by_external_id(
                    self._profile_store, faiss_id
                )
            except (KeyError, RuntimeError) as exc:
                logger.warning(
                    "Profil vektörü okunamadı: id=%d, hata=%s", faiss_id, exc
                )
                continue

            results = self._tender_store.search(
                query_vector=vector,
                limit=self._settings.faiss_search_top_k,
                score_threshold=None,
            )
            for result in results:
                key = self._chunk_key(result)
                existing = raw_by_chunk.get(key)
                if existing is None or float(result.get("score", 0.0)) > float(
                    existing.get("score", 0.0)
                ):
                    raw_by_chunk[key] = result

        # 5. İhale bazında grupla
        groups = self._group_by_tender(
            raw_chunks=list(raw_by_chunk.values()),
            max_chunks=self._settings.faiss_max_chunks_per_tender,
        )

        # 6. Her grup için aday puanı hesapla
        candidates: list[ProfileTenderMatch] = []
        for tender_key, chunks in groups.items():
            match = self._build_match(
                tender_key=tender_key,
                chunks=chunks,
                profile_code=norm_code,
                profile_name=profile_name,
                query_terms=query_terms,
                okas_prefixes=okas_prefixes,
                strong_terms=strong_terms,
                negative_terms=negative_terms,
            )
            if match is None:
                continue

            # İSBAK kendi ihalelerini filtrele
            if _is_excluded_authority(
                match.authority_name, self._settings.excluded_authorities
            ):
                logger.debug(
                    "Kendi ihalesi filtrelendi: %s — %s",
                    match.ikn,
                    match.authority_name,
                )
                continue

            if match.retrieval_score >= min_score:
                candidates.append(match)

        # 7. Sırala ve kırp
        candidates.sort(key=lambda m: m.retrieval_score, reverse=True)
        result_list = candidates[:top_k]

        # Sıralama numarası ata
        for rank, m in enumerate(result_list, start=1):
            m.retrieval_rank = rank

        return result_list

    # ------------------------------------------------------------------
    # Yardımcı yöntemler
    # ------------------------------------------------------------------

    def _load_profile_meta(self, profile_code: str) -> dict[str, Any]:
        """Profil metaverilerini loader'dan yükler (opsiyonel)."""
        if self._profile_loader is None:
            return {}
        try:
            profile_data = self._profile_loader.load_profile(profile_code)
            profile_obj = IsbakProfile.model_validate(profile_data)
            signals = profile_obj.ihale_kategori_sinyalleri
            return {
                "name": profile_obj.profil_adi,
                "strong_terms": signals.guclu_terimler,
                "negative_terms": signals.negatif_terimler,
                "okas_prefixes": signals.okas_kod_on_ekleri,
            }
        except Exception as exc:
            logger.debug("Profil meta yüklenemedi: %s — %s", profile_code, exc)
            return {}

    def _group_by_tender(
        self,
        *,
        raw_chunks: list[dict[str, Any]],
        max_chunks: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """Chunk'ları ihale bazında gruplar, chunk başına en yüksek skoru tutar."""
        tender_map: dict[str, list[dict[str, Any]]] = {}

        for chunk in raw_chunks:
            payload = dict(chunk.get("payload") or {})
            tender_key = str(
                payload.get("tender_id") or payload.get("ikn") or ""
            ).strip()
            if not tender_key:
                continue
            tender_map.setdefault(tender_key, []).append(chunk)

        groups: dict[str, list[dict[str, Any]]] = {}
        for tkey, chunks in tender_map.items():
            # Aynı chunk_id için en yüksek skoru tut
            dedup: dict[str, dict[str, Any]] = {}
            for c in chunks:
                cid = str(c.get("payload", {}).get("chunk_id") or id(c))
                if cid not in dedup or float(c.get("score", 0.0)) > float(
                    dedup[cid].get("score", 0.0)
                ):
                    dedup[cid] = c
            sorted_chunks = sorted(
                dedup.values(),
                key=lambda c: float(c.get("score", 0.0)),
                reverse=True,
            )
            groups[tkey] = sorted_chunks[:max_chunks]

        return groups

    def _build_match(
        self,
        *,
        tender_key: str,
        chunks: list[dict[str, Any]],
        profile_code: str,
        profile_name: str,
        query_terms: tuple[str, ...],
        okas_prefixes: list[str],
        strong_terms: list[str],
        negative_terms: list[str],
    ) -> ProfileTenderMatch | None:
        if not chunks:
            return None

        top_payload = chunks[0].get("payload", {})
        raw_scores = [float(c.get("score", 0.0)) for c in chunks]

        # İhale bilgileri
        tender_id = str(top_payload.get("tender_id") or tender_key)
        ikn = str(top_payload.get("ikn") or tender_key)
        tender_name = self._resolve_tender_name(top_payload)
        authority_name = self._reader.resolve_authority_name(top_payload)

        # Section types — gerçek section_type değerleri
        section_types = []
        for c in chunks:
            st = self._reader.resolve_section_type(c.get("payload", {}))
            section_types.append(st or "")

        # OKAS kodları
        okas_codes = []
        for c in chunks:
            okas_codes.extend(self._reader.resolve_okas_codes(c.get("payload", {})))

        # Kanıt chunk'ları
        evidence_chunk_ids = [
            str(c.get("payload", {}).get("chunk_id") or "") for c in chunks
        ]
        evidence_texts = [
            str(c.get("payload", {}).get("text") or "")[:500] for c in chunks
        ]

        # Puan hesapla
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

        return ProfileTenderMatch(
            profile_code=profile_code,
            profile_name=profile_name,
            tender_id=tender_id,
            ikn=ikn,
            tender_name=tender_name,
            authority_name=authority_name,
            retrieval_rank=0,  # sonradan atanır
            retrieval_score=breakdown.final_score,
            score_breakdown=breakdown,
            evidence_chunk_ids=evidence_chunk_ids,
            evidence_sections=section_types,
            evidence_texts=evidence_texts,
        )

    @staticmethod
    def _resolve_tender_name(payload: dict[str, Any]) -> str:
        meta = payload.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("document_title"), str):
            return meta["document_title"].strip()
        title = payload.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        return str(payload.get("ikn") or "—")

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


__all__ = ["ProfileToTenderMatcher"]
