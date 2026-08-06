"""Nihai kararı kullanıcıya yönelik, güvenli ve insan onay kapılı sözleşmeye çevirir."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.decision.models import FinalTenderDecision

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class PublicEvidence:
    chunk_id: str
    excerpt: str


@dataclass(frozen=True)
class PublicRequirement:
    requirement: str
    status: str


@dataclass(frozen=True)
class PublicSuitablePart:
    kisim_no: str
    kisim_adi: str
    gerekce: str


@dataclass(frozen=True)
class PublicDecisionResponse:
    tender_id: str
    ikn: str
    tender_name: str
    authority_name: str
    decision: str
    activity_decision: str
    activity_match: str
    participation_status: str
    confidence: float
    decision_summary: str
    primary_profile_code: str
    secondary_profile_codes: list[str] = field(default_factory=list)
    kismi_teklif: bool = False
    uygun_kisimlar: list[PublicSuitablePart] = field(default_factory=list)
    matched_evidences: list[PublicEvidence] = field(default_factory=list)
    unmet_requirements: list[PublicRequirement] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    human_review_required: bool = False
    human_review_reason: str = ""
    human_approval_required: bool = False
    human_approval_status: str = "gerekli_degil"
    automatic_action_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rstrip(" ,;:-")
    return shortened + "…"


def _first_sentence(value: str, max_chars: int = 260) -> str:
    clean = _clean_text(value, max_chars)
    if not clean:
        return ""
    sentence = _SENTENCE_END.split(clean, maxsplit=1)[0].strip()
    if sentence[-1:] not in ".!?":
        sentence += "."
    return sentence


def _summary(decision: FinalTenderDecision) -> str:
    if decision.activity_match == "guclu":
        first = "İhale konusu şirketin faaliyet alanıyla güçlü biçimde örtüşmektedir."
    elif decision.activity_match == "kismi":
        first = "İhale konusu şirketin faaliyet alanıyla yalnız belirli kısımlarda örtüşmektedir."
    elif decision.activity_match == "zayif":
        first = "İhale konusu ile şirketin faaliyet alanı arasındaki eşleşme zayıftır."
    else:
        first = "İhale konusu ile şirketin faaliyet alanı arasındaki eşleşme kesinleştirilememiştir."

    if decision.final_decision == "uygun":
        second = "Olumlu sonuç otomatik kullanılamaz; insan onayı zorunludur."
    elif decision.final_decision == "uygun_degil":
        reason = next(iter(decision.primary_model.uygunsuzluk_gerekceleri), "")
        second = _first_sentence(reason) or "Doğrulanan kapsam çelişkisi nedeniyle ihale uygun değildir."
    else:
        second = _first_sentence(decision.human_review_reason) or (
            "Kararı etkileyen belirsizlikler nedeniyle insan incelemesi gerekmektedir."
        )
    return f"{first} {second}".strip()


def _evidences(decision: FinalTenderDecision) -> list[PublicEvidence]:
    context = decision.validation_context
    if context is None:
        return []
    valid_texts = context.evidence_text_by_chunk
    ordered_ids = list(
        dict.fromkeys(
            [
                *decision.primary_used_chunk_ids,
                *decision.primary_model.kullanilan_chunk_idleri,
                *decision.validation.negative_scope.evidence_chunk_ids,
                *(
                    chunk_id
                    for part in decision.suitable_parts
                    for chunk_id in part.evidence_chunk_ids
                ),
            ]
        )
    )
    return [
        PublicEvidence(
            chunk_id=chunk_id,
            excerpt=_clean_text(valid_texts[chunk_id], 200),
        )
        for chunk_id in ordered_ids
        if chunk_id in valid_texts and _clean_text(valid_texts[chunk_id], 200)
    ][:3]


def _requirements(decision: FinalTenderDecision) -> list[PublicRequirement]:
    requirements: list[PublicRequirement] = []
    seen: set[str] = set()
    for value in decision.dogrulanamayan_katilim_sartlari:
        text = _clean_text(value, 240)
        if text and text not in seen:
            seen.add(text)
            requirements.append(PublicRequirement(text, "dogrulanamadi"))
    for assessment in decision.validation.criterion_assessments:
        if assessment.model_status != "karsilanmiyor":
            continue
        text = _clean_text(assessment.description or assessment.criterion_id, 240)
        if text and text not in seen:
            seen.add(text)
            requirements.append(PublicRequirement(text, "karsilanmiyor"))
    return requirements[:5]


def build_public_decision_response(
    decision: FinalTenderDecision,
) -> PublicDecisionResponse:
    return PublicDecisionResponse(
        tender_id=decision.tender_id,
        ikn=decision.ikn,
        tender_name=decision.tender_name,
        authority_name=decision.authority_name,
        decision=decision.final_decision,
        activity_decision=decision.activity_decision,
        activity_match=decision.activity_match,
        participation_status=decision.katilim_yeterliligi_durumu,
        confidence=round(decision.final_confidence, 4),
        decision_summary=_summary(decision),
        primary_profile_code=decision.primary_profile_code,
        secondary_profile_codes=list(decision.secondary_profile_codes),
        kismi_teklif=decision.partial_offer,
        uygun_kisimlar=[
            PublicSuitablePart(
                kisim_no=part.part_number,
                kisim_adi=_clean_text(part.part_name, 300),
                gerekce=_clean_text(part.reason, 300),
            )
            for part in decision.suitable_parts
        ],
        matched_evidences=_evidences(decision),
        unmet_requirements=_requirements(decision),
        conflicts=[
            _clean_text(item, 240)
            for item in decision.validation.contradictions
            if _clean_text(item, 240)
        ][:3],
        human_review_required=decision.human_review_required,
        human_review_reason=(
            _clean_text(decision.human_review_reason, 300)
            if decision.human_review_required
            else ""
        ),
        human_approval_required=decision.human_approval_required,
        human_approval_status=decision.human_approval_status,
        automatic_action_allowed=False,
    )


__all__ = [
    "PublicDecisionResponse",
    "PublicEvidence",
    "PublicRequirement",
    "PublicSuitablePart",
    "build_public_decision_response",
]
