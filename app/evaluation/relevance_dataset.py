"""
Etiketleme veri seti yönetimi.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class IsbakRelevanceLabel:
    query_id: str
    query: str
    ikn: str
    tender_name: str
    primary_profile_code: str
    profile_codes: list[str]
    rank: int
    scores: dict[str, float]
    relevance_grade: int | None
    label_status: str
    notes: str
    reviewed_at: str
    dataset_split: str
    idare_adi: str = ""
    rationale: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IsbakRelevanceLabel:
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RelevanceDataset:
    """Etiketleme verilerini yöneten ve split atayan sınıf."""

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)
        self.labels: list[IsbakRelevanceLabel] = []
        if self.file_path.exists():
            self._load()

    def _load(self) -> None:
        with open(self.file_path, encoding="utf-8") as f:
            data = json.load(f)
            self.labels = [IsbakRelevanceLabel.from_dict(item) for item in data]

    def save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            data = [label.to_dict() for label in self.labels]
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_label(self, label: IsbakRelevanceLabel) -> None:
        """Etiket ekler veya günceller."""
        for i, existing in enumerate(self.labels):
            if existing.query_id == label.query_id and existing.ikn == label.ikn:
                self.labels[i] = label
                return
        self.labels.append(label)

    def get_labels_by_query(self, query_id: str) -> list[IsbakRelevanceLabel]:
        return [L for L in self.labels if L.query_id == query_id]

    @staticmethod
    def compute_dataset_splits(queries: list[dict[str, Any]]) -> dict[str, str]:
        """Sorguları development ve holdout kümelerine deterministik böler."""
        groups: dict[str, list[str]] = {}
        for q in queries:
            g = q["profile_group"]
            if g not in groups:
                groups[g] = []
            groups[g].append(q["query_id"])

        splits: dict[str, str] = {}
        rng = random.Random(42)

        for _, q_ids in groups.items():
            q_ids.sort()
            n = len(q_ids)

            # Yüzde 30 holdout, ama en az 2 sorgu (yeterli sorgu varsa)
            holdout_count = int(round(n * 0.3))
            if n >= 4:
                holdout_count = max(2, holdout_count)
            elif n == 3:
                holdout_count = 2  # En az 2 kuralını sağlamak için
            elif n == 2:
                holdout_count = 1
            else:
                holdout_count = 0

            holdout_set = set(rng.sample(q_ids, holdout_count))
            for qid in q_ids:
                splits[qid] = "holdout" if qid in holdout_set else "development"

        return splits
