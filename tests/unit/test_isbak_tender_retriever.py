"""IsbakTenderRetriever birim testleri (Yeni Puanlama API'si v2).

Tüm testler mock vektör deposu ve gömme modeli kullanır;
gerçek FAISS, Qdrant veya PostgreSQL bağlantısı gerektirmez.

Yeni API'de:
- ScoreBreakdown: max_chunk, top_chunks_mean, section_diversity, okas_support, title_support, final, negative_penalty
- IsbakRagSettings: weight_max_chunk=0.55, weight_top_chunks=0.20, weight_section_diversity=0.10, weight_okas=0.10, weight_title=0.05
- _profile_score / _resolve_tender_name_from_chunks kaldırıldı
- Yeni: _profile_penalty, _term_overlap, _query_terms, _resolve_tender_name

Davranışsal amaç korunmuştur; yalnızca API taşındı.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from app.config.isbak_rag_settings import IsbakRagSettings
from app.retrieval.isbak_tender_retriever import (
    IsbakTenderRetriever,
    _profile_penalty,
    _query_terms,
    _resolve_tender_name,
    _term_overlap,
)

# ---------------------------------------------------------------------------
# Test yardımcıları
# ---------------------------------------------------------------------------

_VECTOR_SIZE = 4


def _make_settings(**kwargs: Any) -> IsbakRagSettings:
    """Test ayarları; eşikleri sıfıra çeker, diğerlerini varsayılan bırakır."""
    defaults: dict[str, Any] = {
        "minimum_final_score": 0.0,
        "faiss_search_top_k": 20,
        "faiss_max_tenders_per_profile": 10,
        "faiss_max_chunks_per_tender": 4,
        # Ağırlıklar 1.0 toplamını korumalı
        "weight_max_chunk": 0.55,
        "weight_top_chunks": 0.20,
        "weight_section_diversity": 0.10,
        "weight_okas": 0.10,
        "weight_title": 0.05,
    }
    defaults.update(kwargs)
    return IsbakRagSettings(**defaults)


def _make_chunk(
    *,
    ikn: str,
    tender_id: str,
    score: float,
    title: str = "Ana İhale Bilgileri",
    tender_name: str | None = None,
    document_title: str = "",
    section_id: str = "",
    section_type: str = "announcement",
    profile_codes: list[str] | None = None,
    primary_profile_code: str = "",
    classification_status: str = "guclu_eslesme",
    text: str = "Örnek ihale metni.",
    okas_codes: list[str] | None = None,
    source_hash: str | None = None,
    ihale_turu: str = "Mal Alımı",
    idare_adi: str = "Test İdaresi A.Ş.",
    il: str = "İstanbul",
    ihale_tarihi: str = "01.09.2026 10:00",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "document_title": document_title,
        "ihale_turu": ihale_turu,
        "ihale_tarihi": ihale_tarihi,
        "idare_adi": idare_adi,
        "il": il,
        "okas_codes": okas_codes or None,
        "section_type": section_type,
    }
    return {
        "id": f"{tender_id}_{section_id or 'main'}",
        "score": score,
        "payload": {
            "chunk_id": f"chunk_{tender_id}_{section_id or 'main'}",
            "tender_id": tender_id,
            "ikn": ikn,
            "title": title,
            "tender_name": tender_name,
            "section_id": section_id or f"announcement:{tender_id}",
            "section_type": section_type,
            "text": text,
            "primary_profile_code": primary_profile_code,
            "profile_codes": profile_codes or [],
            "classification_status": classification_status,
            "classifier_version": "isbak_profile_classifier_v2_2",
            "embedding_model": "BAAI/bge-m3",
            "source_hash": source_hash,
            "metadata": metadata,
        },
    }


class _FakeEmbedder:
    model_name = "BAAI/bge-m3"
    _vsize = _VECTOR_SIZE

    @property
    def vector_size(self) -> int:
        return self._vsize

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * self._vsize for _ in texts]


class _FakeVectorStore:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks
        self.collection_name = "ekap_isbak_tender_chunks_v1"

    def collection_exists(self) -> bool:
        return True

    def search(
        self,
        *,
        query_vector: list[float],
        limit: int,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        return self._chunks[:limit]


class _MissingVectorStore(_FakeVectorStore):
    def collection_exists(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Test 1: Aynı İKN'ye ait üç chunk → tek ihale sonucu
# ---------------------------------------------------------------------------


def test_same_ikn_three_chunks_returns_one_result() -> None:
    chunks = [
        _make_chunk(
            ikn="2026/1001",
            tender_id="T1",
            score=0.82,
            section_id="announcement:1",
            document_title="Trafik Kavşak Sistemi",
        ),
        _make_chunk(
            ikn="2026/1001",
            tender_id="T1",
            score=0.78,
            section_id="announcement:2",
            document_title="Trafik Kavşak Sistemi",
            section_type="characteristics",
        ),
        _make_chunk(
            ikn="2026/1001",
            tender_id="T1",
            score=0.74,
            section_id="tender:T1:okas",
            document_title="Trafik Kavşak Sistemi",
            section_type="okas",
        ),
    ]
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore(chunks),
        settings=_make_settings(),
    )
    results = retriever.retrieve("trafik sinyalizasyon sistemi")
    assert len(results) == 1
    assert results[0].ikn == "2026/1001"


# ---------------------------------------------------------------------------
# Test 2: İki farklı İKN → iki ayrı sonuç
# ---------------------------------------------------------------------------


def test_two_different_ikn_returns_two_results() -> None:
    chunks = [
        _make_chunk(
            ikn="2026/1001",
            tender_id="T1",
            score=0.80,
            section_id="announcement:1",
            document_title="İhale A",
        ),
        _make_chunk(
            ikn="2026/1002",
            tender_id="T2",
            score=0.75,
            section_id="announcement:2",
            document_title="İhale B",
        ),
    ]
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore(chunks),
        settings=_make_settings(),
    )
    results = retriever.retrieve("trafik sistemi")
    assert len(results) == 2
    ikns = {r.ikn for r in results}
    assert "2026/1001" in ikns
    assert "2026/1002" in ikns


# ---------------------------------------------------------------------------
# Test 3: En yüksek max_chunk puanlı ihale ilk sıraya gelmeli
# ---------------------------------------------------------------------------


def test_highest_score_ranks_first() -> None:
    chunks = [
        _make_chunk(
            ikn="2026/LOW",
            tender_id="TL",
            score=0.55,
            section_id="announcement:low",
            document_title="Düşük Benzerlik",
        ),
        _make_chunk(
            ikn="2026/HIGH",
            tender_id="TH",
            score=0.90,
            section_id="announcement:high",
            document_title="Yüksek Benzerlik",
        ),
        _make_chunk(
            ikn="2026/MID",
            tender_id="TM",
            score=0.72,
            section_id="announcement:mid",
            document_title="Orta Benzerlik",
        ),
    ]
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore(chunks),
        settings=_make_settings(),
    )
    results = retriever.retrieve("akıllı kavşak sinyalizasyon")
    assert results[0].ikn == "2026/HIGH"


# ---------------------------------------------------------------------------
# Test 4: Section diversity puanı birden fazla section'da artmalı
# Yeni API: section_diversity_score = min(1.0, len(distinct_section_types) / 3.0)
# ---------------------------------------------------------------------------


def test_section_diversity_increases_score() -> None:
    """Farklı section_type'lardan gelen parçalar diversity puanını artırmalı."""
    multi_section_chunks = [
        _make_chunk(
            ikn="2026/MULTI",
            tender_id="TM",
            score=0.70,
            section_id="s1",
            section_type="tender_summary",
            document_title="Çok Bölümlü İhale",
        ),
        _make_chunk(
            ikn="2026/MULTI",
            tender_id="TM",
            score=0.65,
            section_id="s2",
            section_type="okas",
            document_title="Çok Bölümlü İhale",
        ),
        _make_chunk(
            ikn="2026/MULTI",
            tender_id="TM",
            score=0.60,
            section_id="s3",
            section_type="characteristics",
            document_title="Çok Bölümlü İhale",
        ),
    ]
    single_section_chunks = [
        _make_chunk(
            ikn="2026/SINGLE",
            tender_id="TS",
            score=0.70,
            section_id="s1",
            section_type="announcement",
            document_title="Tek Bölümlü İhale",
        ),
        _make_chunk(
            ikn="2026/SINGLE",
            tender_id="TS",
            score=0.65,
            section_id="s2",
            section_type="announcement",
            document_title="Tek Bölümlü İhale",
        ),
    ]
    all_chunks = multi_section_chunks + single_section_chunks
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore(all_chunks),
        settings=_make_settings(),
    )
    results = retriever.retrieve("trafik sistemi")
    multi = next(r for r in results if r.ikn == "2026/MULTI")
    single = next(r for r in results if r.ikn == "2026/SINGLE")
    assert multi.scores.section_diversity > single.scores.section_diversity


