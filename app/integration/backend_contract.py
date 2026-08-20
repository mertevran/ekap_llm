"""LLM V2 nihai kararını mevcut backend HTTP sözleşmesine dönüştürür.

Bu modül bir anti-corruption layer / adapter katmanıdır. LLM karar modelinin
alanları backend DTO'suna sızmaz; backend'in eski sözleşmesi de karar motorunun
iç modelini değiştirmez.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from app.decision.models import FinalTenderDecision

BACKEND_DECISION_MAP: dict[str, str] = {
    "uygun": "uygun",
    "uygun_degil": "uygun_degil",
    "inceleme_gerekli": "belirsiz",
}

_BACKEND_ALLOWED_DECISIONS = frozenset(BACKEND_DECISION_MAP.values())
_BACKEND_FIELDS = frozenset(
    {
        "ikn",
        "tender_id",
        "karar",
        "ilgi_skoru",
        "en_ust_benzerlik",
        "eslesen_paket",
        "gerekce",
        "on_filtre",
        "notlar",
        "sure_sn",
    }
)


class BackendContractError(ValueError):
    """Backend entegrasyon sözleşmesi ihlal edildiğinde üretilir."""


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _finite_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BackendContractError(
            f"{field_name} sayısal bir değer olmalıdır."
        ) from exc
    if not math.isfinite(number):
        raise BackendContractError(f"{field_name} sonlu bir sayı olmalıdır.")
    return number


def _score_0_1(value: Any, *, field_name: str) -> float | None:
    number = _finite_float(value, field_name=field_name)
    if number is not None and not 0.0 <= number <= 1.0:
        raise BackendContractError(f"{field_name} 0 ile 1 arasında olmalıdır.")
    return number


def _highest_profile_score(decision: FinalTenderDecision) -> float | None:
    if not decision.profile_match_scores:
        return None
    scores = [
        _score_0_1(value, field_name="profile_match_scores")
        for value in decision.profile_match_scores.values()
    ]
    concrete = [value for value in scores if value is not None]
    return max(concrete) if concrete else None


def _professional_reason(decision: FinalTenderDecision) -> str | None:
    """Backend için kısa ve bağımsız bir gerekçe üretir.

    Bu adapter, ``public_response`` katmanına bilinçli olarak bağımlı değildir.
    Böylece backend sözleşmesine dönüştürme işlemi yalnızca
    ``FinalTenderDecision`` üzerinde çalışır ve ``validation_context`` gibi
    frontend/public-response ayrıntılarını gerektirmez.
    """

    reasons: list[str] = []

    primary_model = getattr(decision, "primary_model", None)
    if primary_model is not None:
        for attr_name in (
            "uygunluk_gerekceleri",
            "uygunsuzluk_gerekceleri",
        ):
            values = getattr(primary_model, attr_name, None) or []
            for value in values:
                text = " ".join(str(value or "").split()).strip()
                if text and text not in reasons:
                    reasons.append(text)

    human_review_reason = _optional_text(
        getattr(decision, "human_review_reason", None)
    )
    if human_review_reason and human_review_reason not in reasons:
        reasons.append(human_review_reason)

    if reasons:
        return " ".join(reasons)

    final_decision = getattr(decision, "final_decision", None)
    fallback_by_decision = {
        "uygun": (
            "İhale, değerlendirilen şirket profili açısından uygun bulunmuştur."
        ),
        "uygun_degil": (
            "İhale, değerlendirilen şirket profili açısından uygun bulunmamıştır."
        ),
        "inceleme_gerekli": (
            "İhale için kesin otomatik karar verilememiştir; "
            "manuel inceleme gerekmektedir."
        ),
    }
    return fallback_by_decision.get(final_decision)


def final_decision_to_backend_item(
    decision: FinalTenderDecision,
    *,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Bir ``FinalTenderDecision`` nesnesini backend DTO JSON şekline çevirir.

    LLM V2 içindeki ``inceleme_gerekli`` etiketi, değiştirilmeksizin yalnızca
    entegrasyon sınırında backend/frontend sözleşmesindeki ``belirsiz`` etiketine
    çevrilir.
    """

    try:
        backend_decision = BACKEND_DECISION_MAP[decision.final_decision]
    except KeyError as exc:
        raise BackendContractError(
            f"Desteklenmeyen LLM karar etiketi: {decision.final_decision!r}"
        ) from exc

    item: dict[str, Any] = {
        "ikn": str(decision.ikn or "").strip(),
        "tender_id": _optional_text(decision.tender_id),
        "karar": backend_decision,
        "ilgi_skoru": _score_0_1(
            decision.final_confidence,
            field_name="ilgi_skoru",
        ),
        "en_ust_benzerlik": _highest_profile_score(decision),
        "eslesen_paket": _optional_text(decision.primary_profile_code),
        "gerekce": _professional_reason(decision),
        "on_filtre": None,
        "notlar": {
            "pipeline_version": "llm-dev-mert-v2",
            "original_decision": decision.final_decision,
            "activity_decision": decision.activity_decision,
            "activity_match": decision.activity_match,
            "participation_status": decision.katilim_yeterliligi_durumu,
            "human_review_required": decision.human_review_required,
            "human_review_reason": decision.human_review_reason,
            "human_approval_required": decision.human_approval_required,
            "human_approval_status": decision.human_approval_status,
            "automatic_action_allowed": decision.automatic_action_allowed,
            "validation_passed": (
                decision.validation.get("passed")
                if isinstance(decision.validation, dict)
                else getattr(decision.validation, "passed", None)
            ),
            "validation_forced_decision": (
                decision.validation.get("forced_decision")
                if isinstance(decision.validation, dict)
                else getattr(decision.validation, "forced_decision", None)
            ),
            "validation_issues": [
                {
                    "code": (
                        issue.get("code")
                        if isinstance(issue, dict)
                        else getattr(issue, "code", None)
                    ),
                    "message": (
                        issue.get("message")
                        if isinstance(issue, dict)
                        else getattr(issue, "message", None)
                    ),
                    "severity": (
                        issue.get("severity")
                        if isinstance(issue, dict)
                        else getattr(issue, "severity", None)
                    ),
                    "source": (
                        issue.get("source")
                        if isinstance(issue, dict)
                        else getattr(issue, "source", None)
                    ),
                }
                for issue in (
                    decision.validation.get("issues", [])
                    if isinstance(decision.validation, dict)
                    else getattr(decision.validation, "issues", [])
                )
            ],
            "negative_scope_verified": decision.negative_scope_verified,
            "matched_negative_terms": list(decision.matched_negative_terms),
            "evaluated_profile_codes": list(decision.evaluated_profile_codes),
            "secondary_profile_codes": list(decision.secondary_profile_codes),
            "evaluated_at": decision.evaluated_at,
        },
        "sure_sn": _finite_float(duration_seconds, field_name="sure_sn"),
    }

    return validate_backend_item(item)


def validate_backend_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Backend ``ImportAiEvaluationItemDto`` sözleşmesini LLM tarafında doğrular."""

    unknown = set(item) - _BACKEND_FIELDS
    if unknown:
        raise BackendContractError(
            "Backend sözleşmesinde olmayan alanlar bulundu: "
            + ", ".join(sorted(unknown))
        )

    ikn = str(item.get("ikn") or "").strip()
    if not ikn:
        raise BackendContractError("ikn zorunludur ve boş olamaz.")

    karar = str(item.get("karar") or "").strip()
    if karar not in _BACKEND_ALLOWED_DECISIONS:
        raise BackendContractError(
            "karar yalnızca uygun, belirsiz veya uygun_degil olabilir."
        )

    validated = dict(item)
    validated["ikn"] = ikn
    validated["karar"] = karar
    validated["ilgi_skoru"] = _score_0_1(
        item.get("ilgi_skoru"), field_name="ilgi_skoru"
    )
    validated["en_ust_benzerlik"] = _score_0_1(
        item.get("en_ust_benzerlik"), field_name="en_ust_benzerlik"
    )
    validated["sure_sn"] = _finite_float(item.get("sure_sn"), field_name="sure_sn")

    return validated