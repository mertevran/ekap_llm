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
        public_jsonl_path = self.output_dir / "tender_public_decisions.jsonl"
        public_csv_path = self.output_dir / "tender_public_decisions.csv"
        csv_path = self.output_dir / "tender_model_decisions.csv"
        review_path = self.output_dir / "tender_review_required.csv"
        human_action_path = self.output_dir / "tender_human_action_queue.csv"
        professional_csv_path = self.output_dir / "tender_professional_decisions.csv"

        with open(jsonl_path, "w", encoding="utf-8") as f:
            for d in decisions:
                f.write(json.dumps(d.to_dict(), ensure_ascii=False) + "\n")

        with open(public_jsonl_path, "w", encoding="utf-8") as f:
            for d in decisions:
                f.write(
                    json.dumps(d.to_public_dict(), ensure_ascii=False) + "\n"
                )

        self._write_public_csv(public_csv_path, decisions)
        self._write_csv(csv_path, decisions)
        self._write_csv(
            review_path,
            [
                d
                for d in decisions
                if d.human_review_required or d.final_decision == "inceleme_gerekli"
            ],
        )
        self._write_csv(
            human_action_path,
            [
                d
                for d in decisions
                if d.human_review_required or d.human_approval_required
            ],
        )
        self._write_professional_csv(professional_csv_path, decisions)

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
                    "activity_decision",
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
                    "criterion_source_assessments",
                    "participation_status",
                    "participation_review_required",
                    "partial_offer",
                    "suitable_parts",
                    "validated_unverified_participation_requirements",
                    "model_reported_participation_gaps",
                    "missing_evidence",
                    "human_review_required",
                    "human_review_reason",
                    "human_approval_required",
                    "human_approval_status",
                    "automatic_action_allowed",
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
                        d.activity_decision,
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
                        json.dumps(
                            [
                                {
                                    "criterion_id": assessment.criterion_id,
                                    "model_status": assessment.model_status,
                                    "source_status": assessment.source_status,
                                    "matched_chunk_ids": assessment.matched_chunk_ids,
                                    "matched_phrases": assessment.matched_phrases,
                                }
                                for assessment in d.validation.criterion_assessments
                            ],
                            ensure_ascii=False,
                        ),
                        d.katilim_yeterliligi_durumu,
                        d.participation_review_required,
                        d.partial_offer,
                        json.dumps(
                            [
                                {
                                    "kisim_no": part.part_number,
                                    "kisim_adi": part.part_name,
                                    "evidence_chunk_ids": part.evidence_chunk_ids,
                                    "gerekce": part.reason,
                                }
                                for part in d.suitable_parts
                            ],
                            ensure_ascii=False,
                        ),
                        "|".join(d.dogrulanamayan_katilim_sartlari),
                        "|".join(d.optional_missing_evidence),
                        "|".join(missing),
                        d.human_review_required,
                        d.human_review_reason,
                        d.human_approval_required,
                        d.human_approval_status,
                        d.automatic_action_allowed,
                        d.evaluated_at,
                    ]
                )

    def _write_public_csv(
        self, path: Path, decisions: list[FinalTenderDecision]
    ) -> None:
        """Kullanıcıya gösterilecek sade public CSV raporu."""
        if not decisions:
            return

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ikn",
                "decision",
                "confidence",
                "decision_summary",
                "human_review_required",
                "human_review_reason"
            ])
            for d in decisions:
                pub = d.to_public_dict()
                writer.writerow([
                    pub.get("ikn", ""),
                    pub.get("decision", ""),
                    round(pub.get("confidence", 0.0), 4) if isinstance(pub.get("confidence"), (float, int)) else pub.get("confidence", ""),
                    pub.get("decision_summary", ""),
                    pub.get("human_review_required", False),
                    pub.get("human_review_reason", "")
                ])

    def _write_professional_csv(
        self, path: Path, decisions: list[FinalTenderDecision]
    ) -> None:
        """Yöneticilere sunulabilecek sade profesyonel CSV raporu.

        Teknik iç alanları (chunk_id, validation detayları, model raw_response)
        içermez. Yalnızca kurumsal karar bilgisi ve profesyonel gerekçe aktarılır.
        """
        if not decisions:
            return

        from app.decision.public_response import build_public_decision_response

        _PROFESSIONAL_COLUMNS = [
            "ikn",
            "tender_name",
            "authority_name",
            "primary_profile_code",
            "karar",
            "guven",
            "yonetici_ozeti",
            "teknik_gerekce",
            "katilim_degerlendirmesi",
            "sonuc",
            "inceleme_notu",
            "human_review_required",
            "human_approval_required",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_PROFESSIONAL_COLUMNS)
            for d in decisions:
                pub = build_public_decision_response(d)
                pr = pub.professional_reasoning
                writer.writerow(
                    [
                        d.ikn,
                        d.tender_name,
                        d.authority_name,
                        d.primary_profile_code,
                        pr.karar_basligi,
                        round(d.final_confidence, 4),
                        pr.yonetici_ozeti,
                        pr.teknik_gerekce,
                        pr.katilim_degerlendirmesi,
                        pr.sonuc,
                        pr.inceleme_notu,
                        d.human_review_required,
                        d.human_approval_required,
                    ]
                )
