"""Karar değeri normalizer.

Sistemin tüm bileşenlerinde yalnızca üç standart karar değeri kullanılır:

    uygun
    uygun_degil
    inceleme_gerekli

Eski değerler (doğrudan_uygun, ilgisiz) yalnızca bu modül aracılığıyla
yeni değerlere dönüştürülür. Veritabanına ve raporlara hiçbir zaman eski
karar değeri yazılmaz.
"""

from __future__ import annotations

from app.decision.models import DecisionLabel

# Eski → Yeni dönüşüm tablosu
_LEGACY_MAPPING: dict[str, DecisionLabel] = {
    "doğrudan_uygun": "uygun",
    "dogrudan_uygun": "uygun",
    "ilgisiz": "uygun_degil",
    "uygun değil": "uygun_degil",
    "uygun degil": "uygun_degil",
    "inceleme gerekli": "inceleme_gerekli",
    # Mevcut yeni değerler — aynı kalır
    "uygun": "uygun",
    "uygun_degil": "uygun_degil",
    "inceleme_gerekli": "inceleme_gerekli",
}

VALID_DECISIONS: frozenset[str] = frozenset(
    {"uygun", "uygun_degil", "inceleme_gerekli"}
)


def normalize_decision(raw: str | None) -> DecisionLabel:
    """Ham karar değerini standart forma dönüştürür.

    Args:
        raw: Model veya veritabanından gelen ham karar değeri.

    Returns:
        Standart karar değeri.

    Raises:
        ValueError: Bilinmeyen veya dönüştürülemeyen değer.
    """
    if not raw:
        raise ValueError("Karar değeri boş olamaz.")

    normalized = str(raw).strip().lower()
    result = _LEGACY_MAPPING.get(normalized)
    if result is None:
        raise ValueError(
            f"Bilinmeyen karar değeri: {raw!r}. "
            f"Geçerli değerler: {sorted(VALID_DECISIONS)}"
        )
    return result


def is_valid_decision(value: str | None) -> bool:
    """Değerin geçerli bir standart karar olup olmadığını kontrol eder."""
    if not value:
        return False
    return str(value).strip() in VALID_DECISIONS


__all__ = [
    "VALID_DECISIONS",
    "is_valid_decision",
    "normalize_decision",
]
