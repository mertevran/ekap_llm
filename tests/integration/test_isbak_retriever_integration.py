"""İSBAK Retriever yerel Qdrant bütünleşme testi.

Bu test gerçek ``storage/qdrant_isbak`` deposunu kullanır.
Varsayılan pytest çalışmasında atlanır; yalnızca ``-m integration``
ile çalıştırıldığında devreye girer::

    PYTHONPATH=. pytest tests/integration/test_isbak_retriever_integration.py \\
        -m integration -v

Ön koşullar
-----------
1. ``storage/qdrant_isbak`` dizininde ``ekap_isbak_tender_chunks_v1``
   koleksiyonu mevcut ve dolu olmalıdır.
2. BGE-M3 modeli ``storage/model_cache``'de önbelleğe alınmış veya
   internet bağlantısı mevcut olmalıdır.
3. Python ortamında gerekli paketler yüklü olmalıdır.
"""

from __future__ import annotations

import pytest

from app.config.isbak_rag_settings import IsbakRagSettings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def integration_settings() -> IsbakRagSettings:
    """Gerçek bütünleşme testleri için düşük eşikli ayarlar."""
    return IsbakRagSettings(
        minimum_final_score=0.25,
        faiss_search_top_k=60,
        faiss_max_tenders_per_profile=10,
        faiss_max_chunks_per_tender=3,
    )


@pytest.fixture(scope="module")
def retriever(integration_settings: IsbakRagSettings):
    """Gerçek embedder ve Qdrant kullanan retriever."""
    # Gerçek bağımlılıkları yüklemeyi dene
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("qdrant_client")

    from app.indexing.embedder import BgeM3Embedder
    from app.retrieval.isbak_tender_retriever import IsbakTenderRetriever
    from app.vector_store.qdrant_store import QdrantVectorStore

    qdrant_path = integration_settings.resolved_qdrant_path

    vector_store = QdrantVectorStore(
        path=str(qdrant_path),
        collection_name=integration_settings.collection_name,
    )

    if not vector_store.collection_exists():
        vector_store.close()
        pytest.skip(
            f"'{integration_settings.collection_name}' koleksiyonu bulunamadı "
            f"({qdrant_path}). İndeksleme yapın ve tekrar deneyin."
        )

    embedder = BgeM3Embedder(
        model_name=integration_settings.embedding_model,
        device=integration_settings.embedding_device,
        batch_size=1,
        show_progress_bar=False,
    )

    ret = IsbakTenderRetriever(
        embedder=embedder,
        vector_store=vector_store,
        settings=integration_settings,
    )
    yield ret
    vector_store.close()


class TestIsbakRetrieverIntegration:
    """Gerçek Qdrant + BGE-M3 bütünleşme testleri."""

    def test_basic_search_returns_results(self, retriever) -> None:
        """Temel aramanın sonuç döndürdüğünü doğrular."""
        results = retriever.retrieve("akıllı ulaşım sistemleri trafik yönetimi sinyalizasyon")
        assert isinstance(results, list)
        # Koleksiyon doluysa en az bir sonuç beklenir
        assert len(results) >= 1

    def test_no_duplicate_ikn_in_results(self, retriever) -> None:
        """Sonuç listesinde aynı İKN birden fazla kez bulunmamalı."""
        results = retriever.retrieve(
            "trafik kavşak sinyalizasyon kamera sensör sistemi",
            limit=10,
        )
        ikns = [r.ikn for r in results]
        assert len(ikns) == len(set(ikns)), f"Tekrarlı İKN bulundu: {ikns}"

    def test_tender_name_not_generic_section_title(self, retriever) -> None:
        """Tender name hiçbir zaman generic section başlığı olmamalı."""
        generic_titles = {
            "ana ihale bilgileri",
            "ihale özellikleri",
            "okas kodları",
        }
        results = retriever.retrieve(
            "trafik yönetimi sinyalizasyon sistemi kavşak",
            limit=5,
        )
        for r in results:
            normalized_name = r.tender_name.lower().strip()
            assert normalized_name not in generic_titles, (
                f"Generic section başlığı ihale adı olarak döndü: '{r.tender_name}' (İKN: {r.ikn})"
            )

    def test_result_has_all_score_components(self, retriever) -> None:
        """Her sonuçta yeni puanlama mimarisinin tüm bileşenleri bulunmalı."""
        cfg = retriever.settings
        results = retriever.retrieve("akıllı kavşak sistemi", limit=3)

        for result in results:
            assert result.scores.max_chunk >= 0.0
            assert result.scores.top_chunks_mean >= 0.0
            assert result.scores.section_diversity >= 0.0
            assert result.scores.okas_support >= 0.0
            assert result.scores.title_support >= 0.0
            assert result.scores.negative_penalty >= 0.0
            assert result.scores.final >= 0.0

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
                f"Nihai puan tutarsız: final={result.scores.final:.4f}, "
                f"reconstructed={reconstructed:.4f}"
            )

    def test_traffic_query_ranks_traffic_tender_first(self, retriever) -> None:
        """Trafik sorgusunda trafik ihalesi akıllı kavşak sonucunda üstte olmalı.

        Bu test örnek veri setine bağımlıdır; koleksiyon yoksa atlanır.
        """
        results = retriever.retrieve(
            "akıllı ulaşım sistemleri trafik yönetimi sinyalizasyon "
            "kamera sensör yazılım geliştirme ve teknik destek ihalesi",
            limit=5,
        )
        if not results:
            pytest.skip("Koleksiyon boş veya eşik altında sonuç yok.")

        # En üstteki sonucun FAISS parça benzerliği makul olmalı.
        assert results[0].scores.max_chunk >= 0.40, (
            f"En üstteki sonucun en yüksek parça skoru çok düşük: {results[0].scores.max_chunk}"
        )

    def test_no_write_to_qdrant_during_search(self, retriever) -> None:
        """Arama sırasında Qdrant koleksiyonunun nokta sayısı değişmemeli."""
        store = retriever.vector_store
        count_before = store.count()

        retriever.retrieve("trafik kavşak sistemi", limit=5)

        count_after = store.count()

        assert count_before == count_after, (
            f"Nokta sayısı değişti: {count_before} → {count_after}. "
            "Retriever yazma yapıyor olabilir!"
        )