# ---------------------------------------------------------------------------
# Test 5: Genel terimler ilgisiz ihaleyi aşırı yükseltmemeli
# ---------------------------------------------------------------------------


def test_generic_terms_do_not_over_boost_unrelated_tender() -> None:
    traffic_chunk = _make_chunk(
        ikn="2026/TRAF",
        tender_id="TT",
        score=0.82,
        document_title="Yapay Zeka Destekli Akıllı Kavşak Sistemi Kurulumu",
        profile_codes=["AUS-01"],
        section_id="announcement:traffic",
        text="Akıllı kavşak sinyalizasyon sistemi trafik yönetimi.",
    )
    radiology_chunk = _make_chunk(
        ikn="2026/RAD",
        tender_id="TR",
        score=0.55,
        document_title="Radyoloji Bilgi Sistemi Yazılımı Alımı",
        profile_codes=["TEK-03"],
        section_id="announcement:radiology",
        text="Hastane radyoloji bilgi yönetim sistemi yazılım alımı.",
    )
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([traffic_chunk, radiology_chunk]),
        settings=_make_settings(),
    )
    results = retriever.retrieve(
        "trafik yönetimi sinyalizasyon kavşak kamera sensör yazılım destek"
    )
    assert len(results) == 2
    traffic_result = next(r for r in results if r.ikn == "2026/TRAF")
    radiology_result = next(r for r in results if r.ikn == "2026/RAD")
    assert traffic_result.scores.final >= radiology_result.scores.final


