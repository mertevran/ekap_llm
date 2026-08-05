"""
Grid search tabanlı optimizasyon sınıfı.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from app.config.isbak_rag_settings import IsbakRagSettings
from app.evaluation.retrieval_metrics import aggregate_metrics, evaluate_query
from app.retrieval.isbak_tender_retriever import (
    EmbedderProtocol,
    IsbakTenderRetriever,
    VectorStoreProtocol,
)


class CachedEmbedder:
    def __init__(self, real_embedder: EmbedderProtocol):
        self.real = real_embedder
        self.vector_size = real_embedder.vector_size
        self.model_name = real_embedder.model_name
        self._cache = {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        results = []
        for text in texts:
            if text not in self._cache:
                self._cache[text] = self.real.embed([text])[0]
            results.append(self._cache[text])
        return results


class CachedVectorStore:
    def __init__(self, real_store: VectorStoreProtocol):
        self.real = real_store
        self.collection_name = real_store.collection_name
        self._cache = {}

    def search(
        self, *, query_vector: list[float], limit: int, score_threshold: float | None = None
    ) -> list[Any]:
        # We use a naive key: rounded first value of vector to 4 decimals + sum
        # But actually, during tuning, we only have a few queries.
        # We can just key by the vector tuple.
        key = tuple(round(v, 6) for v in query_vector)

        if key not in self._cache:
            # Always fetch max possible limit for tuning cache
            self._cache[key] = self.real.search(
                query_vector=query_vector, limit=200, score_threshold=None
            )

        results = self._cache[key]
        if score_threshold is None:
            return results[:limit]
        return [r for r in results if float(r.get("score", 0.0)) >= score_threshold][:limit]

    def collection_exists(self) -> bool:
        return True


class RetrievalGridSearchTuner:
    """Parametre kombinasyonlarını deneyerek en iyi performansı bulur."""

    def __init__(
        self,
        embedder: EmbedderProtocol,
        vector_store: VectorStoreProtocol,
        queries: list[dict[str, Any]],
        labels: list[Any],
    ):
        self.embedder = CachedEmbedder(embedder)
        self.vector_store = CachedVectorStore(vector_store)
        self.queries = queries
        self.labels = labels

        # Pre-cache
        print("Sorgu vektörleri ve Qdrant sonuçları önbelleğe alınıyor...")
        for q in queries:
            v = self.embedder.embed([q["query"]])[0]
            self.vector_store.search(query_vector=v, limit=100)  # Pre-fetch

    def _evaluate_params(self, settings: IsbakRagSettings, k_values=(5, 10)) -> dict[str, Any]:
        retriever = IsbakTenderRetriever(
            embedder=self.embedder, vector_store=self.vector_store, settings=settings
        )

        all_metrics = []
        for q in self.queries:
            results = retriever.retrieve(
                q["query"], limit=settings.result_limit, candidate_limit=settings.candidate_limit
            )
            retrieved_ikns = [r.ikn for r in results]
            retrieved_codes = [r.profile_codes for r in results]

            # Etiketler (yalnızca >=2 olanlar relevant)
            expected = q.get("expected_profile_groups", [])
            relevant_ikns = set(q.get("relevant_ikns", []))
            graded = q.get("graded_relevance", {})

            metrics = evaluate_query(
                retrieved_ikns=retrieved_ikns,
                retrieved_profile_codes=retrieved_codes,
                relevant_ikns=relevant_ikns,
                graded_relevance=graded,
                expected_profile_groups=expected,
                k_values=k_values,
            )
            all_metrics.append(metrics)

        agg = aggregate_metrics(all_metrics, k_values)
        return {k: (0.0 if math.isnan(v) else v) for k, v in agg.items()}

    def run(self, param_grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(itertools.product(*values))

        results = []
        total = len(combinations)

        for i, comb in enumerate(combinations, 1):
            kwargs = dict(zip(keys, comb))
            try:
                settings = IsbakRagSettings(**kwargs)
            except ValueError:
                # Toplam 1.0 kuralını ihlal eden kombinasyonları atla
                continue
            # Print progress without spamming too many lines
            if i % 10 == 0 or i == total:
                print(f"  Tuning progress: {i}/{total} combinations...")

            metrics = self._evaluate_params(settings)

            # Score heuristik: MRR + Recall@10
            score = metrics.get("mrr", 0.0) + metrics.get("recall@10", 0.0)

            results.append({"params": kwargs, "metrics": metrics, "tuning_score": score})

        # Puanına göre sırala
        results.sort(key=lambda x: x["tuning_score"], reverse=True)
        return results
