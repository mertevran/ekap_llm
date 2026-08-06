"""İhale metnindeki profil kapsam sinyallerini deterministik olarak doğrular."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from app.decision.models import DecisionValidationContext, NegativeScopeAnalysis

_NON_ALNUM = re.compile(r"[^0-9a-zçğıöşü]+", re.IGNORECASE)
_GENERIC_TRAILING_TOKENS = {
    "alım",
    "alımı",
    "hizmet",
    "hizmeti",
    "kontrollük",
    "kontrollüğü",
    "satınalma",
    "temin",
    "temini",
    "tedarik",
    "tedariki",
}
_SEMANTIC_TOKEN_GROUPS = (
    frozenset({"araç", "arac", "taşıt", "tasit", "otomobil", "minibüs", "minibus", "otobüs", "otobus", "kamyon", "ambulans"}),
    frozenset({"bakım", "bakim", "onarım", "onarim", "tamir", "servis"}),
    frozenset({"kiralama", "kiralık", "kiralik", "kira"}),
    frozenset({"yapım", "yapim", "inşaat", "insaat"}),
    frozenset({"taşıma", "tasima", "taşımacılık", "tasimacilik", "nakliye"}),
    frozenset({"levha", "tabela"}),
    frozenset({"organizasyon", "etkinlik", "festival"}),
    frozenset({"klima", "iklimlendirme"}),
)


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"I": "ı", "İ": "i"}))
    text = text.lower().replace("\u0307", "")
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _normalize_code(value: Any) -> str:
    return "".join(char for char in str(value or "") if char.isalnum()).upper()


def _ordered_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(
        dict.fromkeys(
            text
            for item in values
            if (text := " ".join(str(item).split()).strip())
        )
    )


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_text(phrase)
    if not normalized_text or not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {normalized_text} "


def _token_matches(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if any(expected in group and actual in group for group in _SEMANTIC_TOKEN_GROUPS):
        return True
    if min(len(expected), len(actual)) < 5:
        return False
    common_prefix_length = 0
    for expected_char, actual_char in zip(expected, actual, strict=False):
        if expected_char != actual_char:
            break
        common_prefix_length += 1
    return common_prefix_length >= max(5, min(len(expected), len(actual)) - 2)


def _term_variants(term: str) -> list[list[str]]:
    tokens = _normalize_text(term).split()
    if not tokens:
        return []
    variants = [tokens]
    shortened = list(tokens)
    while len(shortened) > 2 and shortened[-1] in _GENERIC_TRAILING_TOKENS:
        shortened = shortened[:-1]
        variants.append(list(shortened))
    return variants


def _contains_term(normalized_text: str, term: str) -> bool:
    if _contains_phrase(normalized_text, term):
        return True

    text_tokens = normalized_text.split()
    for variant in _term_variants(term):
        # Tek genel sözcük anlamsal ret üretmek için yeterli değildir.
        if len(variant) < 2 or len(text_tokens) < len(variant):
            continue
        maximum_window = len(variant) + 4
        for start in range(len(text_tokens)):
            search_from = start
            matched_positions: list[int] = []
            for expected in variant:
                position = next(
                    (
                        index
                        for index in range(search_from, len(text_tokens))
                        if _token_matches(expected, text_tokens[index])
                    ),
                    None,
                )
                if position is None:
                    break
                matched_positions.append(position)
                search_from = position + 1
            if (
                len(matched_positions) == len(variant)
                and matched_positions[-1] - matched_positions[0] <= maximum_window
            ):
                return True
    return False


def _matching_terms(texts: Iterable[str], terms: list[str]) -> list[str]:
    normalized_texts = [_normalize_text(text) for text in texts if str(text).strip()]
    return [
        term
        for term in terms
        if any(_contains_term(text, term) for text in normalized_texts)
    ]


def analyze_negative_scope(
    context: DecisionValidationContext | None,
) -> NegativeScopeAnalysis:
    """Negatif terimleri yalnızca gerçek ihale başlığı ve kanıtlarında arar.

    OKAS kodları negatif terimi tek başına kanıtlamaz. Kodlar, bulunan metinsel
    sinyalin profil kategorisiyle ilişkisini raporlamak için ayrı tutulur.
    """

    if context is None:
        return NegativeScopeAnalysis()

    signals = context.profile_signals
    if not isinstance(signals, dict):
        return NegativeScopeAnalysis()

    negative_terms = _ordered_strings(signals.get("negatif_terimler"))
    positive_terms = _ordered_strings(signals.get("guclu_terimler"))
    positive_terms.extend(
        term
        for term in _ordered_strings(signals.get("destekleyici_terimler"))
        if term not in positive_terms
    )

    normalized_title = _normalize_text(context.tender_name)
    title_matches = [
        term
        for term in negative_terms
        if _contains_term(normalized_title, term)
    ]

    evidence_terms: list[str] = []
    evidence_chunk_ids: list[str] = []
    for chunk_id, text in context.evidence_text_by_chunk.items():
        normalized_evidence = _normalize_text(text)
        chunk_matches = [
            term
            for term in negative_terms
            if _contains_term(normalized_evidence, term)
        ]
        if not chunk_matches:
            continue
        evidence_chunk_ids.append(str(chunk_id))
        evidence_terms.extend(chunk_matches)

    configured_codes = {
        _normalize_code(code)
        for code in _ordered_strings(signals.get("okas_kodlari"))
        if _normalize_code(code)
    }
    configured_prefixes = {
        _normalize_code(code)
        for code in _ordered_strings(signals.get("okas_kod_on_ekleri"))
        if _normalize_code(code)
    }
    tender_codes = [
        normalized
        for code in context.tender_okas_codes
        if (normalized := _normalize_code(code))
    ]
    matched_okas_codes = [
        code
        for code in tender_codes
        if code in configured_codes
        or any(code.startswith(prefix) for prefix in configured_prefixes)
    ]

    all_evidence_texts = list(context.evidence_text_by_chunk.values())
    positive_matches = _matching_terms(
        [context.tender_name, *all_evidence_texts],
        positive_terms,
    )
    matched_terms = list(dict.fromkeys([*title_matches, *evidence_terms]))
    if not matched_terms:
        scope_type = "none"
    elif positive_matches:
        scope_type = "mixed"
    elif title_matches:
        scope_type = "full"
    else:
        scope_type = "ambiguous"

    return NegativeScopeAnalysis(
        verified=bool(matched_terms),
        matched_terms=matched_terms,
        title_matched_terms=list(dict.fromkeys(title_matches)),
        evidence_matched_terms=list(dict.fromkeys(evidence_terms)),
        evidence_chunk_ids=list(dict.fromkeys(evidence_chunk_ids)),
        matched_okas_codes=list(dict.fromkeys(matched_okas_codes)),
        profile_okas_supported=bool(matched_okas_codes),
        matched_positive_terms=list(dict.fromkeys(positive_matches)),
        scope_type=scope_type,
    )


__all__ = ["analyze_negative_scope"]