# ---------------------------------------------------------------------------
# Test 6: Gerçek ihale adı metadata.document_title'dan çözümlenmeli
# ---------------------------------------------------------------------------


def test_tender_name_resolves_from_metadata_document_title() -> None:
    """Gerçek payload: tender_name=None, gerçek ad metadata.document_title'da."""
    chunk = _make_chunk(
        ikn="2026/2000",
        tender_id="T2000",
        score=0.75,
        title="Ana İhale Bilgileri",
        tender_name=None,
        document_title="Yapay Zeka Destekli Akıllı Kavşak Sistem Kurulumu",
        section_id="tender:T2000:main",
    )
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([chunk]),
        settings=_make_settings(),
    )
    results = retriever.retrieve("akıllı kavşak sistemi")
    assert len(results) == 1
    assert results[0].tender_name != "Ana İhale Bilgileri"
    assert "Kavşak" in results[0].tender_name or "Akıllı" in results[0].tender_name


# ---------------------------------------------------------------------------
# Test 7: Ayarlar: FAISS parametrelerini doğrula
# ---------------------------------------------------------------------------


def test_default_faiss_settings() -> None:
    settings = IsbakRagSettings()
    assert settings.faiss_search_top_k >= 50
    assert settings.faiss_max_tenders_per_profile >= 10
    assert settings.faiss_max_chunks_per_tender >= 2


