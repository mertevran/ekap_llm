"""İndeks durum ve önbellek testleri (Gereksinim 52: 16-23).

Durum tablosunun source_hash, chunking_version, embedding_model vb.
birlikte değerlendirilerek yeniden işleme kararı verdiğini doğrular.
"""

from __future__ import annotations

# ─── Sahte durum nesnesi ──────────────────────────────────────────────────────


class _FakeState:
    def __init__(
        self,
        *,
        source_hash: str = "abc123",
        index_status: str = "indexed",
        chunking_version: str = "section-aware-v1",
        embedding_model: str = "BAAI/bge-m3",
        embedding_version: str = "1.0",
        index_version: str = "active-tenders-v1",
        cache_version: str = "v1",
        classification: str | None = None,
    ):
        self.source_hash = source_hash
        self.index_status = index_status
        self.chunking_version = chunking_version
        self.embedding_model = embedding_model
        self.embedding_version = embedding_version
        self.index_version = index_version
        self.cache_version = cache_version
        self.classification = classification
        self.indexed_at = None


# ─── Yeniden işleme mantığını test et ────────────────────────────────────────


def _needs_reprocessing(state: _FakeState | None, current: dict) -> bool:
    """active_tender_indexer.py içindeki yeniden işleme mantığını yansıtır."""
    if state is None:
        return True
    if state.index_status != "indexed":
        return True
    if state.source_hash != current["source_hash"]:
        return True
    if state.chunking_version != current["chunking_version"]:
        return True
    if state.embedding_model != current["embedding_model"]:
        return True
    if state.embedding_version != current["embedding_version"]:
        return True
    if state.index_version != current["index_version"]:
        return True
    if state.cache_version != current["cache_version"]:
        return True
    return False


CURRENT = {
    "source_hash": "abc123",
    "chunking_version": "section-aware-v1",
    "embedding_model": "BAAI/bge-m3",
    "embedding_version": "1.0",
    "index_version": "active-tenders-v1",
    "cache_version": "v1",
}


# ---------------------------------------------------------------------------
# Test 16: Aynı source_hash → yeniden gömme yapılmamalı
# ---------------------------------------------------------------------------


def test_same_source_hash_no_reprocessing() -> None:
    state = _FakeState(source_hash="abc123", index_status="indexed")
    assert _needs_reprocessing(state, CURRENT) is False


# ---------------------------------------------------------------------------
# Test 17: Değişen source_hash → yeniden işleme
# ---------------------------------------------------------------------------


def test_changed_source_hash_triggers_reprocessing() -> None:
    state = _FakeState(source_hash="DIFFERENT_HASH", index_status="indexed")
    assert _needs_reprocessing(state, CURRENT) is True


# ---------------------------------------------------------------------------
# Test 18: chunking_version değişimi → yeniden işleme
# ---------------------------------------------------------------------------


def test_chunking_version_change_triggers_reprocessing() -> None:
    state = _FakeState(chunking_version="old-v0")
    assert _needs_reprocessing(state, CURRENT) is True


# ---------------------------------------------------------------------------
# Test 19: embedding_model değişimi → yeniden işleme
# ---------------------------------------------------------------------------


def test_embedding_model_change_triggers_reprocessing() -> None:
    state = _FakeState(embedding_model="BAAI/bge-large-en-v1.5")
    assert _needs_reprocessing(state, CURRENT) is True


# ---------------------------------------------------------------------------
# Test 20: "failed" durumundaki kayıt tekrar işlenmeli
# ---------------------------------------------------------------------------


def test_failed_status_triggers_reprocessing() -> None:
    state = _FakeState(index_status="failed")
    assert _needs_reprocessing(state, CURRENT) is True


# ---------------------------------------------------------------------------
# Test 21: State None ise (ilk kez görülen ihale) işlenmeli
# ---------------------------------------------------------------------------


def test_no_state_triggers_reprocessing() -> None:
    assert _needs_reprocessing(None, CURRENT) is True


# ---------------------------------------------------------------------------
# Test 22: index_version değişimi → yeniden işleme
# ---------------------------------------------------------------------------


def test_index_version_change_triggers_reprocessing() -> None:
    state = _FakeState(index_version="old-v0")
    assert _needs_reprocessing(state, CURRENT) is True


# ---------------------------------------------------------------------------
# Test 23: classification alanı yeni uygunluk kararı olarak kullanılmamalı
# ---------------------------------------------------------------------------


def test_classification_not_used_as_eligibility_decision() -> None:
    """TenderIndexState.classification teknik sınıflandırma içindir,
    yeni model kararı olarak kullanılmamalı."""
    import inspect

    from app.indexing.active_tender_indexer import ActiveTenderIndexer

    source = inspect.getsource(ActiveTenderIndexer.run)
    # 'classification' okunabilir ama bunu 'uygun/uygun_degil' olarak kullanamalı
    # FAISS payload'ına 'final_decision' olarak yazılmamalı
    assert "final_decision" not in source, (
        "ActiveTenderIndexer.run() FAISS'e final_decision yazmamalı"
    )


# ---------------------------------------------------------------------------
# Test 24: source_hash deterministik üretilmeli
# ---------------------------------------------------------------------------


def test_source_hash_is_deterministic() -> None:
    """Aynı veriden üretilen hash her seferinde aynı olmalı."""
    from app.indexing.hash_generator import generate_source_hash

    class _MockTender:
        id = "T001"
        ikn = "2026/001"
        adi = "Test İhalesi"
        idare_adi = "Test İdare"
        il = "İstanbul"
        ihale_tarihi = "2026-12-01 10:00:00"
        ihale_turu = "Hizmet Alımı"
        ihale_usulu = "Açık İhale"
        ihale_durumu = "aktif"
        kapsam = "Test kapsam"
        e_ihale = 1
        kismi_teklif = 0
        dokuman_sayisi = 5
        ihale_yeri = "İstanbul"
        isin_yeri = "İstanbul Geneli"
        updated_at = "2026-01-01T00:00:00"
        announcements = []
        characteristics = []
        okas_codes = []

    tender = _MockTender()
    hash1 = generate_source_hash(tender)
    hash2 = generate_source_hash(tender)
    assert hash1 == hash2, "source_hash deterministik olmalı"
    assert isinstance(hash1, str)
    assert len(hash1) > 0
