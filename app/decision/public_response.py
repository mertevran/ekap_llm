"""Nihai kararı kullanıcıya yönelik, sınırlı bir sözleşmeye dönüştürür."""

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
class PublicDecisionResponse:
    tender_id: str
    ikn: str
    tender_name: str
    authority_name: str
    decision: str
    confidence: float
    decision_summary: str
    activity_match: str
    primary_profile_code: str
    secondary_profile_codes: list[str] = field(default_factory=list)
    matched_evidences: list[PublicEvidence] = field(default_factory=list)
    unmet_requirements: list[PublicRequirement] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    human_review_required: bool = False
    human_review_reason: str = ""

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


def _summary(decision: "FinalTenderDecision") -> str:
    activity = decision.activity_match
    if activity == "guclu":
        first = "İhale konusu şirketin faaliyet alanıyla güçlü biçimde örtüşmektedir."
    elif activity == "kismi":
        first = "İhale konusu şirketin faaliyet alanıyla kısmen örtüşmektedir."
    elif activity == "zayif":
        first = "İhale konusu ile şirketin faaliyet alanı arasındaki eşleşme zayıftır."
    else:
        first = "İhale konusu ile şirketin faaliyet alanı arasındaki eşleşme kesinleştirilememiştir."

    if decision.final_decision == "uygun":
        reason = next(iter(decision.primary_model.uygunluk_gerekceleri), "")
        second = _first_sentence(reason) or "Doğrulanan kanıtlar uygun kararını desteklemektedir."
    elif decision.final_decision == "uygun_degil":
        if decision.matched_negative_terms:
            terms = ", ".join(decision.matched_negative_terms[:3])
            second = _first_sentence(
                f"İhale, profilin negatif kapsamındaki {terms} alanıyla açıkça çakıştığı için uygun değildir"
            )
        else:
            reason = next(iter(decision.primary_model.uygunsuzluk_gerekceleri), "")
            second = _first_sentence(reason) or "Doğrulanan çelişki nedeniyle ihale uygun değildir."
    else:
        if decision.dogrulanamayan_katilim_sartlari:
            requirement = decision.dogrulanamayan_katilim_sartlari[0]
            second = _first_sentence(
                f"{requirement} şartının karşılandığı doğrulanamadığından insan incelemesi gerekmektedir"
            )
        elif decision.validation.negative_scope.scope_type == "mixed":
            second = "Olumlu ve negatif faaliyet kapsamları birlikte bulunduğundan insan incelemesi gerekmektedir."
        else:
            second = _first_sentence(decision.human_review_reason) or (
                "Kararı etkileyen belirsizlikler nedeniyle insan incelemesi gerekmektedir."
            )

    return f"{first} {second}".strip()


def _evidences(decision: "FinalTenderDecision") -> list[PublicEvidence]:
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


def _requirements(decision: "FinalTenderDecision") -> list[PublicRequirement]:
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
        text = _clean_text(
            assessment.description or assessment.criterion_id,
            240,
        )
        if text and text not in seen:
            seen.add(text)
            requirements.append(PublicRequirement(text, "karsilanmiyor"))
    return requirements[:5]


def build_public_decision_response(
    decision: "FinalTenderDecision",
) -> PublicDecisionResponse:
    return PublicDecisionResponse(
        tender_id=decision.tender_id,
        ikn=decision.ikn,
        tender_name=decision.tender_name,
        authority_name=decision.authority_name,
        decision=decision.final_decision,
        confidence=round(decision.final_confidence, 4),
        decision_summary=_summary(decision),
        activity_match=decision.activity_match,
        primary_profile_code=decision.primary_profile_code,
        secondary_profile_codes=list(decision.secondary_profile_codes),
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
    )


__all__ = [
    "PublicDecisionResponse",
    "PublicEvidence",
    "PublicRequirement",
    "build_public_decision_response",
]