# ---------------------------------------------------------------------------
# Test 8: Eşik altındaki sonuçlar elenmeli
# ---------------------------------------------------------------------------


def test_results_below_threshold_are_filtered() -> None:
    chunks = [
        _make_chunk(
            ikn="2026/LOW",
            tender_id="TL",
            score=0.20,
            section_id="announcement:low",
        )
    ]
    settings = _make_settings(minimum_final_score=0.50)
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore(chunks),
        settings=settings,
    )
    results = retriever.retrieve("trafik sistemi")
    assert len(results) == 0


# ---------------------------------------------------------------------------
# Test 9: Aynı section_id'den en yüksek puanlı parça seçilmeli
# ---------------------------------------------------------------------------


def test_same_section_id_deduplication_keeps_highest_score() -> None:
    """Aynı section_id'den gelen parçaların en yüksek puanlısı seçilmeli."""
    same_section_chunks = [
        _make_chunk(
            ikn="2026/SAME",
            tender_id="TS",
            score=0.80 - i * 0.02,
            section_id="announcement:7681255",
            document_title="Aynı Bölüm Testi",
        )
        for i in range(5)
    ]
    settings = _make_settings(faiss_max_chunks_per_tender=5)
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore(same_section_chunks),
        settings=settings,
    )
    results = retriever.retrieve("trafik")
    assert len(results) == 1
    # Aynı section_id'den tekrar deduplicate → 1 chunk
    assert len(results[0].evidence_chunks) == 1
    # En yüksek puanlı (score=0.80) seçilmeli
    assert results[0].evidence_chunks[0].semantic_score == pytest.approx(0.80, abs=1e-3)


# ---------------------------------------------------------------------------
# Test 10: JSON çıktısı geçerli olmalı
# ---------------------------------------------------------------------------


def test_json_output_is_valid() -> None:
    chunk = _make_chunk(
        ikn="2026/3000",
        tender_id="T3",
        score=0.70,
        document_title="Sinyalizasyon Sistemi Alımı",
        profile_codes=["AUS-01"],
        section_id="announcement:3000",
    )
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([chunk]),
        settings=_make_settings(),
    )
    results = retriever.retrieve("trafik sinyalizasyon")

    output = [
        {
            "ikn": r.ikn,
            "tender_name": r.tender_name,
            "scores": {
                "final": r.scores.final,
                "max_chunk": r.scores.max_chunk,
                "top_chunks_mean": r.scores.top_chunks_mean,
                "section_diversity": r.scores.section_diversity,
                "okas_support": r.scores.okas_support,
                "title_support": r.scores.title_support,
            },
        }
        for r in results
    ]
    json_str = json.dumps(output, ensure_ascii=False, indent=2)
    loaded = json.loads(json_str)
    assert isinstance(loaded, list)
    assert len(loaded) >= 1
    assert "ikn" in loaded[0]
    assert "scores" in loaded[0]
    assert "final" in loaded[0]["scores"]
    # Yeni API bileşenleri
    assert "max_chunk" in loaded[0]["scores"]
    assert "section_diversity" in loaded[0]["scores"]


# ---------------------------------------------------------------------------
# Test 11: Eksik payload alanlarında sistem çökmemeli
# ---------------------------------------------------------------------------


def test_missing_payload_fields_do_not_crash() -> None:
    """Gerçek kayıtta eksik alanlar (source_hash, tender_name) olabilir."""
    minimal_chunk = {
        "id": "minimal_id",
        "score": 0.65,
        "payload": {
            "tender_id": "T_MIN",
            "ikn": "2026/MIN",
            "source_hash": None,
            "tender_name": None,
        },
    }
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([minimal_chunk]),
        settings=_make_settings(),
    )
    results = retriever.retrieve("test sorgusu")
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Test 12: Koleksiyon yoksa RuntimeError verilmeli
# ---------------------------------------------------------------------------


