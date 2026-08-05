"""İSBAK çoklu profil ihale sınıflandırıcısı - sürüm 2.1."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from app.company_profiles import IsbakProfileLoader
from app.domain import TenderRecord

CLASSIFIER_VERSION = "isbak_profile_classifier_v2_2"

SOURCE_WEIGHTS = {
    "tender_title": 5.0,
    "tender_metadata": 4.0,
    "announcement_titles": 3.0,
    "announcement_content": 1.0,
    "tender_characteristics": 3.0,
    "tender_okas_codes": 5.0,
}

STRONG_TERM_WEIGHT = 2.0
SUPPORT_TERM_WEIGHT = 1.0
GENERIC_TERM_WEIGHT = 0.25
NEGATIVE_TERM_WEIGHT = -2.5

STRONG_THRESHOLD = 10.0
CONDITIONAL_THRESHOLD = 6.0
REVIEW_THRESHOLD = 2.0


@dataclass(frozen=True)
class ProfileMatch:
    profile_code: str
    profile_name: str
    raw_score: float
    normalized_score: float
    status: str
    matched_terms: list[str] = field(default_factory=list)
    matched_sources: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    has_strong_term: bool = False
    supporting_term_count: int = 0
    exclusion_count: int = 0
    okas_supported_by_text: bool = False


@dataclass(frozen=True)
class TenderProfileClassification:
    tender_id: str
    ikn: str
    title: str
    primary_profile_code: str | None
    profile_codes: list[str]
    review_profile_codes: list[str]
    evaluation_profile_codes: list[str]
    matches: list[ProfileMatch]
    overall_status: str
    classifier_version: str = CLASSIFIER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"I": "ı", "İ": "i"})).lower()
    text = text.replace("\u0307", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(normalize(term))
    return re.compile(rf"(?<![\wçğıöşü]){escaped}(?![\wçğıöşü])")


def build_source_texts(tender: TenderRecord) -> dict[str, str]:
    return {
        "tender_title": str(tender.adi or ""),
        "tender_metadata": "\n".join(
            filter(
                None,
                [
                    tender.kapsam,
                    tender.ihale_turu,
                    tender.ihale_usulu,
                    tender.idare_adi,
                ],
            )
        ),
        "announcement_titles": "\n".join(
            str(item.baslik) for item in tender.announcements if item.baslik
        ),
        "announcement_content": "\n".join(
            str(item.icerik) for item in tender.announcements if item.icerik
        ),
        "tender_characteristics": "\n".join(
            str(item.ozellik) for item in tender.characteristics if item.ozellik
        ),
        "tender_okas_codes": "\n".join(
            " ".join(filter(None, [str(item.kod or ""), str(item.ad or "")]))
            for item in tender.okas_codes
            if item.kod or item.ad
        ),
    }


def _normalized_okas_code(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


class IsbakTenderProfileClassifier:
    def __init__(self, loader: IsbakProfileLoader | None = None) -> None:
        self.loader = loader or IsbakProfileLoader()

    def classify(self, tender: TenderRecord) -> TenderProfileClassification:
        sources = build_source_texts(tender)
        normalized_sources = {k: normalize(v) for k, v in sources.items()}

        matches: list[ProfileMatch] = []
        for entry in self.loader.list_profiles():
            profile = self.loader.load_profile(entry["profil_kodu"])
            match = self._score_profile(
                profile=profile,
                tender=tender,
                normalized_sources=normalized_sources,
            )
            if match.status != "ilgisiz":
                matches.append(match)

        matches.sort(key=lambda item: (-item.raw_score, item.profile_code))

        # Kesin aday profiller yalnızca güçlü veya koşullu eşleşmelerdir.
        # İnceleme profilleri ayrı tutulur; Qdrant filtrelerine ve otomatik
        # değerlendirme bağlamına doğrudan dahil edilmez.
        selected_matches = [
            item for item in matches if item.status in {"guclu_eslesme", "kosullu_eslesme"}
        ]
        review_matches = [item for item in matches if item.status == "inceleme_gerekli"]

        # AUS, ENT, PLN ve TEK ana teknik profillerdir.
        # OPS profilleri kurulum, bakım ve danışmanlık gibi destek
        # yetkinliklerini temsil eder. Teknik profil bulunduğunda
        # OPS profili birincil profile çıkmamalıdır.
        def profile_priority(profile_code: str) -> int:
            return 1 if profile_code.startswith("OPS-") else 0

        selected_matches.sort(
            key=lambda item: (
                profile_priority(item.profile_code),
                -item.raw_score,
                item.profile_code,
            )
        )

        profile_codes = [item.profile_code for item in selected_matches]
        review_profile_codes = [item.profile_code for item in review_matches]
        primary = profile_codes[0] if profile_codes else None

        evaluation_codes = (
            self.loader.resolve_profile_codes(
                primary_code=primary,
                secondary_codes=profile_codes[1:],
                include_supporting_profiles=True,
            )
            if primary
            else []
        )

        if selected_matches:
            overall_status = selected_matches[0].status
        elif review_matches:
            overall_status = "inceleme_gerekli"
        else:
            overall_status = "ilgisiz"

        return TenderProfileClassification(
            tender_id=str(tender.id),
            ikn=str(tender.ikn),
            title=str(tender.adi or ""),
            primary_profile_code=primary,
            profile_codes=profile_codes,
            review_profile_codes=review_profile_codes,
            evaluation_profile_codes=evaluation_codes,
            matches=matches,
            overall_status=overall_status,
        )

    def _best_term_hits(
        self,
        terms: list[str],
        normalized_sources: dict[str, str],
        term_weight: float,
    ) -> tuple[float, list[str], list[str]]:
        """Aynı terim birden fazla kaynakta geçse bile yalnızca en yüksek kaynağı puanlar."""
        score = 0.0
        matched_terms: list[str] = []
        matched_sources: list[str] = []

        for term in terms:
            pattern = term_pattern(term)
            hits = [
                (SOURCE_WEIGHTS[source], source)
                for source, text in normalized_sources.items()
                if pattern.search(text)
            ]
            if not hits:
                continue
            best_weight, best_source = max(hits)
            score += best_weight * term_weight
            matched_terms.append(term)
            matched_sources.append(best_source)

        return score, matched_terms, matched_sources

    def _score_profile(
        self,
        *,
        profile: dict[str, Any],
        tender: TenderRecord,
        normalized_sources: dict[str, str],
    ) -> ProfileMatch:
        signals = profile.get("ihale_kategori_sinyalleri", {})
        strong_terms = list(signals.get("guclu_terimler", []))
        support_terms = list(signals.get("destekleyici_terimler", []))
        generic_terms = list(signals.get("genel_terimler", []))
        # Yalnızca düşük ağırlıklı sözcüksel sinyal olarak eylem ifadelerini genel terimlere ekle
        action_verbs = list(profile.get("action_verbs", []))
        if action_verbs:
            generic_terms.extend(action_verbs)

        negative_terms = list(signals.get("negatif_terimler", []))
        okas_requires_text = bool(signals.get("okas_metin_destegi_zorunlu", True))

        score = 0.0
        terms: list[str] = []
        sources: list[str] = []
        reasons: list[str] = []

        strong_score, strong_hits, strong_sources = self._best_term_hits(
            strong_terms,
            normalized_sources,
            STRONG_TERM_WEIGHT,
        )
        support_score, support_hits, support_sources = self._best_term_hits(
            support_terms,
            normalized_sources,
            SUPPORT_TERM_WEIGHT,
        )
        generic_score, generic_hits, generic_sources = self._best_term_hits(
            generic_terms,
            normalized_sources,
            GENERIC_TERM_WEIGHT,
        )
        negative_score, negative_hits, negative_sources = self._best_term_hits(
            negative_terms,
            normalized_sources,
            NEGATIVE_TERM_WEIGHT,
        )

        score += strong_score + support_score + generic_score + negative_score
        terms.extend(strong_hits)
        terms.extend(support_hits)
        terms.extend(generic_hits)
        terms.extend(f"-{term}" for term in negative_hits)
        sources.extend(strong_sources + support_sources + generic_sources + negative_sources)

        okas_codes = {
            _normalized_okas_code(value)
            for value in signals.get("okas_kodlari", [])
            if _normalized_okas_code(value)
        }
        okas_prefixes = tuple(
            _normalized_okas_code(value)
            for value in signals.get("okas_kod_on_ekleri", [])
            if _normalized_okas_code(value)
        )

        has_text_support = bool(strong_hits or support_hits)
        okas_hit = False
        for item in tender.okas_codes:
            code = _normalized_okas_code(item.kod)
            if not code:
                continue

            exact = code in okas_codes
            prefix = bool(okas_prefixes and any(code.startswith(value) for value in okas_prefixes))
            if not (exact or prefix):
                continue

            okas_hit = True
            sources.append("tender_okas_codes")
            if exact and (has_text_support or not okas_requires_text):
                score += 8.0
                reasons.append(f"Metin destekli tam OKAS eşleşmesi: {item.kod}")
            elif prefix and has_text_support:
                score += 4.0
                reasons.append(f"Metin destekli OKAS ön ek eşleşmesi: {item.kod}")
            else:
                score += 1.0
                reasons.append(f"Tek başına düşük ağırlıklı OKAS eşleşmesi: {item.kod}")

        exclusion_count = len(negative_hits)
        has_strong_term = bool(strong_hits)
        supporting_term_count = len(support_hits)
        okas_supported_by_text = bool(okas_hit and has_text_support)

        if exclusion_count and not has_strong_term:
            status = "ilgisiz"
        elif has_strong_term and score >= STRONG_THRESHOLD:
            status = "guclu_eslesme"
        elif (
            has_strong_term or supporting_term_count >= 2 or okas_supported_by_text
        ) and score >= CONDITIONAL_THRESHOLD:
            status = "kosullu_eslesme"
        elif (generic_hits or support_hits or okas_hit) and score >= REVIEW_THRESHOLD:
            status = "inceleme_gerekli"
        else:
            status = "ilgisiz"

        if terms:
            reasons.append("Terim eşleşmeleri: " + ", ".join(dict.fromkeys(terms)))

        return ProfileMatch(
            profile_code=str(profile["profil_kodu"]),
            profile_name=str(profile["profil_adi"]),
            raw_score=round(score, 4),
            normalized_score=round(
                min(1.0, max(0.0, score / 20.0)),
                4,
            ),
            status=status,
            matched_terms=list(dict.fromkeys(terms)),
            matched_sources=list(dict.fromkeys(sources)),
            reasons=reasons,
            has_strong_term=has_strong_term,
            supporting_term_count=supporting_term_count,
            exclusion_count=exclusion_count,
            okas_supported_by_text=okas_supported_by_text,
        )


__all__ = [
    "CLASSIFIER_VERSION",
    "IsbakTenderProfileClassifier",
    "ProfileMatch",
    "TenderProfileClassification",
]
