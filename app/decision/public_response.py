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
class ProfessionalDecisionReasoning:
    """Deterministik Python fonksiyonlarıyla üretilen kurumsal karar gerekçesi.

    Yeni LLM çağrısı yapılmaz. Kaynak: FinalTenderDecision + Python doğrulayıcı
    çıktıları.
    """

    karar_basligi: str
    yonetici_ozeti: str
    teknik_gerekce: str
    katilim_degerlendirmesi: str
    sonuc: str
    inceleme_notu: str = ""


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
    professional_reasoning: ProfessionalDecisionReasoning = field(
        default_factory=lambda: ProfessionalDecisionReasoning(
            karar_basligi="",
            yonetici_ozeti="",
            teknik_gerekce="",
            katilim_degerlendirmesi="",
            sonuc="",
            inceleme_notu="",
        )
    )
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
    # Cümle ortasında kesme — son cümle sınırına kadar al
    shortened = text[:max_chars]
    # Son noktalama işaretine kadar kes
    for i in range(len(shortened) - 1, max(len(shortened) - 80, 0), -1):
        if shortened[i] in ".!?":
            return shortened[: i + 1].strip()
    # Noktalama yoksa kelime sınırında kes
    last_space = shortened.rfind(" ")
    if last_space > max_chars // 2:
        return shortened[:last_space].rstrip(" ,;:-") + "…"
    return shortened.rstrip(" ,;:-") + "…"


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


def _karar_basligi(final_decision: str) -> str:
    mapping = {
        "uygun": "UYGUN",
        "uygun_degil": "UYGUN DEĞİL",
        "inceleme_gerekli": "İNCELEME GEREKLİ",
    }
    return mapping.get(final_decision, final_decision.upper())


def _yonetici_ozeti(decision: FinalTenderDecision) -> str:
    fd = decision.final_decision
    ad = decision.activity_decision
    am = decision.activity_match
    ps = decision.katilim_yeterliligi_durumu

    if fd == "uygun" or ad == "uygun":
        text = "İhale konusu faaliyet açısından şirketin profiliyle teknik uyum göstermektedir."
        if am == "guclu":
            text = "İhale konusu faaliyet açısından şirketin profiliyle güçlü ve doğrudan teknik uyum göstermektedir."

        if ps == "dogrulanmadi":
            text += " Ancak katılım yeterliliğine ilişkin bazı belge veya şartlar mevcut verilerle doğrulanmamıştır."
    elif fd == "uygun_degil":
        text = (
            "İhale konusu ile değerlendirilen şirket profilinin temel faaliyet alanı "
            "arasında yeterli teknik uyum bulunmamaktadır."
        )
    else:
        # inceleme_gerekli (ve uygun + zayif/belirsiz gibi kenar durumlar)
        text = (
            "İhale ile şirket faaliyet alanı arasında potansiyel uyum bulunmakla "
            "birlikte, nihai karar için doğrulanması gereken önemli unsurlar mevcuttur."
        )
    return _clean_text(text, 400)


def _teknik_gerekce(decision: FinalTenderDecision) -> str:
    parts: list[str] = []

    # 1. Doğrulanmış faaliyet eşleşmesi
    if decision.final_decision != "uygun_degil":
        if decision.activity_match == "guclu":
            parts.append(
                "Doğrulanan kanıtlar, ihale kapsamının şirket faaliyet profiliyle "
                "doğrudan ve güçlü teknik uyum içinde olduğunu ortaya koymaktadır."
            )
        elif decision.activity_match == "kismi":
            parts.append(
                "Doğrulanan kanıtlar, ihale kapsamının belirli bölümlerinin şirket "
                "faaliyet profiliyle uyumlu olduğunu göstermektedir."
            )

    # 2. Uygun kısımlar (suitable_parts) varsa
    if decision.suitable_parts:
        kisim_adlari = ", ".join(
            p.part_name for p in decision.suitable_parts[:3] if p.part_name
        )
        if kisim_adlari:
            parts.append(
                f"Doğrulanan ihale kapsamı içinde teknik uyum gösterilen "
                f"bölümler: {kisim_adlari}."
            )

    # 3. Python doğrulayıcıdan gelen kaynak onaylı kriterler
    source_confirmed = [
        a for a in decision.validation.criterion_assessments
        if a.source_status == "mandatory" and a.source_available
    ]
    if source_confirmed:
        criteria_desc = "; ".join(
            _clean_text(a.description or a.criterion_id, 80)
            for a in source_confirmed[:2]
        )
        parts.append(
            f"İhale kaynağında zorunlu olduğu doğrulanan kriterler: {criteria_desc}."
        )

    # 4. Negatif kapsam doğrulanmışsa — yalnızca Python doğrulamasıyla teyit edilmiş
    if decision.negative_scope_verified and decision.matched_negative_terms:
        terms = ", ".join(decision.matched_negative_terms[:3])
        parts.append(
            f"Mevcut kanıtlarda doğrulanan kapsam çelişkisi tespit edilmiştir "
            f"({terms})."
        )

    # 5. Python doğrulamasıyla çelişmeyen model uygunluk gerekçeleri
    if (
        not decision.negative_scope_verified
        and decision.final_decision != "uygun_degil"
        and decision.primary_model.uygunluk_gerekceleri
    ):
        ilk_gerekce = _clean_text(
            decision.primary_model.uygunluk_gerekceleri[0], 200
        )
        if ilk_gerekce and not any(ilk_gerekce in p for p in parts):
            parts.append(
                f"Mevcut kanıtlar kapsamında: {ilk_gerekce}"
                if not ilk_gerekce.endswith(".")
                else f"Mevcut kanıtlar kapsamında: {ilk_gerekce}"
            )

    if not parts:
        parts.append(
            "Mevcut ihale kanıtları ve şirket faaliyet profili "
            "karşılaştırılmıştır."
        )

    full_text = " ".join(parts)
    return _clean_text(full_text, 700)


