"""Unit testler — DecisionAggregator."""

from __future__ import annotations

from app.matching.decision_aggregator import DecisionAggregator


def _d(decision: str, confidence: float = 0.8, profile_code: str = "TST"):
    return {
        "profile_code": profile_code,
        "profile_name": profile_code,
        "final_decision": decision,
        "final_confidence": confidence,
        "positive_reasons": [f"{profile_code} uygun gerekçe"],
        "negative_reasons": [],
        "missing_evidence": [],
        "human_review_required": False,
        "human_review_reason": "",
    }


def test_any_uygun_gives_uygun():
    agg = DecisionAggregator()
    result = agg.aggregate(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Test",
        authority_name="Test",
        best_retrieval_score=0.8,
        profile_decisions=[
            _d("uygun", 0.9, "P1"),
            _d("uygun_degil", 0.5, "P2"),
        ],
    )
    assert result.final_decision == "uygun"
    assert result.primary_profile_code == "P1"


def test_all_uygun_degil_gives_uygun_degil():
    agg = DecisionAggregator()
    result = agg.aggregate(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Test",
        authority_name="Test",
        best_retrieval_score=0.5,
        profile_decisions=[
            _d("uygun_degil", 0.8, "P1"),
            _d("uygun_degil", 0.6, "P2"),
        ],
    )
    assert result.final_decision == "uygun_degil"


def test_no_uygun_with_inceleme_gives_inceleme():
    agg = DecisionAggregator()
    result = agg.aggregate(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Test",
        authority_name="Test",
        best_retrieval_score=0.6,
        profile_decisions=[
            _d("inceleme_gerekli", 0.7, "P1"),
            _d("uygun_degil", 0.4, "P2"),
        ],
    )
    assert result.final_decision == "inceleme_gerekli"
    assert result.human_review_required is True


def test_highest_confidence_uygun_is_primary():
    agg = DecisionAggregator()
    result = agg.aggregate(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Test",
        authority_name="Test",
        best_retrieval_score=0.85,
        profile_decisions=[
            _d("uygun", 0.7, "P1"),
            _d("uygun", 0.9, "P2"),  # en yüksek güven
        ],
    )
    assert result.final_decision == "uygun"
    assert result.primary_profile_code == "P2"
    assert "P1" in result.supporting_profile_codes


def test_empty_decisions_gives_inceleme():
    agg = DecisionAggregator()
    result = agg.aggregate(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Test",
        authority_name="Test",
        best_retrieval_score=0.0,
        profile_decisions=[],
    )
    assert result.final_decision == "inceleme_gerekli"
    assert result.human_review_required is True


def test_evaluated_codes_populated():
    agg = DecisionAggregator()
    result = agg.aggregate(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Test",
        authority_name="Test",
        best_retrieval_score=0.7,
        profile_decisions=[
            _d("uygun", 0.8, "P1"),
            _d("uygun_degil", 0.5, "P2"),
            _d("inceleme_gerekli", 0.6, "P3"),
        ],
    )
    assert set(result.evaluated_profile_codes) == {"P1", "P2", "P3"}


def test_human_review_propagated():
    agg = DecisionAggregator()
    pd = _d("uygun", 0.9, "P1")
    pd["human_review_required"] = True
    pd["human_review_reason"] = "Belirsizlik var."

    result = agg.aggregate(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Test",
        authority_name="Test",
        best_retrieval_score=0.9,
        profile_decisions=[pd],
    )
    assert result.human_review_required is True
    assert "Belirsizlik var." in result.human_review_reason
