import pytest
import numpy as np
from uuid import uuid4
from pathlib import Path

from app.vector_store.faiss_store import FaissVectorStore, VectorRecord

@pytest.fixture
def temp_faiss_store(tmp_path):
    store_path = tmp_path / "faiss_test"
    store = FaissVectorStore(path=store_path, collection_name="test_collection")
    store.ensure_collection(vector_size=2, recreate=True)
    yield store
    if store.index_file.exists():
        store.index_file.unlink()
    if store.payload_file.exists():
        store.payload_file.unlink()

def create_records(tender_id: str, count: int) -> list[VectorRecord]:
    records = []
    for i in range(count):
        chunk_id = f"{tender_id}_chunk_{i}"
        records.append(
            VectorRecord(
                point_id=chunk_id,
                vector=[float(i), float(i)],
                payload={"tender_id": tender_id, "chunk_id": chunk_id}
            )
        )
    return records

def test_successful_replace(temp_faiss_store):
    # Insert tender 1 and 2
    temp_faiss_store.upsert_records(create_records("T1", 3))
    temp_faiss_store.upsert_records(create_records("T2", 2))
    
    assert temp_faiss_store.count() == 5
    
    # Replace Tender 1 with 4 new chunks
    new_t1_records = create_records("T1", 4)
    temp_faiss_store.replace_tenders_records(["T1"], new_t1_records)
    
    assert temp_faiss_store.count() == 6 # 2 (from T2) + 4 (from T1)
    
    payloads = temp_faiss_store.payloads.values()
    t1_count = sum(1 for p in payloads if p["tender_id"] == "T1")
    t2_count = sum(1 for p in payloads if p["tender_id"] == "T2")
    
    assert t1_count == 4
    assert t2_count == 2
    assert temp_faiss_store.index.ntotal == len(temp_faiss_store.payloads)

def test_dimension_error_preserves_old_records(temp_faiss_store):
    temp_faiss_store.upsert_records(create_records("T1", 2))
    
    bad_records = [
        VectorRecord(point_id="bad", vector=[1.0, 2.0, 3.0], payload={"tender_id": "T1", "chunk_id": "bad"})
    ]
    
    with pytest.raises(ValueError):
        temp_faiss_store.replace_tenders_records(["T1"], bad_records)
        
    assert temp_faiss_store.count() == 2 # T1 is still there

def test_duplicate_chunk_id(temp_faiss_store):
    temp_faiss_store.upsert_records(create_records("T1", 2))
    
    dup_records = [
        VectorRecord(point_id="T1_dup", vector=[1.0, 1.0], payload={"tender_id": "T1", "chunk_id": "T1_dup"}),
        VectorRecord(point_id="T1_dup", vector=[2.0, 2.0], payload={"tender_id": "T1", "chunk_id": "T1_dup"}),
    ]
    
    # Duplicate chunk ID is now explicitly forbidden and should raise ValueError
    with pytest.raises(ValueError, match="Yeni FAISS kayıtlarında yinelenen point_id bulundu."):
        temp_faiss_store.replace_tenders_records(["T1"], dup_records)
    
    # Assert that old FAISS records are strictly preserved
    assert temp_faiss_store.index.ntotal == 2
    assert len(temp_faiss_store.payloads) == 2
    
    payloads = temp_faiss_store.payloads.values()
    t1_count = sum(1 for p in payloads if p["tender_id"] == "T1")
    assert t1_count == 2

def test_disk_write_error_rollback(temp_faiss_store, monkeypatch):
    temp_faiss_store.upsert_records(create_records("T1", 2))
    
    # Mock _save to throw an exception
    def mock_save(*args, **kwargs):
        raise OSError("Disk full")
    
    monkeypatch.setattr(temp_faiss_store, "_save", mock_save)
    
    new_t1_records = create_records("T1", 1)
    
    with pytest.raises(OSError, match="Disk full"):
        temp_faiss_store.replace_tenders_records(["T1"], new_t1_records)
        
    # Check that memory rolled back
    assert temp_faiss_store.count() == 2
    assert len(temp_faiss_store.payloads) == 2

def test_temp_file_save_mechanism(temp_faiss_store):
    # Just to exercise the safe save paths
    temp_faiss_store.upsert_records(create_records("T1", 1))
    
    assert temp_faiss_store.index_file.exists()
    assert temp_faiss_store.payload_file.exists()
    
    assert not temp_faiss_store.index_file.with_suffix(".index.tmp").exists()
    assert not temp_faiss_store.index_file.with_suffix(".index.bak").exists()
