import pytest

from app.decision.decision_normalizer import normalize_decision


def test_decision_normalization():
    assert normalize_decision("uygun") == "uygun"
    assert normalize_decision("UYgun") == "uygun"
    assert normalize_decision("doğrudan_uygun") == "uygun"

    assert normalize_decision("uygun_degil") == "uygun_degil"
    assert normalize_decision("ilgisiz") == "uygun_degil"
    assert normalize_decision("uygun değil") == "uygun_degil"
    assert normalize_decision("uygun degil") == "uygun_degil"

    assert normalize_decision("inceleme_gerekli") == "inceleme_gerekli"
    assert normalize_decision("inceleme gerekli") == "inceleme_gerekli"

def test_invalid_decision_raises_error():
    with pytest.raises(ValueError) as exc:
        normalize_decision("bilinmeyen_karar")
    assert "Bilinmeyen karar değeri" in str(exc.value)

def test_empty_decision_raises_error():
    with pytest.raises(ValueError) as exc:
        normalize_decision("")
    assert "Karar değeri boş olamaz" in str(exc.value)
