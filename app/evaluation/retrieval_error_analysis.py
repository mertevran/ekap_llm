"""
Bilgi getirme hatalarını analiz eden ve kök nedenlerini çıkaran modül.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from app.evaluation.relevance_dataset import RelevanceDataset


@dataclass
class ErrorInstance:
    query_id: str
    query: str
    ikn: str
    tender_name: str
    error_type: str
    relevance_grade: int
    rank: int
    scores: dict[str, float]
    details: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetrievalErrorAnalyzer:
    def __init__(self, evaluation_report_path: str, labels_path: str) -> None:
        self.dataset = RelevanceDataset(labels_path)
        with open(evaluation_report_path, encoding="utf-8") as f:
            self.report = json.load(f)

    def analyze(self) -> dict[str, Any]:
        profiles = self.report.get("profiles", [])
        if not profiles:
            return {}

        # İlk (veya en iyi) profil üzerinden analiz yap (varsayılan)
        target_profile = profiles[0]
        for p in profiles:
            if p.get("weight_profile") == self.report.get("evaluation_metadata", {}).get(
                "best_weight_profile"
            ):
                target_profile = p
                break

        query_results = target_profile.get("query_results", [])

        errors: list[ErrorInstance] = []

        for qr in query_results:
            if qr.get("status") != "success":
                continue

            qid = qr["query_id"]
            qtext = qr["query"]
            labels = self.dataset.get_labels_by_query(qid)
            label_map = {
                L.ikn: L
                for L in labels
                if L.label_status == "reviewed" and L.relevance_grade is not None
            }

            top_results = qr.get("top_results", [])
            retrieved_ikns = set()

            for res in top_results:
                ikn = res["ikn"]
                retrieved_ikns.add(ikn)
                rank = res["rank"]

                L = label_map.get(ikn)
                if not L:
                    continue

                grade = L.relevance_grade
                sem = L.scores.get("semantic", 0.0)
                lex = L.scores.get("lexical", 0.0)
                pro = L.scores.get("profile", 0.0)

                # False Positive
                if grade <= 1 and rank <= 5:
                    errors.append(
                        ErrorInstance(
                            qid,
                            qtext,
                            ikn,
                            L.tender_name,
                            "false_positive",
                            grade,
                            rank,
                            L.scores,
                            "Top 5 içinde ilgisiz sonuç.",
                        )
                    )

                # High Semantic Low Relevance
                if grade <= 1 and sem > 0.45:
                    errors.append(
                        ErrorInstance(
                            qid,
                            qtext,
                            ikn,
                            L.tender_name,
                            "high_semantic_low_relevance",
                            grade,
                            rank,
                            L.scores,
                            "Semantik puanı çok yüksek ama ilgisiz.",
                        )
                    )

                # High Profile Low Relevance
                if grade <= 1 and pro > 0.5:
                    errors.append(
                        ErrorInstance(
                            qid,
                            qtext,
                            ikn,
                            L.tender_name,
                            "high_profile_low_relevance",
                            grade,
                            rank,
                            L.scores,
                            "Profil puanı yüksek ama ilgisiz.",
                        )
                    )

                # Lexical Overmatch
                if grade <= 1 and lex > 0.5:
                    errors.append(
                        ErrorInstance(
                            qid,
                            qtext,
                            ikn,
                            L.tender_name,
                            "lexical_overmatch",
                            grade,
                            rank,
                            L.scores,
                            "Kelime eşleşmesi yüksek ama ilgisiz (muhtemelen genel kelimeler).",
                        )
                    )

                # Profile Mismatch
                expected_group = qr.get("profile_group", "")
                has_expected = False
                for c in L.profile_codes:
                    if c.startswith(expected_group):
                        has_expected = True
                        break
                if not has_expected and rank <= 10:
                    errors.append(
                        ErrorInstance(
                            qid,
                            qtext,
                            ikn,
                            L.tender_name,
                            "profile_mismatch",
                            grade,
                            rank,
                            L.scores,
                            f"Beklenen {expected_group} profili bulunamadı.",
                        )
                    )

            # False Negative (Etiketlerde ilgili (>=2) ama sonuçlarda top 10'da yok)
            for ikn, L in label_map.items():
                if L.relevance_grade >= 2 and ikn not in retrieved_ikns:
                    errors.append(
                        ErrorInstance(
                            qid,
                            qtext,
                            ikn,
                            L.tender_name,
                            "false_negative",
                            L.relevance_grade,
                            999,
                            L.scores,
                            "İlgili kayıt Top 10 listesine giremedi.",
                        )
                    )

        # İstatistikleri topla
        error_counts = {}
        for err in errors:
            error_counts[err.error_type] = error_counts.get(err.error_type, 0) + 1

        return {
            "summary": {"total_errors_found": len(errors), "error_counts": error_counts},
            "errors": [err.to_dict() for err in errors],
        }
