"""Modelin bildirdiği ihale kriterlerini gerçek kaynak metniyle doğrular."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.decision.models import (
    CriterionEvidenceAssessment,
    CriterionResult,
)

_WORD_RE = re.compile(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]+")
_NON_ALNUM = re.compile(r"[^0-9a-zçğıöşü]+", re.IGNORECASE)

_NOT_REQUIRED_MARKERS = (
    "belirtilmemiştir",
    "belirtilmemektedir",
    "belirtilmeyecektir",
    "istenmemiştir",
    "istenmemektedir",
    "istenmeyecektir",
    "aranmamaktadır",
    "aranmayacaktır",
    "öngörülmemiştir",
    "öngörülmemektedir",
    "talep edilmemektedir",
    "talep edilmeyecektir",
    "kriter bulunmamaktadır",
    "şart bulunmamaktadır",
    "mevcut değildir",
)

_MANDATORY_MARKERS = (
    "zorunludur",
    "zorunlu tutulmuştur",
    "istenmektedir",
    "istenecektir",
    "aranmaktadır",
    "aranacaktır",
    "talep edilmektedir",
    "sunulmalıdır",
    "sunulması gerekir",
    "sunmaları gerekir",
    "ibraz edilmelidir",
    "ibraz edilmesi gerekir",
    "sahip olmalıdır",
    "sahip olmak",
    "sağlamalıdır",
    "az olmamak üzere",
    "iş deneyimini gösteren",
    "belgelerin sunulması",
    "taşıması gereken kriterler",
    "ilişkin belgeler",
)

_AFFIRMATIVE_MANDATORY_MARKERS = (
    "zorunludur",
    "zorunlu tutulmuştur",
    "istenmektedir",
    "istenecektir",
    "aranmaktadır",
    "aranacaktır",
    "talep edilmektedir",
    "sunulmalıdır",
    "sunulması gerekir",
    "sunmaları gerekir",
    "ibraz edilmelidir",
    "ibraz edilmesi gerekir",
    "sahip olmalıdır",
    "sahip olmak",
    "sağlamalıdır",
    "az olmamak üzere",
)

_NON_BLOCKING_MARKERS = (
    "yönetimindeki görevliler",
    "ortaklar ve ortaklık oranları",
    "ortaklar/kurucular",
    "ortaklar veya kurucular",
    "teklif mektubu",
    "geçici teminat",
    "elektronik teklif",
    "elektronik eksiltme",
    "ekap üzerinden",
    "e-imza",
    "e imza",
    "imza beyannamesi",
    "imza sirküleri",
    "vekaletname",
    "tebligat adresi",
    "fiyat avantajı",
)

_GENERIC_ANCHOR_TOKENS = {
    "acikca",
    "açıkça",
    "asgari",
    "belge",
    "belgeler",
    "belgesi",
    "belirlenen",
    "bilgi",
    "eksikligi",
    "eksikliği",
    "ihale",
    "ihalede",
    "kriter",
    "kriterler",
    "sart",
    "şart",
    "sartname",
    "şartname",
    "şartnamesinde",
    "zorunlu",
}


def _normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"I": "ı", "İ": "i"}))
    text = text.lower().replace("\u0307", "")
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _token_matches_anchor(token: str, anchor: str) -> bool:
    normalized_token = _normalize_text(token)
    normalized_anchor = _normalize_text(anchor)
    if not normalized_token or not normalized_anchor:
        return False
    if normalized_token == normalized_anchor:
        return True
    if min(len(normalized_token), len(normalized_anchor)) < 5:
        return False
    return (
        normalized_token.startswith(normalized_anchor)
        or normalized_anchor.startswith(normalized_token)
    )


def _criterion_anchors(criterion: CriterionResult) -> list[str]:
    combined = _normalize_text(
        " ".join(
            (
                criterion.criterion_id,
                criterion.description,
                criterion.explanation,
            )
        )
    )
    anchors: list[str] = []

    known_groups = (
        (("iş deneyim", "is deneyim", "benzer iş", "benzer is"), ("deneyim", "benzer")),
        (("personel",), ("personel",)),
        (("ekipman", "makine", "teçhizat", "techizat", "tesis"), ("ekipman", "makine", "teçhizat", "tesis")),
        (("sertifika", "iso ", "ts en", "tse "), ("sertifika", "iso", "tse")),
        (("mesleki", "teknik yeter"), ("mesleki", "yeterlik", "yeterlilik")),
        (("mali yeter", "bilanço", "bilanco", "ciro"), ("mali", "bilanço", "ciro")),
        (("ortak", "kurucu", "yönetimindeki", "yonetimindeki"), ("ortak", "kurucu", "görevli", "yönetim")),
        (("yetki belgesi",), ("yetki",)),
        (("kritik teknik",), ("kritik", "teknik")),
    )
    for triggers, group_anchors in known_groups:
        if any(_normalize_text(trigger) in combined for trigger in triggers):
            anchors.extend(group_anchors)

    standard_tokens = re.findall(r"\b(?:iso|tse|ts en)\s*[0-9][0-9a-z -]*", combined)
    anchors.extend(standard_tokens)
    if anchors:
        return _ordered_unique(_normalize_text(anchor) for anchor in anchors)

    for token in combined.split():
        if (
            len(token) >= 5
            and token not in _GENERIC_ANCHOR_TOKENS
            and not token.isdigit()
            and not re.fullmatch(r"zor\s*0*[1-5]", token)
        ):
            anchors.append(token)
    return _ordered_unique(anchors[:8])


def _relevant_excerpts(text: str, anchors: list[str]) -> list[str]:
    if not text.strip() or not anchors:
        return []

    excerpts: list[str] = []
    for match in _WORD_RE.finditer(text):
        token = match.group(0)
        if not any(_token_matches_anchor(token, anchor) for anchor in anchors):
            continue
        start = max(0, match.start() - 220)
        end = min(len(text), match.end() + 220)
        excerpt = " ".join(text[start:end].split()).strip()
        if excerpt:
            excerpts.append(excerpt)
    return _ordered_unique(excerpts)


def _matching_markers(texts: Iterable[str], markers: tuple[str, ...]) -> list[str]:
    normalized_texts = [_normalize_text(text) for text in texts]
    return [
        marker
        for marker in markers
        if any(_normalize_text(marker) in text for text in normalized_texts)
    ]


def assess_criterion_evidence(
    criterion: CriterionResult,
    evidence_text_by_chunk: dict[str, str],
) -> CriterionEvidenceAssessment:
    """Bir kriter iddiasının kaynakta zorunlu şart olup olmadığını sınıflandırır."""

    evidence_chunk_ids = _ordered_unique(
        str(chunk_id).strip()
        for chunk_id in criterion.evidence_chunk_ids
        if str(chunk_id).strip()
    )
    available_sources = [
        (chunk_id, str(evidence_text_by_chunk.get(chunk_id) or "").strip())
        for chunk_id in evidence_chunk_ids
        if str(evidence_text_by_chunk.get(chunk_id) or "").strip()
    ]
    source_available = bool(available_sources)
    if not source_available:
        return CriterionEvidenceAssessment(
            criterion_id=criterion.criterion_id,
            description=criterion.description,
            model_status=criterion.status,
            source_status="unverified",
            source_available=False,
            evidence_chunk_ids=evidence_chunk_ids,
            reason="Kriterin işaret ettiği kaynak metni doğrulama bağlamında bulunamadı.",
        )

    anchors = _criterion_anchors(criterion)
    excerpts_by_chunk: dict[str, list[str]] = {
        chunk_id: _relevant_excerpts(text, anchors)
        for chunk_id, text in available_sources
    }
    relevant_excerpts = [
        excerpt
        for excerpts in excerpts_by_chunk.values()
        for excerpt in excerpts
    ]
    matched_chunk_ids = [
        chunk_id
        for chunk_id, excerpts in excerpts_by_chunk.items()
        if excerpts
    ]
    if not relevant_excerpts:
        return CriterionEvidenceAssessment(
            criterion_id=criterion.criterion_id,
            description=criterion.description,
            model_status=criterion.status,
            source_status="unverified",
            source_available=True,
            evidence_chunk_ids=evidence_chunk_ids,
            reason=(
                "Kaynak metni mevcut; ancak modelin bildirdiği kriteri kanıtlayan "
                "ilgili ifade bulunamadı."
            ),
        )

    not_required_markers = _matching_markers(
        relevant_excerpts,
        _NOT_REQUIRED_MARKERS,
    )
    mandatory_markers = _matching_markers(
        relevant_excerpts,
        _MANDATORY_MARKERS,
    )
    affirmative_mandatory_markers = [
        marker
        for marker in mandatory_markers
        if marker in _AFFIRMATIVE_MANDATORY_MARKERS
    ]
    matched_phrases = _ordered_unique(
        [*not_required_markers, *mandatory_markers]
    )
    source_excerpt = relevant_excerpts[0][:500]

    if not_required_markers and not affirmative_mandatory_markers:
        source_status = "not_required"
        reason = "Kaynak, bu kriterin istenmediğini veya belirtilmediğini açıkça söylüyor."
    elif mandatory_markers and not not_required_markers:
        combined_text = _normalize_text(
            " ".join(
                (
                    criterion.criterion_id,
                    criterion.description,
                    *relevant_excerpts,
                )
            )
        )
        if any(
            _normalize_text(marker) in combined_text
            for marker in _NON_BLOCKING_MARKERS
        ):
            source_status = "non_blocking"
            reason = (
                "Kaynakta standart idari/teklif süreci şartı var; bu kayıt faaliyet "
                "uygunluğunu veya şirket kapasitesini tek başına engellemez."
            )
        else:
            source_status = "mandatory"
            reason = "Kaynak, karar açısından zorunlu bir yeterlilik şartını doğruluyor."
    elif not_required_markers and affirmative_mandatory_markers:
        source_status = "unverified"
        reason = (
            "Aynı kaynak çevresinde hem zorunluluk hem olumsuzluk ifadesi bulundu; "
            "kriter otomatik engelleyici yapılmadı."
        )
    else:
        source_status = "unverified"
        reason = (
            "Kaynakta ilgili başlık bulundu; fakat açık bir zorunluluk ifadesi "
            "doğrulanamadı."
        )

    return CriterionEvidenceAssessment(
        criterion_id=criterion.criterion_id,
        description=criterion.description,
        model_status=criterion.status,
        source_status=source_status,
        source_available=True,
        evidence_chunk_ids=evidence_chunk_ids,
        matched_chunk_ids=matched_chunk_ids,
        matched_phrases=matched_phrases,
        source_excerpt=source_excerpt,
        reason=reason,
    )


__all__ = ["assess_criterion_evidence"]
