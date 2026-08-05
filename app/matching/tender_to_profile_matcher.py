"""İhale → Profil eşleştirici.

İhale FAISS indeksindeki hazır vektörleri kullanarak profil FAISS indeksinde
arama yapar. Profil indeksindeki tüm vektörler taranır, ardından
profile_code bazında gruplanarak top-k profil seçilir.

İhale metni yeniden gömme işleminden geçirilmez.

Kanıt bütünlüğü:
    - tender_evidence_* alanları yalnızca ihale FAISS payload kayıtlarından üretilir.
    - profile_evidence_* alanları yalnızca profil arama sonucu payload'larından üretilir.
    - Metinler asla karışmaz; context_builder katmanında ayrı tutulur.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from app.matching.models import ChunkMatchEvidence, TenderProfileMatch
from app.matching.score_aggregator import ScoreAggregator, build_query_terms
from app.vector_store.faiss_store import FaissVectorStore
from app.vector_store.faiss_vector_reader import FaissVectorReader

logger = logging.getLogger(__name__)


def _normalize_authority(name: str) -> str:
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
    norm = _normalize_authority(authority_name)
    return any(_normalize_authority(exc) == norm for exc in excluded)


def _evidence_pair_key(record: dict[str, Any]) -> tuple[str, str]:
    """İhale–profil kanıt çifti için tekilleştirme anahtarı üretir.

    Anahtar: (tender_chunk_id, profile_chunk_id)
    """
    tender_payload = record["tender_payload"]
    profile_result = record["profile_result"]
    profile_payload = profile_result.get("payload", {})

    tender_chunk_id = str(
        tender_payload.get("chunk_id")
        or tender_payload.get("point_id")
        or record["tender_external_id"]
    )

    profile_chunk_id = str(
        profile_payload.get("chunk_id")
        or profile_payload.get("section_id")
        or profile_result.get("id")
        or ""
    )

    return tender_chunk_id, profile_chunk_id


class TenderToProfileMatcher:
    """İhale FAISS vektörleriyle profil FAISS indeksinde arama yapar.

    Profil indeksindeki tüm vektörler taranır. Sonuçlar profile_code
    bazında gruplanır; her profil için birden fazla profil parçası skoru
    birleştirilir. Yalnızca güçlü profil–ihale çiftleri döndürülür.

    Kanıt bütünlüğü:
        Her arama kaydı, kaynak ihale payload'ını da saklar. Böylece
        ihale ve profil metinleri TenderProfileMatch içinde asla karışmaz.
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
        tender_id: str | None = None,
        ikn: str | None = None,
        top_k: int = 3,
        minimum_score: float | None = None,
    ) -> list[TenderProfileMatch]:
        """Belirtilen ihale için en uygun profilleri döndürür.

        Args:
            tender_id: İhale FAISS'teki tender_id değeri.
            ikn: İhale Kayıt Numarası.
            top_k: Döndürülecek profil sayısı.
            minimum_score: Bu değerin altındaki adayları ele.

        Returns:
            TenderProfileMatch listesi, retrieval_score'a göre azalan sırada.
        """
        if not tender_id and not ikn:
            raise ValueError("tender_id veya ikn'den en az biri gereklidir.")

        min_score = (
            minimum_score
            if minimum_score is not None
            else self._settings.minimum_final_score
        )

        # 1. İhale parçalarını bul
        tender_entries = self._reader.find_tender_entries(
            self._tender_store,
            tender_id=tender_id,
            ikn=ikn,
        )
        if not tender_entries:
            ref = ikn or tender_id
            raise KeyError(
                f"İhale FAISS indeksinde ihale bulunamadı: {ref}"
            )

        # İhale bilgilerini ilk parçadan al
        top_tender_payload = tender_entries[0][1]
        resolved_tender_id = str(
            top_tender_payload.get("tender_id") or tender_id or ""
        )
        resolved_ikn = str(top_tender_payload.get("ikn") or ikn or "")
        tender_name = self._resolve_tender_name(top_tender_payload)
        authority_name = self._reader.resolve_authority_name(top_tender_payload)

        # tender_id veya ikn ikisi birden verilmişse aynı ihale mi doğrula
        if tender_id and ikn:
            p_tid = str(top_tender_payload.get("tender_id") or "").strip()
            p_ikn = str(top_tender_payload.get("ikn") or "").strip()
            if p_tid and p_ikn and p_tid != tender_id.strip() and p_ikn == ikn.strip():
                logger.debug(
                    "tender_id eşleşmedi ama ikn eşleşti; ikn öncelikli."
                )

        # İSBAK kendi ihaleleri bu modda da filtrelenir
        if _is_excluded_authority(authority_name, self._settings.excluded_authorities):
            logger.info("Kendi ihalesi filtrelendi (tender→profile): %s", resolved_ikn)
            return []

        # 2. İhale chunk'larından bağlam metni oluştur (skor hesabı için)
        composite_tender_text = "\n\n".join(
            str(payload.get("text") or "").strip()
            for _, payload in tender_entries
            if str(payload.get("text") or "").strip()
        )
        tender_query_terms = build_query_terms(composite_tender_text)

        # İhale OKAS kodları
        tender_okas: list[str] = []
        for _, tpayload in tender_entries:
            tender_okas.extend(self._reader.resolve_okas_codes(tpayload))

        # 3. Her ihale vektörüyle profil indeksinde ara
        # Sonuçları hem profil result hem kaynak ihale payload ile birlikte sakla
        n_profile_chunks = self._profile_store.count()
        search_limit = max(n_profile_chunks, self._settings.faiss_search_top_k)

        # profile_code → [evidence_record, ...]
        # Her kayıt: {profile_result, tender_external_id, tender_payload}
        # Tekilleştirme anahtarı: (tender_chunk_id, profile_chunk_id)
        profile_groups: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}

        for tender_external_id, tender_payload in tender_entries:
            try:
                vector = self._reader.reconstruct_vector_by_external_id(
                    self._tender_store, tender_external_id
                )
            except (KeyError, RuntimeError) as exc:
                logger.warning(
                    "İhale vektörü okunamadı: id=%d, hata=%s",
                    tender_external_id,
                    exc,
                )
                continue

            results = self._profile_store.search(
                query_vector=vector,
                limit=search_limit,
                score_threshold=None,
            )
            for result in results:
                payload = result.get("payload", {})
                p_code = str(payload.get("profile_code") or "").strip().upper()
                if not p_code:
                    continue

                record: dict[str, Any] = {
                    "profile_result": result,
                    "tender_external_id": tender_external_id,
                    "tender_payload": tender_payload,
                }

                pair_key = _evidence_pair_key(record)

                # Boş chunk_id'li kayıtları atla
                t_cid, p_cid = pair_key
                if not t_cid or not p_cid:
                    continue

                if p_code not in profile_groups:
                    profile_groups[p_code] = {}

                # Aynı ihale–profil çifti tekrar gelirse en yüksek skor korunur
                existing = profile_groups[p_code].get(pair_key)
                raw_score = float(result.get("score", 0.0))
                if existing is None or raw_score > float(
                    existing["profile_result"].get("score", 0.0)
                ):
                    profile_groups[p_code][pair_key] = record

        # 4. Her profil için en güçlü kanıt çiftlerini seç ve TenderProfileMatch oluştur
        candidates: list[TenderProfileMatch] = []

        limit = self._settings.faiss_max_chunks_per_tender

        for p_code, pair_map in profile_groups.items():
            records = list(pair_map.values())
            if not records:
                continue

            # Çeşitlilikli seçim: farklı tender chunk'larından en iyi çiftler
            selected_pairs = self._select_diverse_evidence_pairs(records, limit)

            if not selected_pairs:
                continue

            # Skor hesabı — seçilen kanıt çiftlerinden
            raw_scores = [
                max(0.0, min(1.0, float(rec["profile_result"].get("score", 0.0))))
                for rec in selected_pairs
            ]

            # Section types — seçilen ihale parçalarından (tüm tender_entries'den değil)
            selected_tender_payloads = [rec["tender_payload"] for rec in selected_pairs]
            section_types = [
                self._reader.resolve_section_type(payload)
                for payload in selected_tender_payloads
                if self._reader.resolve_section_type(payload)
            ]

            # Profil meta
            profile_meta = self._load_profile_meta(p_code)
            profile_name = profile_meta.get("name", p_code)
            strong_terms = profile_meta.get("strong_terms", [])
            negative_terms = profile_meta.get("negative_terms", [])
            okas_prefixes = profile_meta.get("okas_prefixes", [])

            breakdown = self._scorer.compute(
                raw_scores=raw_scores,
                section_types=section_types,
                okas_codes=tender_okas,
                query_terms=tender_query_terms,
                tender_name=tender_name,
                profile_okas_prefixes=okas_prefixes,
                strong_terms=strong_terms,
                negative_terms=negative_terms,
            )

            if breakdown.final_score < min_score:
                continue

            # İhale kanıt alanlarını gerçek ihale payload'larından üret
            # Profil kanıt alanlarını gerçek profil payload'larından üret
            # Tekilleştirme: tender chunk'ları ve profile chunk'ları ayrı ayrı
            tender_evidence = self._build_tender_evidence(selected_pairs)
            profile_evidence = self._build_profile_evidence(selected_pairs)

            # ChunkMatchEvidence kayıtlarını oluştur
            chunk_matches = self._build_chunk_matches(selected_pairs)

            candidates.append(
                TenderProfileMatch(
                    tender_id=resolved_tender_id,
                    ikn=resolved_ikn,
                    tender_name=tender_name,
                    authority_name=authority_name,
                    profile_code=p_code,
                    profile_name=profile_name,
                    retrieval_rank=0,
                    retrieval_score=breakdown.final_score,
                    score_breakdown=breakdown,
                    tender_evidence_chunk_ids=tender_evidence["chunk_ids"],
                    tender_evidence_sections=tender_evidence["sections"],
                    tender_evidence_texts=tender_evidence["texts"],
                    profile_evidence_chunk_ids=profile_evidence["chunk_ids"],
                    profile_evidence_sections=profile_evidence["sections"],
                    profile_evidence_texts=profile_evidence["texts"],
                    chunk_matches=chunk_matches,
                )
            )

        # 5. Sırala, kırp, rank ata
        candidates.sort(key=lambda m: m.retrieval_score, reverse=True)
        result_list = candidates[:top_k]

        for rank, m in enumerate(result_list, start=1):
            m.retrieval_rank = rank

        return result_list

    # ------------------------------------------------------------------
    # Çeşitlilikli kanıt seçimi
    # ------------------------------------------------------------------

    @staticmethod
    def _select_diverse_evidence_pairs(
        records: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Her profil grubu için en güçlü ve çeşitli kanıt çiftlerini seçer.

        Algoritma:
            1. Her benzersiz tender_chunk_id için en güçlü eşleşmeyi seç.
            2. Boş yer kalırsa kalan en yüksek skorlu eşleşmeleri ekle.
            3. Deterministik sıralama: skor desc → tender_chunk_id → profile_chunk_id.

        Args:
            records: Tüm kanıt kayıtları (pair_key tekilleştirilmiş).
            limit: Maksimum seçim sayısı.

        Returns:
            Seçilen kanıt kayıtları listesi.
        """
        if not records:
            return []

        def _score(rec: dict[str, Any]) -> float:
            return max(0.0, min(1.0, float(rec["profile_result"].get("score", 0.0))))

        def _tender_cid(rec: dict[str, Any]) -> str:
            return str(
                rec["tender_payload"].get("chunk_id")
                or rec["tender_payload"].get("point_id")
                or rec["tender_external_id"]
            )

        def _profile_cid(rec: dict[str, Any]) -> str:
            pp = rec["profile_result"].get("payload", {})
            return str(
                pp.get("chunk_id")
                or pp.get("section_id")
                or rec["profile_result"].get("id")
                or ""
            )

        # Deterministik sıralama: skor azalan, sonra chunk_id artan
        sorted_records = sorted(
            records,
            key=lambda r: (-_score(r), _tender_cid(r), _profile_cid(r)),
        )

        # 1. Adım: her tender_chunk_id için bir kez en iyisini seç
        selected: list[dict[str, Any]] = []
        seen_tender_cids: set[str] = set()

        for rec in sorted_records:
            if len(selected) >= limit:
                break
            tcid = _tender_cid(rec)
            if tcid not in seen_tender_cids:
                seen_tender_cids.add(tcid)
                selected.append(rec)

        # 2. Adım: hâlâ yer varsa kalan en yüksek skorluları ekle
        if len(selected) < limit:
            selected_keys = {id(r) for r in selected}
            for rec in sorted_records:
                if len(selected) >= limit:
                    break
                if id(rec) not in selected_keys:
                    selected.append(rec)

        return selected

    # ------------------------------------------------------------------
    # Kanıt üretimi
    # ------------------------------------------------------------------

    def _build_tender_evidence(
        self, selected_pairs: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        """Seçilen kanıt çiftlerinden ihale kanıt listelerini üretir.

        Her alan yalnızca ihale payload'ından okunur.
        Aynı tender_chunk_id yalnızca bir kez eklenir (tekilleştirme).
        """
        chunk_ids: list[str] = []
        sections: list[str] = []
        texts: list[str] = []
        seen: set[str] = set()

        # Skor azalan → tender_chunk_id artan sırasında işle (deterministik)
        sorted_pairs = sorted(
            selected_pairs,
            key=lambda r: (
                -max(0.0, min(1.0, float(r["profile_result"].get("score", 0.0)))),
                str(
                    r["tender_payload"].get("chunk_id")
                    or r["tender_payload"].get("point_id")
                    or r["tender_external_id"]
                ),
            ),
        )

        for rec in sorted_pairs:
            tp = rec["tender_payload"]
            cid = str(
                tp.get("chunk_id")
                or tp.get("point_id")
                or rec["tender_external_id"]
            )
            text = str(tp.get("text") or "").strip()

            # Boş chunk_id veya metin atlanır
            if not cid or not text:
                continue

            # Aynı tender_chunk_id yalnızca bir kez
            if cid in seen:
                continue
            seen.add(cid)

            section = self._reader.resolve_section_type(tp)
            chunk_ids.append(cid)
            sections.append(section)
            texts.append(text)

        return {"chunk_ids": chunk_ids, "sections": sections, "texts": texts}

    def _build_profile_evidence(
        self, selected_pairs: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        """Seçilen kanıt çiftlerinden profil kanıt listelerini üretir.

        Her alan yalnızca profil result payload'ından okunur.
        Aynı profile_chunk_id yalnızca bir kez eklenir (tekilleştirme).
        """
        chunk_ids: list[str] = []
        sections: list[str] = []
        texts: list[str] = []
        seen: set[str] = set()

        sorted_pairs = sorted(
            selected_pairs,
            key=lambda r: (
                -max(0.0, min(1.0, float(r["profile_result"].get("score", 0.0)))),
            ),
        )

        for rec in sorted_pairs:
            pp = rec["profile_result"].get("payload", {})
            cid = str(
                pp.get("chunk_id")
                or pp.get("section_id")
                or rec["profile_result"].get("id")
                or ""
            )
            text = str(pp.get("text") or "").strip()

            if not cid or not text:
                continue

            if cid in seen:
                continue
            seen.add(cid)

            section = str(
                pp.get("section")
                or pp.get("section_type")
                or ""
            ).strip()
            chunk_ids.append(cid)
            sections.append(section)
            texts.append(text)

        return {"chunk_ids": chunk_ids, "sections": sections, "texts": texts}

    def _build_chunk_matches(
        self, selected_pairs: list[dict[str, Any]]
    ) -> list[ChunkMatchEvidence]:
        """Her seçilmiş kanıt çifti için ChunkMatchEvidence oluşturur."""
        result: list[ChunkMatchEvidence] = []

        for rec in selected_pairs:
            tp = rec["tender_payload"]
            pr = rec["profile_result"]
            pp = pr.get("payload", {})

            t_cid = str(
                tp.get("chunk_id")
                or tp.get("point_id")
                or rec["tender_external_id"]
            )
            t_section = self._reader.resolve_section_type(tp)
            t_text = str(tp.get("text") or "").strip()

            p_cid = str(
                pp.get("chunk_id")
                or pp.get("section_id")
                or pr.get("id")
                or ""
            )
            p_section = str(
                pp.get("section")
                or pp.get("section_type")
                or ""
            ).strip()
            p_text = str(pp.get("text") or "").strip()

            raw_score = float(pr.get("score", 0.0))
            score = max(0.0, min(1.0, raw_score))

            # Boş chunk_id veya metin olan kayıtları atla
            if not t_cid or not p_cid or not t_text or not p_text:
                continue

            result.append(
                ChunkMatchEvidence(
                    tender_chunk_id=t_cid,
                    tender_section_type=t_section,
                    tender_text=t_text,
                    profile_chunk_id=p_cid,
                    profile_section_type=p_section,
                    profile_text=p_text,
                    similarity_score=score,
                )
            )

        return result

    # ------------------------------------------------------------------
    # Yardımcı
    # ------------------------------------------------------------------

    def _load_profile_meta(self, profile_code: str) -> dict[str, Any]:
        if self._profile_loader is None:
            return {}
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
        except Exception as exc:
            logger.debug("Profil meta yüklenemedi: %s — %s", profile_code, exc)
            return {}

    @staticmethod
    def _resolve_tender_name(payload: dict[str, Any]) -> str:
        meta = payload.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("document_title"), str):
            return meta["document_title"].strip()
        title = payload.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        return str(payload.get("ikn") or "—")


__all__ = ["TenderToProfileMatcher"]