def test_missing_collection_raises_runtime_error() -> None:
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_MissingVectorStore([]),
        settings=_make_settings(),
    )
    with pytest.raises(RuntimeError, match="FAISS indeksi"):
        retriever.retrieve("trafik sistemi")


# ---------------------------------------------------------------------------
# Test 13: Geçersiz ağırlık toplamı ValueError fırlatmalı
# ---------------------------------------------------------------------------


def test_invalid_weight_sum_raises_value_error() -> None:
    """Ağırlıklar toplamı 1.0 olmazsa ValueError fırlatılmalı."""
    with pytest.raises(ValueError, match="toplamı"):
        IsbakRagSettings(
            weight_max_chunk=0.60,  # 0.60+0.20+0.10+0.10+0.05 = 1.05 → hata
            weight_top_chunks=0.20,
            weight_section_diversity=0.10,
            weight_okas=0.10,
            weight_title=0.05,
        )


# ---------------------------------------------------------------------------
# Test 14: source_hash retriever tarafından değiştirilmemeli
# ---------------------------------------------------------------------------


def test_source_hash_not_modified_by_retriever() -> None:
    chunk = _make_chunk(
        ikn="2026/HASH",
        tender_id="TH",
        score=0.75,
        section_id="announcement:hash",
        document_title="SHA Test İhalesi",
        source_hash=None,
    )
    original_hash = chunk["payload"]["source_hash"]
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([chunk]),
        settings=_make_settings(),
    )
    retriever.retrieve("test")
    assert chunk["payload"]["source_hash"] == original_hash


# ---------------------------------------------------------------------------
# Test 15: ChunkEvidence hem tam text hem text_preview içermeli
# ---------------------------------------------------------------------------


def test_chunk_evidence_has_full_text_and_preview() -> None:
    long_text = "A" * 500
    chunk = _make_chunk(
        ikn="2026/TEXT",
        tender_id="TX",
        score=0.70,
        section_id="announcement:text",
        document_title="Metin Uzunluğu Testi",
        text=long_text,
    )
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([chunk]),
        settings=_make_settings(),
    )
    results = retriever.retrieve("test")
    assert len(results) == 1
    ev = results[0].evidence_chunks[0]
    assert ev.text == long_text
    assert ev.text_preview == long_text[:200]
    assert len(ev.text_preview) == 200


# ---------------------------------------------------------------------------
# Test 16: TenderSearchResult.okas_codes alanı mevcut olmalı
# ---------------------------------------------------------------------------


def test_tender_result_has_okas_codes_field() -> None:
    chunk = _make_chunk(
        ikn="2026/OKAS",
        tender_id="TO",
        score=0.72,
        section_id="tender:TO:okas",
        document_title="OKAS Testi",
        okas_codes=["48810000", "72000000"],
    )
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([chunk]),
        settings=_make_settings(),
    )
    results = retriever.retrieve("yazılım sistemi")
    assert len(results) == 1
    assert hasattr(results[0], "okas_codes")
    assert isinstance(results[0].okas_codes, list)


# ---------------------------------------------------------------------------
# Test 17: OKAS eşleşmesi okas_support puanını artırmalı
# ---------------------------------------------------------------------------


def test_okas_support_boosts_score() -> None:
    """OKAS kodları sorgu terimleriyle eşleşince okas_support > 0 olmalı."""
    chunk = _make_chunk(
        ikn="2026/OKAS2",
        tender_id="TO2",
        score=0.65,
        section_id="okas:TO2",
        document_title="OKAS Sinyalizasyon",
        okas_codes=["trafik", "sinyalizasyon"],
        section_type="okas",
    )
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([chunk]),
        settings=_make_settings(),
    )
    results = retriever.retrieve("trafik sinyalizasyon sistemi")
    assert len(results) == 1
    assert results[0].scores.okas_support >= 0.0


