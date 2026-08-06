from __future__ import annotations

import csv
import json
from types import SimpleNamespace

from app.decision.models import SuitableTenderPart
from app.reporting.decision_reporter import DecisionReporter


class _Decision(SimpleNamespace):
    def to_dict(self):
        return {"ikn": self.ikn, "final_decision": self.final_decision}

    def to_public_dict(self):
        return {
            "ikn": self.ikn,
            "decision": self.final_decision,
            "automatic_action_allowed": False,
        }


def _decision() -> _Decision:
    return _Decision(
        tender_id="1",
        ikn="2026/1",
        tender_name="Kamera ihalesi",
        authority_name="Test",
        final_decision="uygun",
        activity_decision="uygun",
        activity_match="kismi",
        primary_model=SimpleNamespace(
            decision="uygun",
            eksik_kanitlar=[],
        ),
        secondary_model=None,
        validation=SimpleNamespace(
            passed=True,
            forced_decision=None,
            criterion_assessments=[],
        ),
        primary_profile_code="TEST-01",
        secondary_profile_codes=[],
        evaluated_profile_codes=["TEST-01"],
        profile_match_scores={"TEST-01": 0.8},
        confidence_calibration=SimpleNamespace(
            raw_confidence=0.82,
            applied_cap=0.9,
            reasons=[],
        ),
        final_confidence=0.82,
        negative_scope_verified=False,
        matched_negative_terms=[],
        katilim_yeterliligi_durumu="uygulanamaz",
        participation_review_required=False,
        partial_offer=True,
        suitable_parts=[
            SuitableTenderPart(
                part_number="1",
                part_name="Kamera",
                evidence_chunk_ids=["chk_1"],
                reason="Faaliyet eşleşmesi",
            )
        ],
        dogrulanamayan_katilim_sartlari=[],
        optional_missing_evidence=[],
        human_review_required=False,
        human_review_reason="",
        human_approval_required=True,
        human_approval_status="bekliyor",
        automatic_action_allowed=False,
        evaluated_at="2026-08-06T00:00:00+00:00",
    )


def test_reporter_writes_public_contract_and_approval_queue(tmp_path) -> None:
    DecisionReporter(output_dir=str(tmp_path)).write_reports([_decision()])

    public = json.loads(
        (tmp_path / "tender_public_decisions.jsonl").read_text(encoding="utf-8")
    )
    assert public["automatic_action_allowed"] is False

    with (tmp_path / "tender_model_decisions.csv").open(
        encoding="utf-8", newline=""
    ) as file:
        rows = list(csv.reader(file))
    assert len(rows[0]) == len(rows[1])
    assert "human_approval_required" in rows[0]
    assert "suitable_parts" in rows[0]

    with (tmp_path / "tender_human_action_queue.csv").open(
        encoding="utf-8", newline=""
    ) as file:
        action_rows = list(csv.DictReader(file))
    assert action_rows[0]["human_approval_status"] == "bekliyor"
    assert action_rows[0]["automatic_action_allowed"] == "False"