def _katilim_degerlendirmesi(decision: FinalTenderDecision) -> str:
    unverified = decision.dogrulanamayan_katilim_sartlari
    ps = decision.katilim_yeterliligi_durumu

    if ps == "dogrulanmadi":
        sart_ozeti = ""
        if unverified:
            sart_ozeti = " (" + "; ".join(_clean_text(s, 100) for s in unverified[:3]) + ")"
        text = (
            "Faaliyet alanı açısından teknik uyum değerlendirmesi bağımsız yapılmıştır. "
            f"Bununla birlikte ihaleye katılım için gerekli mali, idari veya teknik "
            f"yeterlilik belge şartları mevcut kaynaklarla tam olarak doğrulanamamıştır{sart_ozeti}. "
            "İhaleye katılım kararı alınması halinde bu belgelerin kontrolü zorunludur."
        )
    elif ps == "karsilanmiyor":
        text = "İhaleye katılım için zorunlu olan mali, idari veya teknik bir şartın açıkça karşılanmadığı tespit edilmiştir."
    else:
        text = (
            "Mevcut analiz kapsamında faaliyet uygunluğunu engelleyen doğrulanmış "
            "bir katılım sorunu tespit edilmemiştir. Nihai teklif öncesinde ihale "
            "belgelerinin insan kontrolünden geçirilmesi her zaman gereklidir."
        )
    return _clean_text(text, 500)


def _sonuc(decision: FinalTenderDecision) -> str:
    fd = decision.final_decision
    pc = decision.primary_profile_code
    ps = decision.katilim_yeterliligi_durumu

    if fd == "uygun":
        # Kısmi teklif + uygun kısımlar varsa
        if decision.partial_offer and decision.suitable_parts:
            text = (
                f"İhalenin yalnız doğrulanan uygun kısımlarının faaliyet açısından {pc} profili "
                f"kapsamında değerlendirilmesi uygundur."
            )
        else:
            text = (
                f"İhalenin faaliyet uyumu açısından {pc} profili kapsamında değerlendirilmesi uygundur."
            )

        if ps == "dogrulanmadi":
            text += " İhaleye katılım için tüm yeterlilik şartlarının doğrulandığı anlamına gelmez, idari evrak kontrolü şarttır."

        # İnsan onayı gerekiyorsa ikinci cümle
        if decision.human_approval_required:
            text += (
                " Olumlu değerlendirme, nihai teklif veya operasyonel işlem "
                "öncesinde insan onayına tabidir."
            )
    elif fd == "uygun_degil":
        text = (
            f"İhalenin {pc} profili kapsamında takip edilmesi "
            "önerilmemektedir."
        )
    else:
        # inceleme_gerekli
        text = (
            "İhale doğrudan elenmemeli; ilgili teknik veya ticari birim "
            "tarafından detaylı incelemeye alınmalıdır."
        )
    return _clean_text(text, 400)


def _inceleme_notu(decision: FinalTenderDecision) -> str:
    if not decision.human_review_required:
        return ""
    reason = _clean_text(decision.human_review_reason, 300)
    if not reason:
        return ""
    return reason


def _build_professional_reasoning(
    decision: FinalTenderDecision,
) -> ProfessionalDecisionReasoning:
    return ProfessionalDecisionReasoning(
        karar_basligi=_karar_basligi(decision.final_decision),
        yonetici_ozeti=_yonetici_ozeti(decision),
        teknik_gerekce=_teknik_gerekce(decision),
        katilim_degerlendirmesi=_katilim_degerlendirmesi(decision),
        sonuc=_sonuc(decision),
        inceleme_notu=_inceleme_notu(decision),
    )


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
        professional_reasoning=_build_professional_reasoning(decision),
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
    "ProfessionalDecisionReasoning",
    "PublicDecisionResponse",
    "PublicEvidence",
    "PublicRequirement",
    "PublicSuitablePart",
    "build_public_decision_response",
]
