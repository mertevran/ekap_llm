import pytest

pytest.importorskip("sentence_transformers")
import numpy as np

from app.rag.embeddings import EmbeddingService


class FakeSentenceTransformer:
    def get_sentence_embedding_dimension(self) -> int:
        return 4

    def encode(
        self,
        texts,
        batch_size,
        show_progress_bar,
        convert_to_numpy,
        normalize_embeddings,
    ):
        return np.array(
            [[1.0, 0.0, 0.0, 0.0] for _ in texts],
            dtype=np.float32,
        )


def build_service() -> EmbeddingService:
    service = EmbeddingService(
        model_name="fake-model",
        device="cpu",
        batch_size=2,
        normalize_embeddings=True,
    )

    service._model = FakeSentenceTransformer()
    service._vector_size = 4

    return service


def test_encode_texts_returns_expected_vector_count() -> None:
    service = build_service()

    vectors = service.encode_texts(["Birinci metin", "İkinci metin"])

    assert len(vectors) == 2
    assert all(len(vector) == 4 for vector in vectors)


def test_encode_texts_ignores_empty_values() -> None:
    service = build_service()

    vectors = service.encode_texts(["Geçerli metin", "", "   "])

    assert len(vectors) == 1


def test_encode_query_returns_single_vector() -> None:
    service = build_service()

    vector = service.encode_query("İhale yeterlilik kriterleri")

    assert len(vector) == 4


def test_encode_empty_query_raises_error() -> None:
    service = build_service()

    try:
        service.encode_query("   ")
    except ValueError as exc:
        assert "boş olamaz" in str(exc)
    else:
        raise AssertionError("ValueError bekleniyordu.")