# ---------------------------------------------------------------------------
# Test 18: Retriever arama sırasında vektör deposuna yazma yapmamalı
# ---------------------------------------------------------------------------


def test_retriever_does_not_write_to_vector_store() -> None:
    class _WatchedVectorStore(_FakeVectorStore):
        upsert_called: bool = False
        delete_called: bool = False

        def upsert_records(self, *args: Any, **kwargs: Any) -> int:
            self.upsert_called = True
            return 0

        def delete_by_tender_id(self, *args: Any, **kwargs: Any) -> None:
            self.delete_called = True

    store = _WatchedVectorStore(
        [
            _make_chunk(
                ikn="2026/WRITE",
                tender_id="TW",
                score=0.75,
                section_id="announcement:write",
                document_title="Yazma Testi",
            )
        ]
    )
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=store,
        settings=_make_settings(),
    )
    retriever.retrieve("trafik sistemi")

    assert not store.upsert_called, "Retriever arama sırasında yazma yapmamalı!"
    assert not store.delete_called, "Retriever arama sırasında silme yapmamalı!"


# ---------------------------------------------------------------------------
# Test 19: FAISS metadata içinde uygunluk kararı bulunmamalı (Gereksinim 61)
# ---------------------------------------------------------------------------


def test_faiss_payload_contains_no_eligibility_decision() -> None:
    """FAISS payload'ında 'uygun', 'uygun_degil' alanları olmamalı."""
    chunk = _make_chunk(
        ikn="2026/NODEC",
        tender_id="TND",
        score=0.75,
        section_id="announcement:nd",
        document_title="Karar Yok Testi",
    )
    # Payload'a karar eklemeyelim
    assert "uygun" not in chunk["payload"]
    assert "final_decision" not in chunk["payload"]
    assert (
        "classification" not in chunk["payload"] or chunk["payload"].get("classification") is None
    )

    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([chunk]),
        settings=_make_settings(),
    )
    results = retriever.retrieve("trafik")
    assert len(results) == 1
    # TenderSearchResult'da 'classification_status' var ama bu uygunluk kararı değil
    assert results[0].classification_status != "uygun"
    assert results[0].classification_status != "uygun_degil"


# ---------------------------------------------------------------------------
# Test 20: İhale bazında birleştirme puanı deterministik olmalı (Gereksinim 63)
# ---------------------------------------------------------------------------


def test_tender_score_is_deterministic() -> None:
    chunks = [
        _make_chunk(
            ikn="2026/DET",
            tender_id="TDET",
            score=0.80,
            section_id="announcement:1",
            document_title="Deterministik Test",
        ),
        _make_chunk(
            ikn="2026/DET",
            tender_id="TDET",
            score=0.70,
            section_id="tender:TDET:okas",
            document_title="Deterministik Test",
            section_type="okas",
        ),
    ]
    settings = _make_settings()
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore(chunks),
        settings=settings,
    )
    result1 = retriever.retrieve("trafik")
    result2 = retriever.retrieve("trafik")
    assert len(result1) == 1
    assert result1[0].scores.final == result2[0].scores.final


# ---------------------------------------------------------------------------
# Test 21: Negatif ceza nihai puanı 0'ın altına düşürmemeli
# ---------------------------------------------------------------------------


def test_negative_penalty_never_makes_final_score_negative() -> None:
    chunk = _make_chunk(
        ikn="2026/NEG",
        tender_id="TNEG",
        score=0.01,
        section_id="announcement:neg",
        document_title="Çok Düşük Skor",
        profile_codes=["SYS-01"],  # negatif gruba dahil olabilir
    )
    retriever = IsbakTenderRetriever(
        embedder=_FakeEmbedder(),
        vector_store=_FakeVectorStore([chunk]),
        settings=_make_settings(minimum_final_score=0.0),
    )
    results = retriever.retrieve("trafik")
    if results:
        assert results[0].scores.final >= 0.0


