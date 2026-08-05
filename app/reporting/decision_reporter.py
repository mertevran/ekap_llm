import csv
import json
from pathlib import Path

from app.decision.models import FinalTenderDecision


class DecisionReporter:
    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_reports(self, decisions: list[FinalTenderDecision]):
        if not decisions:
            return

        jsonl_path = self.output_dir / "tender_model_decisions.jsonl"
        csv_path = self.output_dir / "tender_model_decisions.csv"
        review_path = self.output_dir / "tender_review_required.csv"

        with open(jsonl_path, "w", encoding="utf-8") as f:
            for d in decisions:
                f.write(json.dumps(d.to_dict(), ensure_ascii=False) + "\n")

        self._write_csv(csv_path, decisions)
        self._write_csv(
            review_path,
            [
                d
                for d in decisions
                if d.human_review_required or d.final_decision == "inceleme_gerekli"
            ],
        )

    def _write_csv(self, path: Path, decisions: list[FinalTenderDecision]):
        if not decisions:
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "tender_id",
                    "ikn",
                    "tender_name",
                    "authority_name",
                    "final_decision",
                    "activity_match",
                    "primary_decision",
                    "secondary_decision",
                    "python_validation_passed",
                    "forced_decision",
                    "primary_profile_code",
                    "secondary_profile_codes",
                    "evaluated_profile_codes",
                    "profile_match_scores",
                    "raw_confidence",
                    "confidence",
                    "confidence_cap",
                    "confidence_calibration_reasons",
                    "negative_scope_verified",
                    "matched_negative_terms",
                    "participation_status",
                    "participation_review_required",
                    "unverified_participation_requirements",
                    "missing_evidence",
                    "human_review_required",
                    "human_review_reason",
                    "evaluated_at",
                ]
            )
            for d in decisions:
                sec_dec = d.secondary_model.decision if d.secondary_model else ""
                missing = d.primary_model.eksik_kanitlar
                writer.writerow(
                    [
                        d.tender_id,
                        d.ikn,
                        d.tender_name,
                        d.authority_name,
                        d.final_decision,
                        d.activity_match,
                        d.primary_model.decision,
                        sec_dec,
                        d.validation.passed,
                        d.validation.forced_decision or "",
                        d.primary_profile_code,
                        "|".join(d.secondary_profile_codes),
                        "|".join(d.evaluated_profile_codes),
                        json.dumps(
                            d.profile_match_scores,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        round(d.confidence_calibration.raw_confidence, 4),
                        round(d.final_confidence, 4),
                        round(d.confidence_calibration.applied_cap, 4),
                        "|".join(d.confidence_calibration.reasons),
                        d.negative_scope_verified,
                        "|".join(d.matched_negative_terms),
                        d.katilim_yeterliligi_durumu,
                        d.participation_review_required,
                        "|".join(d.dogrulanamayan_katilim_sartlari),
                        "|".join(missing),
                        d.human_review_required,
                        d.human_review_reason,
                        d.evaluated_at,
                    ]
                )
