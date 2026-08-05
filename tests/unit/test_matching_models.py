"""Unit testler — matching veri modelleri."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.matching.models import (
    MatchScoreBreakdown,
    ProfileTenderMatch,
    TenderFinalDecision,
    TenderProfileMatch,
)


def test_match_score_breakdown_defaults():
    b = MatchScoreBreakdown()
    assert b.final_score == 0.0
    assert b.max_similarity == 0.0


def test_match_score_breakdown_valid():
    b = MatchScoreBreakdown(
        max_similarity=0.8,
        top_similarity_mean=0.7,
        section_diversity=0.5,
        okas_support=0.6,
        title_support=0.4,
        final_score=0.72,
    )
    assert b.final_score == 0.72


def test_match_score_breakdown_out_of_range():
    with pytest.raises(ValidationError):
        MatchScoreBreakdown(max_similarity=1.5)  # > 1.0

    with pytest.raises(ValidationError):
        MatchScoreBreakdown(final_score=-0.1)  # < 0.0


def test_profile_tender_match_required_fields():
    m = ProfileTenderMatch(
        profile_code="AUS-01",
        tender_id="T1",
        ikn="2026/1234",
    )
    assert m.profile_code == "AUS-01"
    assert m.retrieval_rank == 0
    assert m.retrieval_score == 0.0


def test_profile_tender_match_score_range():
    with pytest.raises(ValidationError):
        ProfileTenderMatch(
            profile_code="AUS-01",
            tender_id="T1",
            ikn="2026/1234",
            retrieval_score=1.5,  # > 1.0
        )


def test_tender_profile_match_required_fields():
    m = TenderProfileMatch(
        tender_id="T1",
        ikn="2026/1234",
        profile_code="AUS-01",
    )
    assert m.profile_code == "AUS-01"
    assert m.retrieval_rank == 0


def test_tender_final_decision_valid():
    d = TenderFinalDecision(
        tender_id="T1",
        ikn="2026/1234",
        final_decision="uygun",
        final_confidence=0.85,
    )
    assert d.final_decision == "uygun"


def test_tender_final_decision_invalid():
    with pytest.raises(ValidationError):
        TenderFinalDecision(
            tender_id="T1",
            ikn="2026/1234",
            final_decision="doğrudan_uygun",  # eski — geçersiz
        )

    with pytest.raises(ValidationError):
        TenderFinalDecision(
            tender_id="T1",
            ikn="2026/1234",
            final_decision="ilgisiz",  # eski — geçersiz
        )


def test_tender_final_decision_all_valid_values():
    for decision in ("uygun", "uygun_degil", "inceleme_gerekli"):
        d = TenderFinalDecision(
            tender_id="T1",
            ikn="2026/1234",
            final_decision=decision,
        )
        assert d.final_decision == decision
