"""Ortak puanlama altyapısı.

Hem profil odaklı hem ihale odaklı modda aynı ScoreAggregator sınıfı kullanılır.
Ağırlıklar IsbakRagSettings'ten okunur; sabit kodlanmaz.

Puan formülü:
    final_score =
        max_similarity * 0.55
        + top_similarity_mean * 0.20
        + section_diversity * 0.10
        + okas_support * 0.10
        + title_support * 0.05
        - negative_term_penalty

Negatif ceza sonucu 0.0 altına düşüremez.
Nihai skor 0.0–1.0 arasına sınırlandırılır.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.matching.models import MatchScoreBreakdown
from app.vector_store.faiss_vector_reader import KNOWN_SECTION_TYPES

_TOKEN_PATTERN = re.compile(r"[a-zçğışöü0-9]+", re.IGNORECASE | re.UNICODE)
_STOPWORDS = frozenset(
    {
        "ve", "veya", "ile", "için", "bir", "bu", "şu", "o",
        "alımı", "alım", "satın", "hizmet", "hizmeti", "işi", "iş",
        "malzeme", "malzemesi", "ihalesi", "ihale", "yapım",
        "temini", "temin", "dahil", "kapsamında",
    }
)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = normalized.translate(str.maketrans({"I": "ı", "İ": "i"}))
    return normalized.lower().replace("\u0307", "").strip()


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(_normalize(text))


def _query_terms(text: str) -> tuple[str, ...]:
    tokens = _tokenize(text)
    meaningful = [t for t in tokens if t not in _STOPWORDS and len(t) >= 3]
    return tuple(dict.fromkeys(meaningful or tokens))


def _term_overlap(query_terms: tuple[str, ...], value: str) -> float:
    """Sorgu terimleri ile metin arasındaki örtüşme oranı (0.0–1.0)."""
    if not query_terms or not value:
        return 0.0
    val_tokens = set(_tokenize(value))
    if not val_tokens:
        return 0.0
    matched = sum(1 for t in query_terms if t in val_tokens)
    return matched / len(query_terms)


def _normalize_okas_code(code: str) -> str:
    """OKAS/CPV kodunu normalize eder: boşluk, nokta, tire kaldır."""
    return code.strip().replace(" ", "").replace(".", "").replace("-", "")


class ScoreAggregator:
    """Ortak puan hesaplayıcı — profil ve ihale odaklı modlar için.

    Ağırlıklar IsbakRagSettings nesnesi üzerinden alınır.
    """

    def __init__(self, settings: Any | None = None) -> None:
        if settings is None:
            from app.config.isbak_rag_settings import get_isbak_rag_settings
            settings = get_isbak_rag_settings()
        self._s = settings
        self._validate_weights()

    def _validate_weights(self) -> None:
        s = self._s
        total = (
            s.weight_max_chunk
            + s.weight_top_chunks
            + s.weight_section_diversity
            + s.weight_okas
            + s.weight_title
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Ağırlıkların toplamı 1.0 olmalıdır, alınan: {total:.6f}"
            )

    def compute(
        self,
        *,
        raw_scores: list[float],
        section_types: list[str],
        okas_codes: list[str],
        query_terms: tuple[str, ...],
        tender_name: str = "",
        profile_okas_prefixes: list[str] | None = None,
        strong_terms: list[str] | None = None,
        negative_terms: list[str] | None = None,
        evidence_texts: list[str] | None = None,
    ) -> MatchScoreBreakdown:
        """Puan kırılımını hesaplar.

        Args:
            raw_scores: Chunk bazlı benzerlik skorları listesi.
            section_types: Chunk'ların gerçek section_type değerleri.
            okas_codes: İhale payload'ından alınan normalize edilmiş OKAS kodları.
            query_terms: Sorgudaki anlamlı terimler.
            tender_name: İhale adı (başlık desteği için).
            profile_okas_prefixes: Profil OKAS ön ekleri (örn. "48", "72").
            strong_terms: Profil güçlü terimleri.
            negative_terms: Profil negatif terimleri.
            evidence_texts: Kanıt chunk metinleri (negatif terim araması için).
        """
        s = self._s

        if not raw_scores:
            return MatchScoreBreakdown()

        max_sim = max(raw_scores)
        top_mean = sum(raw_scores) / len(raw_scores)

        # Section diversity — ihale kanıt parçalarının gerçek section_type değerleri
        unique_known_sections = {
            st for st in section_types if st in KNOWN_SECTION_TYPES
        }
        # En az 3 farklı bölüm = 1.0 çeşitlilik
        section_div = min(1.0, len(unique_known_sections) / 3.0)

        # OKAS desteği — profil ön ekleriyle ihale OKAS kodlarını karşılaştır
        okas_sup = self._compute_okas_support(
            okas_codes=okas_codes,
            profile_okas_prefixes=profile_okas_prefixes or [],
            query_terms=query_terms,
        )

        # Başlık desteği
        title_sup = _term_overlap(query_terms, tender_name)

        # Güçlü terim desteği (bonus — skorun üst sınırı 1.0'i aşmaz)
        strong_sup = 0.0
        if strong_terms:
            combined = " ".join(strong_terms)
            strong_sup = min(1.0, _term_overlap(query_terms, combined))

        # Negatif ceza
        neg_penalty = 0.0
        if negative_terms:
            neg_terms_tuple = _query_terms(" ".join(negative_terms))
            if neg_terms_tuple:
                tender_text = tender_name
                if evidence_texts:
                    tender_text += " " + " ".join(evidence_texts)
                neg_overlap = _term_overlap(neg_terms_tuple, tender_text)
                neg_penalty = min(0.30, neg_overlap * 0.30)

        raw_final = (
            s.weight_max_chunk * max_sim
            + s.weight_top_chunks * top_mean
            + s.weight_section_diversity * section_div
            + s.weight_okas * okas_sup
            + s.weight_title * title_sup
        )

        # Negatif ceza uygulaması — 0.0 altına düşürmez
        final = max(0.0, raw_final - neg_penalty)
        # 1.0 üst sınırı
        final = min(1.0, final)

        return MatchScoreBreakdown(
            max_similarity=round(max_sim, 4),
            top_similarity_mean=round(top_mean, 4),
            section_diversity=round(section_div, 4),
            okas_support=round(okas_sup, 4),
            title_support=round(title_sup, 4),
            strong_term_support=round(strong_sup, 4),
            negative_term_penalty=round(neg_penalty, 4),
            final_score=round(final, 4),
        )

    @staticmethod
    def _compute_okas_support(
        *,
        okas_codes: list[str],
        profile_okas_prefixes: list[str],
        query_terms: tuple[str, ...],
    ) -> float:
        """Profil OKAS ön ekleriyle ihale OKAS kodlarını karşılaştırır."""
        if not okas_codes:
            return 0.0

        # Ön ek eşleşmesi
        if profile_okas_prefixes:
            norm_prefixes = [
                _normalize_okas_code(p) for p in profile_okas_prefixes if p
            ]
            norm_codes = [_normalize_okas_code(c) for c in okas_codes if c]
            for code in norm_codes:
                for prefix in norm_prefixes:
                    if code.startswith(prefix):
                        return 1.0
            # Kısmi ön ek eşleşmesi
            for code in norm_codes:
                for prefix in norm_prefixes:
                    if len(prefix) >= 2 and code.startswith(prefix[:2]):
                        return 0.5
            return 0.0

        # Ön ek yoksa eşleşme olmaz, kelime tabanlı kod eşleştirme yapılmaz.
        return 0.0


def build_query_terms(text: str) -> tuple[str, ...]:
    """Harici kullanım için sorgu terimi üreteci."""
    return _query_terms(text)


__all__ = ["ScoreAggregator", "build_query_terms"]
