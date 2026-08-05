"""İSBAK Retrieval Evaluation bütünleşme testleri.

Gerçek ``storage/qdrant_isbak`` deposu gerektirir.
Varsayılan pytest çalışmasında atlanır::

    PYTHONPATH=. pytest tests/integration/test_isbak_retrieval_evaluation.py \\
        -v -m integration
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config.isbak_rag_settings import IsbakRagSettings
from app.evaluation.retrieval_metrics import (
    duplicate_ikn_count,
    profile_precision_at_k,
)

pytestmark = pytest.mark.integration

# Proje kökünden göreceli
_QUERY_FILE = Path(__file__).parents[2] / "evaluation" / "isbak_retrieval_queries.json"
_K_VALUES = (5, 10)


@pytest.fixture(scope="module")
def settings() -> IsbakRagSettings:
    return IsbakRagSettings(
        minimum_final_score=0.25,
        faiss_search_top_k=60,
        faiss_max_tenders_per_profile=10,
        faiss_max_chunks_per_tender=3,
    )


@pytest.fixture(scope="module")
def retriever(settings: IsbakRagSettings):
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("qdrant_client")

    from app.indexing.embedder import BgeM3Embedder
    from app.retrieval.isbak_tender_retriever import IsbakTenderRetriever
    from app.vector_store.qdrant_store import QdrantVectorStore

    qdrant_path = settings.resolved_qdrant_path
    store = QdrantVectorStore(
        path=str(qdrant_path),
        collection_name=settings.collection_name,
    )

    if not store.collection_exists():
        store.close()
        pytest.skip(f"'{settings.collection_name}' koleksiyonu bulunamadı ({qdrant_path}).")

    embedder = BgeM3Embedder(
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        batch_size=1,
        show_progress_bar=False,
    )

    ret = IsbakTenderRetriever(
        embedder=embedder,
        vector_store=store,
        settings=settings,
    )
    yield ret
    store.close()


@pytest.fixture(scope="module")
def query_file_loaded() -> list[dict]:
    if not _QUERY_FILE.exists():
        pytest.skip(f"Sorgu dosyası bulunamadı: {_QUERY_FILE}")
    data = json.loads(_QUERY_FILE.read_text(encoding="utf-8"))
    return data.get("queries", [])


# ---------------------------------------------------------------------------
# Altyapı testleri
# ---------------------------------------------------------------------------


class TestQdrantInfrastructure:
    """storage/qdrant_isbak bağlantı ve koleksiyon kontrolleri."""

    def test_qdrant_path_exists(self, settings: IsbakRagSettings) -> None:
        qdrant_path = settings.resolved_qdrant_path
        assert qdrant_path.exists(), f"Qdrant dizini bulunamadı: {qdrant_path}"

    def test_collection_exists(self, retriever) -> None:
        assert retriever.vector_store.collection_exists(), (
            f"'{retriever.settings.collection_name}' koleksiyonu mevcut değil."
        )

    def test_collection_not_empty(self, retriever) -> None:
        try:
            count = retriever.vector_store.count()
            assert count > 0, "Koleksiyon boş."
        except AttributeError:
            # count() metodu yoksa skip
            pytest.skip("vector_store.count() metodu desteklenmiyor.")

    def test_query_file_is_valid_json(self, query_file_loaded: list[dict]) -> None:
        assert isinstance(query_file_loaded, list)
        assert len(query_file_loaded) > 0

    def test_query_file_has_required_fields(self, query_file_loaded: list[dict]) -> None:
        required = {"query_id", "query", "expected_profile_groups"}
        for q in query_file_loaded:
            missing = required - set(q.keys())
            assert not missing, f"{q.get('query_id')}: eksik alanlar {missing}"


# ---------------------------------------------------------------------------
# Arama kalite testleri
# ---------------------------------------------------------------------------


class TestRetrievalQuality:
    """Gerçek arama sonuçları üzerinde kalite kontrolleri."""

    def test_basic_query_returns_results(self, retriever) -> None:
        results = retriever.retrieve(
            "akıllı kavşak sinyalizasyon sistemi trafik yönetimi",
            limit=5,
        )
        assert isinstance(results, list)
        assert len(results) >= 1, "Hiç sonuç dönmedi."

    def test_no_duplicate_ikn_in_results(self, retriever) -> None:
        results = retriever.retrieve(
            "trafik sinyalizasyon kamera sensör sistemi kurulumu",
            limit=10,
        )
        ikns = [r.ikn for r in results]
        dup_count = duplicate_ikn_count(ikns)
        assert dup_count == 0, f"Tekrarlı İKN bulundu: {dup_count} adet | {ikns}"

    def test_tender_name_not_empty_or_generic(self, retriever) -> None:
        generic = {"ana ihale bilgileri", "ihale özellikleri", "okas kodları"}
        results = retriever.retrieve("akıllı ulaşım sistemleri trafik yönetimi", limit=5)
        for r in results:
            assert r.tender_name, f"Tender name boş (İKN: {r.ikn})"
            assert r.tender_name.lower().strip() not in generic, (
                f"Generic başlık ihale adı olarak döndü: '{r.tender_name}'"
            )

    def test_scores_are_consistent(self, retriever) -> None:
        """Nihai puan yeni beş bileşenli formülle örtüşmeli."""
        cfg = retriever.settings
        results = retriever.retrieve("bakım onarım teknik destek hizmet alımı", limit=5)

        for result in results:
            reconstructed = (
                result.scores.max_chunk * cfg.weight_max_chunk
                + result.scores.top_chunks_mean * cfg.weight_top_chunks
                + result.scores.section_diversity * cfg.weight_section_diversity
                + result.scores.okas_support * cfg.weight_okas
                + result.scores.title_support * cfg.weight_title
                - result.scores.negative_penalty
            )
            reconstructed = max(0.0, reconstructed)

            assert abs(result.scores.final - reconstructed) < 0.01, (
                f"Skor tutarsız: final={result.scores.final:.4f}, "
                f"reconstructed={reconstructed:.4f} (İKN: {result.ikn})"
            )

    def test_results_sorted_by_final_score(self, retriever) -> None:
        results = retriever.retrieve("akıllı kavşak sinyalizasyon", limit=8)
        if len(results) < 2:
            pytest.skip("Sıralama kontrolü için en az 2 sonuç gerekli.")
        for i in range(len(results) - 1):
            assert results[i].scores.final >= results[i + 1].scores.final, (
                "Sonuçlar nihai puana göre azalan sıralı değil."
            )

    def test_no_write_to_qdrant_during_search(self, retriever) -> None:
        """Arama öncesi ve sonrası Qdrant kayıt sayısı değişmemeli.

        Yerel Qdrant, aynı dizine yalnızca tek bağlantıya izin verir
        (portalocker dosya kilidi). Bu nedenle ayrı bir bağlantı açmak
        yerine retriever'ın mevcut bağlantısı kullanılır.
        """
        store = retriever.vector_store

        # Mevcut bağlantıdan sayı al
        try:
            count_before = store.count()
        except AttributeError:
            # count() yoksa client üzerinden dene
            try:
                info = store.client.get_collection(retriever.settings.collection_name)
                count_before = info.points_count
            except Exception:
                pytest.skip(
                    "Qdrant kayıt sayısı alınamadı; count() ve get_collection() desteklenmiyor."
                )

        retriever.retrieve("trafik sistemi", limit=5)

        # Aynı bağlantıdan tekrar say
        try:
            count_after = store.count()
        except AttributeError:
            try:
                info = store.client.get_collection(retriever.settings.collection_name)
                count_after = info.points_count
            except Exception:
                pytest.skip("Arama sonrası sayım alınamadı.")

        assert count_before == count_after, (
            f"Kayıt sayısı değişti: {count_before} → {count_after}. "
            "Retriever yazma yapıyor olabilir!"
        )


# ---------------------------------------------------------------------------
# Profil kalite testleri
# ---------------------------------------------------------------------------


class TestProfileAccuracy:
    """Sorgu dosyasındaki sorgular üzerinde profil doğruluğu kontrolü."""

    def test_aus_queries_return_aus_profiles(
        self, retriever, query_file_loaded: list[dict]
    ) -> None:
        """AUS sorguları için en az bir AUS profilinin ilk 5'te olması beklenir."""
        aus_queries = [q for q in query_file_loaded if q.get("profile_group") == "AUS"]
        if not aus_queries:
            pytest.skip("AUS sorgusu bulunamadı.")

        # Yalnızca ilk AUS sorgusunu çalıştır (zaman kısıtı)
        q = aus_queries[0]
        results = retriever.retrieve(q["query"], limit=5)
        if not results:
            pytest.skip("Hiç sonuç dönmedi.")

        profile_codes_list = [r.profile_codes for r in results[:5]]
        score = profile_precision_at_k(
            profile_codes_list,
            q["expected_profile_groups"],
            k=min(5, len(results)),
        )
        # Profil doğruluğu %20'nin üzerinde olmalı (en az 1/5 eşleşme)
        assert score >= 0.20, f"AUS profil doğruluğu çok düşük: {score:.2f} (sorgu: '{q['query']}')"

    def test_all_queries_produce_some_results(
        self, retriever, query_file_loaded: list[dict]
    ) -> None:
        """Her örnek sorgu mevcut yapılandırmayla en az bir sonuç üretmeli."""
        failed_queries: list[str] = []

        # Tüm sorguları çalıştırmak çok uzun sürer; ilk 5'i test et
        sample = query_file_loaded[:5]

        for query_case in sample:
            results = retriever.retrieve(
                query_case["query"],
                limit=5,
            )
            if not results:
                failed_queries.append(query_case["query_id"])

        assert not failed_queries, f"Şu sorgular hiç sonuç döndürmedi: {failed_queries}"
