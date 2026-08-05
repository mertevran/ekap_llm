"""
Sorgu niyeti ve profil yönlendirme modülü.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ProfileRoutingSignal:
    primary_groups: list[str]
    secondary_groups: list[str]
    negative_groups: list[str]
    confidence: float
    matched_phrases: list[str]
    ambiguous_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IsbakQueryProfileRouter:
    """Sorgudan profil grubu sinyallerini çıkaran sınıf."""

    _TOKEN_PATTERN = re.compile(r"[a-zçğışöü0-9]+", re.IGNORECASE | re.UNICODE)

    # Mevcut kurallar
    _PROFILE_RULESET: dict[str, dict[str, Any]] = {
        "AUS": {
            "primary_signals": frozenset(
                {
                    "sinyalizasyon",
                    "kavşak",
                    "kavşaklar",
                    "trafik yönetimi",
                    "akıllı kavşak",
                    "dms",
                    "vms",
                    "pts",
                }
            ),
            "context_signals": frozenset(
                {"trafik", "ulaşım", "yol", "köprü", "otopark", "araç sayımı"}
            ),
            "context_threshold": 2,
            "amplifiers": frozenset({"kamera", "sensör", "radar", "dedektör"}),
            "amplifier_context": frozenset({"trafik", "araç", "hız"}),
        },
        "TEK": {
            "primary_signals": frozenset(
                {
                    "yazılım geliştirme",
                    "mobil uygulama",
                    "entegrasyon",
                    "api",
                    "web uygulaması",
                    "platform",
                    "bulut bilişim",
                    "yapay zeka",
                    "makine öğrenmesi",
                    "veri analizi",
                }
            ),
            "context_signals": frozenset(
                {"yazılım", "uygulama", "dijital", "sistem", "veri tabanı"}
            ),
            "context_threshold": 2,
            "amplifiers": frozenset({"geliştirme", "kodlama", "programlama"}),
            "amplifier_context": frozenset({"yazılım", "uygulama"}),
        },
        "ENT": {
            "primary_signals": frozenset(
                {
                    "erişim kontrolü",
                    "kimlik doğrulama",
                    "bina güvenliği",
                    "alarm sistemi",
                    "güvenlik kamerası",
                }
            ),
            "context_signals": frozenset(
                {"güvenlik", "kamera", "izleme", "alarm", "bina", "tesisat"}
            ),
            "context_threshold": 2,
            "amplifiers": frozenset({"kontrol", "izin", "kimlik"}),
            "amplifier_context": frozenset({"güvenlik", "bina"}),
        },
        "OPS": {
            "primary_signals": frozenset(
                {
                    "işletme ve bakım",
                    "bakım onarım",
                    "periyodik bakım",
                    "teknik destek hizmet alımı",
                    "saha hizmetleri",
                }
            ),
            "context_signals": frozenset({"bakım", "onarım", "işletme", "temizlik", "personel"}),
            "context_threshold": 2,
            "amplifiers": frozenset({"sürdürme", "yönetim", "servis"}),
            "amplifier_context": frozenset({"bakım", "işletme"}),
        },
        "PLN": {
            "primary_signals": frozenset({"planlama", "fizibilite", "etüt", "kentsel dönüşüm"}),
            "context_signals": frozenset({"plan", "proje", "tasarım", "harita"}),
            "context_threshold": 2,
            "amplifiers": frozenset(),
            "amplifier_context": frozenset(),
        },
    }

    @classmethod
    def _normalize(cls, text: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(text))
        normalized = normalized.translate(str.maketrans({"I": "ı", "İ": "i"}))
        normalized = normalized.lower().replace("\u0307", "")
        return normalized.strip()

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        return cls._TOKEN_PATTERN.findall(cls._normalize(text))

    def analyze(self, query: str) -> ProfileRoutingSignal:
        """Sorguyu analiz edip Profil Sinyalleri döndürür."""
        norm_q = self._normalize(query)
        terms = set(self._tokenize(norm_q))

        primary_groups = []
        secondary_groups = []
        negative_groups = []
        matched_phrases = []
        ambiguous_terms = []

        for group, rules in self._PROFILE_RULESET.items():
            primary_signals = rules["primary_signals"]
            context_signals = rules["context_signals"]
            context_threshold = rules["context_threshold"]
            amplifiers = rules.get("amplifiers", frozenset())
            amplifier_context = rules.get("amplifier_context", frozenset())

            is_matched = False

            # 1. Birincil sinyal
            for sig in primary_signals:
                if sig in norm_q:
                    is_matched = True
                    matched_phrases.append(sig)

            if is_matched:
                primary_groups.append(group)
                continue

            # 2. Bağlam
            context_hits = [sig for sig in context_signals if sig in terms]
            if len(context_hits) >= context_threshold:
                is_matched = True
                matched_phrases.extend(context_hits)
                primary_groups.append(group)
                continue

            # 3. Amplifier + Bağlam
            if amplifiers and amplifier_context:
                amp_hits = [a for a in amplifiers if a in terms]
                ctx_hits = [c for c in amplifier_context if c in terms]
                if amp_hits and ctx_hits:
                    matched_phrases.extend(amp_hits + ctx_hits)
                    primary_groups.append(group)

        # Mevcut kural setinde negative veya secondary ayrımları, hata analizi sonrasında eklenecek.
        # Şimdilik eşleşenleri primary kabul ediyoruz.

        confidence = 1.0 if primary_groups else 0.0

        # Deduplicate
        primary_groups = list(dict.fromkeys(primary_groups))
        matched_phrases = list(dict.fromkeys(matched_phrases))

        return ProfileRoutingSignal(
            primary_groups=primary_groups,
            secondary_groups=secondary_groups,
            negative_groups=negative_groups,
            confidence=confidence,
            matched_phrases=matched_phrases,
            ambiguous_terms=ambiguous_terms,
        )
