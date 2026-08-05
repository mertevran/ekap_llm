"""Model güven puanını kanıt gücüne göre konservatif biçimde sınırlar."""

from __future__ import annotations

from app.decision.models import (
    ConfidenceCalibration,
    DecisionLabel,
    ModelDecision,
    ValidationResult,
)


def calibrate_confidence(
    *,
    model_decision: ModelDecision,
    final_decision: DecisionLabel,
    validation: ValidationResult,
    evidence_count: int,
    retrieval_score: float,
    raw_confidence: float | None = None,
    context_available: bool = True,
) -> ConfidenceCalibration:
    """Model puanını yükseltmeden, doğrulanabilir üst sınırlar uygular."""

    source_confidence = (
        model_decision.confidence
        if raw_confidence is None
        else raw_confidence
    )
    raw = min(1.0, max(0.0, float(source_confidence)))
    caps: list[tuple[float, str]] = []

    if validation.source_external_information_used:
        caps.append((0.40, "Model kaynak dışı bilgi kullandığını bildirdi."))
    elif validation.has_blocking_issue:
        caps.append((0.55, "Python doğrulaması kritik bir çelişki buldu."))

    if final_decision == "inceleme_gerekli":
        caps.append((0.70, "İnceleme gerekli kararında güven 0,70 ile sınırlandı."))

    if context_available:
        if evidence_count <= 0:
            caps.append((0.45, "Karar bağlamında doğrulanabilir ihale kanıtı yok."))
        elif evidence_count == 1:
            caps.append((0.65, "Karar yalnızca tek ihale kanıtına dayanıyor."))

        used_evidence_count = len(set(model_decision.kullanilan_chunk_idleri))
        if final_decision != "inceleme_gerekli" and used_evidence_count == 0:
            caps.append((0.60, "Model nihai karar için kanıt parçası belirtmedi."))

        if model_decision.faaliyet_eslesmesi == "belirsiz":
            caps.append((0.65, "Faaliyet eşleşmesi belirsiz olarak bildirildi."))
        elif model_decision.faaliyet_eslesmesi == "zayif":
            caps.append((0.75, "Faaliyet eşleşmesi zayıf olarak bildirildi."))

        if (
            model_decision.negatif_kapsam_cakismasi
            and not validation.negative_scope.verified
        ):
            caps.append((0.55, "Negatif kapsam beyanı ihale kaynaklarında doğrulanamadı."))

        if 0.0 < retrieval_score < 0.40:
            caps.append((0.65, "Aday getirme puanı 0,40 değerinin altında."))
        elif 0.0 < retrieval_score < 0.55:
            caps.append((0.78, "Aday getirme puanı orta-alt düzeyde."))

    applied_cap = min((cap for cap, _ in caps), default=1.0)
    calibrated = min(raw, applied_cap)
    active_reasons = [reason for cap, reason in caps if cap == applied_cap]

    return ConfidenceCalibration(
        raw_confidence=round(raw, 4),
        calibrated_confidence=round(calibrated, 4),
        applied_cap=round(applied_cap, 4),
        reasons=active_reasons,
    )


__all__ = ["calibrate_confidence"]
