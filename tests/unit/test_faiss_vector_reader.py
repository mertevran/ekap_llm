"""Unit testler — FAISS vektör okuyucu."""

from __future__ import annotations

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Test fixtures — gerçek FAISS indeksi simüle eden minimal mock
# ---------------------------------------------------------------------------

class _FlatStore:
    """Düz (IndexFlatIP benzeri) store taklidi."""

    def __init__(self, vectors: list[list[float]]) -> None:
        import faiss
        dim = len(vectors[0])
        base = faiss.IndexFlatIP(dim)
        self.index = faiss.IndexIDMap(base)
        arr = np.array(vectors, dtype=np.float32)
        faiss.normalize_L2(arr)
        ids = np.arange(1, len(vectors) + 1, dtype=np.int64)
        self.index.add_with_ids(arr, ids)
        self.payloads = {i + 1: {"chunk_id": f"c{i}", "profile_code": "TST-01"} for i in range(len(vectors))}
        self.uuid_to_id = {}


# ---------------------------------------------------------------------------
# FaissVectorReader testleri
# ---------------------------------------------------------------------------

def test_validate_consistency_ok():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3]])
    ok, msg = FaissVectorReader.validate_index_payload_consistency(store)
    assert ok, msg


def test_validate_consistency_fail():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3]])
    store.payloads[99] = {"extra": True}  # tutarsızlık ekle
    ok, msg = FaissVectorReader.validate_index_payload_consistency(store)
    assert not ok


def test_external_ids():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    ext_ids = FaissVectorReader.external_ids(store)
    assert set(ext_ids.tolist()) == {1, 2}


def test_internal_position_for_external_id():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3]])
    pos = FaissVectorReader.internal_position_for_external_id(store, 1)
    assert pos == 0


def test_internal_position_not_found():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3]])
    with pytest.raises(KeyError):
        FaissVectorReader.internal_position_for_external_id(store, 999)


def test_reconstruct_vector_by_external_id():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    raw = [0.1, 0.2, 0.3]
    store = _FlatStore([raw])
    vec = FaissVectorReader.reconstruct_vector_by_external_id(store, 1)
    assert len(vec) == 3
    # Normalize edildiği için tam eşleşme beklemiyoruz, ama boyut doğru
    assert all(isinstance(v, float) for v in vec)


def test_reconstruct_nonexistent_id():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3]])
    with pytest.raises(KeyError):
        FaissVectorReader.reconstruct_vector_by_external_id(store, 999)


def test_find_profile_entries():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    store.payloads[1]["profile_code"] = "AUS-01"
    store.payloads[2]["profile_code"] = "AUS-02"
    entries = FaissVectorReader.find_profile_entries(store, "AUS-01")
    assert len(entries) == 1
    assert entries[0][1]["profile_code"] == "AUS-01"


def test_find_tender_entries_by_ikn():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    store.payloads[1] = {"ikn": "2026/1234", "tender_id": "T1"}
    store.payloads[2] = {"ikn": "2026/5678", "tender_id": "T2"}
    entries = FaissVectorReader.find_tender_entries(store, ikn="2026/1234")
    assert len(entries) == 1
    assert entries[0][1]["ikn"] == "2026/1234"


def test_find_tender_entries_neither_raises():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    store = _FlatStore([[0.1, 0.2, 0.3]])
    with pytest.raises(ValueError, match="en az biri"):
        FaissVectorReader.find_tender_entries(store)


def test_resolve_authority_name_priority():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    payload = {
        "authority_name": "Ana İdare",
        "idare_adi": "Yedek İdare",
        "metadata": {"idare_adi": "Meta İdare"},
    }
    result = FaissVectorReader.resolve_authority_name(payload)
    assert result == "Ana İdare"


def test_resolve_authority_name_fallback():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    payload = {"metadata": {"idare_adi": "Meta İdare"}}
    result = FaissVectorReader.resolve_authority_name(payload)
    assert result == "Meta İdare"


def test_resolve_okas_codes_normalization():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    payload = {
        "okas_codes": ["72.41.00-0", "48 61 00-0"],
        "metadata": {"cpv_codes": ["72.41.00-0"]},  # tekrar — tekilleştirilmeli
    }
    codes = FaissVectorReader.resolve_okas_codes(payload)
    # Normalize: nokta ve tire kaldırıldı, tekrar → tekilleştirildi
    assert "724100" in codes or "7241000" in codes or "72410" in codes
    # Hiçbiri tekrar içermemeli
    assert len(codes) == len(set(codes))


def test_resolve_section_type_known():
    from app.vector_store.faiss_vector_reader import KNOWN_SECTION_TYPES, FaissVectorReader
    payload = {"section_type": "qualification"}
    result = FaissVectorReader.resolve_section_type(payload)
    assert result in KNOWN_SECTION_TYPES


def test_resolve_section_type_unknown_preserved():
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    payload = {"section_type": "custom_unknown_type"}
    result = FaissVectorReader.resolve_section_type(payload)
    assert result == "custom_unknown_type"
