from app.retrieval.semantic_retriever import RetrievalConfig, SemanticRetriever


class FakeEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0, 0.0]]


class FakeStore:
    def search(self, **kwargs):
        return [
            {"id": "1", "score": 0.80, "payload": {"ikn": "2026/1"}},
            {"id": "2", "score": 0.75, "payload": {"ikn": "2026/1"}},
            {"id": "3", "score": 0.72, "payload": {"ikn": "2026/1"}},
            {"id": "4", "score": 0.70, "payload": {"ikn": "2026/2"}},
            {"id": "5", "score": 0.54, "payload": {"ikn": "2026/3"}},
        ]


def test_threshold_and_diversity() -> None:
    retriever = SemanticRetriever(
        embedder=FakeEmbedder(),
        vector_store=FakeStore(),
        config=RetrievalConfig(
            final_limit=10,
            candidate_limit=40,
            minimum_semantic_score=0.55,
            max_chunks_per_tender=2,
        ),
    )
    results = retriever.retrieve("örnek")
    assert [x["id"] for x in results] == ["1", "2", "4"]


def test_empty_when_all_scores_low() -> None:
    class LowStore:
        def search(self, **kwargs):
            return [{"id": "1", "score": 0.40, "payload": {"ikn": "2026/1"}}]

    retriever = SemanticRetriever(
        embedder=FakeEmbedder(),
        vector_store=LowStore(),
    )
    assert retriever.retrieve("örnek") == []
