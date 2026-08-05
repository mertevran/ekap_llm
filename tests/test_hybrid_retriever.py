from app.retrieval.semantic_retriever import RetrievalConfig, SemanticRetriever


class FakeEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0, 0.0]]


class FakeStore:
    def search(self, **kwargs):
        return [
            {
                "id": "unrelated",
                "score": 0.64,
                "payload": {
                    "ikn": "2026/2",
                    "title": "Temizlik malzemesi alımı",
                    "text": "Çeşitli temizlik ürünleri.",
                },
            },
            {
                "id": "related",
                "score": 0.61,
                "payload": {
                    "ikn": "2026/1",
                    "title": "Bilgisayar ve ekipman alımı",
                    "text": "Bilgisayar, sunucu ve çevre birimleri.",
                },
            },
        ]


def test_related_result_is_promoted() -> None:
    retriever = SemanticRetriever(
        embedder=FakeEmbedder(),
        vector_store=FakeStore(),
        config=RetrievalConfig(
            minimum_final_score=0.40,
        ),
    )
    results = retriever.retrieve("bilgisayar ve bilgisayar malzemesi alımı")
    assert results[0]["id"] == "related"


def test_irrelevant_result_is_removed() -> None:
    retriever = SemanticRetriever(
        embedder=FakeEmbedder(),
        vector_store=FakeStore(),
    )
    results = retriever.retrieve("bilgisayar ve bilgisayar malzemesi alımı")
    assert [item["id"] for item in results] == ["related"]


def test_same_tender_limit_is_preserved() -> None:
    class DuplicateStore:
        def search(self, **kwargs):
            return [
                {
                    "id": str(index),
                    "score": 0.80 - index * 0.01,
                    "payload": {
                        "ikn": "2026/1",
                        "title": "Bilgisayar alımı",
                        "text": "Bilgisayar ekipmanı.",
                    },
                }
                for index in range(5)
            ]

    retriever = SemanticRetriever(
        embedder=FakeEmbedder(),
        vector_store=DuplicateStore(),
        config=RetrievalConfig(
            minimum_final_score=0.40,
            max_chunks_per_tender=2,
        ),
    )
    assert len(retriever.retrieve("bilgisayar alımı")) == 2