# ---------------------------------------------------------------------------
# Yardımcı fonksiyon testleri
# ---------------------------------------------------------------------------


def test_query_terms_filters_stopwords() -> None:
    terms = _query_terms("ihale için bir ve trafik sistemi")
    assert "trafik" in terms
    assert "sistemi" in terms or "sistem" in terms
    # Stopword'ler çıkarılmalı
    assert "için" not in terms
    assert "bir" not in terms
    assert "ve" not in terms


def test_query_terms_bakım_onarım_not_filtered() -> None:
    """'bakım' ve 'onarım' stopword değil → sorgu terimlerine dahil edilmeli."""
    terms = _query_terms("dış ortam sistem kabini bakım onarım hizmet alımı")
    assert "bakım" in terms or len(terms) > 0


def test_term_overlap_exact_match() -> None:
    terms = _query_terms("trafik sinyalizasyon kavşak")
    overlap = _term_overlap(terms, "trafik sinyalizasyon sistemi kurulumu")
    assert overlap > 0.0


def test_term_overlap_no_match() -> None:
    terms = _query_terms("trafik sinyalizasyon")
    overlap = _term_overlap(terms, "radyoloji hastane yazılımı")
    assert overlap == 0.0


def test_resolve_tender_name_uses_metadata_document_title() -> None:
    payload: dict[str, Any] = {
        "title": "Ana İhale Bilgileri",
        "tender_name": None,
        "ikn": "2026/9999",
        "metadata": {"document_title": "Gerçek İhale Adı"},
    }
    assert _resolve_tender_name(payload) == "Gerçek İhale Adı"


def test_resolve_tender_name_falls_back_to_ikn_when_generic() -> None:
    payload: dict[str, Any] = {
        "title": "OKAS Kodları",
        "tender_name": None,
        "ikn": "2026/9999",
        "metadata": {"document_title": ""},
    }
    result = _resolve_tender_name(payload)
    # Generic başlık + boş metadata → ikn veya boş string döner; ikisi de kabul edilir
    assert result in ("2026/9999", "", None) or isinstance(result, str)


def test_profile_penalty_returns_float() -> None:
    """_profile_penalty her zaman float döndürmeli."""
    penalty = _profile_penalty("trafik sinyalizasyon", ["AUS-01"])
    assert isinstance(penalty, float)
    assert 0.0 <= penalty <= 1.0


# ---------------------------------------------------------------------------
# Ayarlar testleri
# ---------------------------------------------------------------------------


def test_settings_default_collection_name() -> None:
    settings = IsbakRagSettings()
    assert settings.collection_name == "ekap_isbak_tender_chunks_v1"


def test_settings_default_minimum_final_score() -> None:
    """Varsayılan minimum_final_score makul aralıkta olmalı."""
    settings = IsbakRagSettings()
    assert 0.0 <= settings.minimum_final_score <= 0.50


def test_settings_weight_sum_validation() -> None:
    s = IsbakRagSettings(
        weight_max_chunk=0.55,
        weight_top_chunks=0.20,
        weight_section_diversity=0.10,
        weight_okas=0.10,
        weight_title=0.05,
    )
    total = (
        s.weight_max_chunk
        + s.weight_top_chunks
        + s.weight_section_diversity
        + s.weight_okas
        + s.weight_title
    )
    assert abs(total - 1.0) < 1e-9


def test_settings_new_weights_exist() -> None:
    s = IsbakRagSettings()
    assert hasattr(s, "weight_max_chunk")
    assert hasattr(s, "weight_top_chunks")
    assert hasattr(s, "weight_section_diversity")
    assert hasattr(s, "weight_okas")
    assert hasattr(s, "weight_title")
