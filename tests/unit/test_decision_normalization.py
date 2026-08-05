"""Unit testler — karar normalizer."""

from __future__ import annotations

import pytest

from app.decision.decision_normalizer import (
    VALID_DECISIONS,
    is_valid_decision,
    normalize_decision,
)


def test_standard_values_unchanged():
    assert normalize_decision("uygun") == "uygun"
    assert normalize_decision("uygun_degil") == "uygun_degil"
    assert normalize_decision("inceleme_gerekli") == "inceleme_gerekli"


def test_legacy_dogrudan_uygun():
    assert normalize_decision("doğrudan_uygun") == "uygun"
    assert normalize_decision("dogrudan_uygun") == "uygun"


def test_legacy_ilgisiz():
    assert normalize_decision("ilgisiz") == "uygun_degil"


def test_case_insensitive():
    assert normalize_decision("UYGUN") == "uygun"
    assert normalize_decision("Uygun_Degil") == "uygun_degil"


def test_whitespace_stripped():
    assert normalize_decision("  uygun  ") == "uygun"


def test_unknown_value_raises():
    with pytest.raises(ValueError):
        normalize_decision("kosullu_uygun")
    with pytest.raises(ValueError):
        normalize_decision("bilinmiyor")


def test_none_raises():
    with pytest.raises(ValueError):
        normalize_decision(None)


def test_empty_raises():
    with pytest.raises(ValueError):
        normalize_decision("")


def test_is_valid_decision():
    for v in ("uygun", "uygun_degil", "inceleme_gerekli"):
        assert is_valid_decision(v)


def test_is_valid_decision_false():
    assert not is_valid_decision("doğrudan_uygun")
    assert not is_valid_decision(None)
    assert not is_valid_decision("")


def test_valid_decisions_set():
    assert "uygun" in VALID_DECISIONS
    assert "uygun_degil" in VALID_DECISIONS
    assert "inceleme_gerekli" in VALID_DECISIONS
    assert "doğrudan_uygun" not in VALID_DECISIONS
    assert "ilgisiz" not in VALID_DECISIONS
