import pickle
import faiss
from collections import Counter

def check_integrity():
    index_path = "storage/faiss/ekap_tender_chunks.index"
    payload_path = "storage/faiss/ekap_tender_chunks_payloads.pkl"
    
    print("FAISS Integrity Check")
    print("=====================")
    print(f"Index Path: {index_path}")
    print(f"Payload Path: {payload_path}")
    
    try:
        index = faiss.read_index(index_path)
        print(f"Index Dimension: {index.d}")
        print(f"Index Vector Count (ntotal): {index.ntotal}")
    except Exception as e:
        print(f"Error reading index: {e}")
        return
        
    try:
        with open(payload_path, "rb") as f:
            payload_data = pickle.load(f)
            
        print(f"Payload data type: {type(payload_data)}")
        if isinstance(payload_data, dict):
            print(f"Payload top-level keys: {list(payload_data.keys())}")
            if "payloads" in payload_data:
                payloads = payload_data["payloads"]
            else:
                payloads = payload_data
        else:
            payloads = payload_data
            
        print(f"Actual payload count: {len(payloads)}")
        print(f"index.ntotal == payload_count: {index.ntotal == len(payloads)}")
        
        tender_ids = set()
        chunk_ids = []
        missing_fields = 0
        
        if isinstance(payloads, dict):
            iterator = payloads.values()
        else:
            iterator = payloads
            
        for record in iterator:
            tender_id = record.get("tender_id") or record.get("ikn")
            chunk_id = record.get("chunk_id")
            if tender_id:
                tender_ids.add(tender_id)
            if chunk_id:
                chunk_ids.append(chunk_id)
            if not tender_id or not chunk_id:
                missing_fields += 1
                
        chunk_counter = Counter(chunk_ids)
        duplicate_chunks = sum(1 for v in chunk_counter.values() if v > 1)
        
        print(f"Unique tender count: {len(tender_ids)}")
        print(f"Unique chunk count: {len(set(chunk_ids))}")
        print(f"Duplicate chunk count: {duplicate_chunks}")
        print(f"Missing mandatory fields (tender_id/chunk_id): {missing_fields}")
        print("Integrity Status: OK" if index.ntotal == len(payloads) and duplicate_chunks == 0 else "Integrity Status: ISSUES DETECTED")
        
    except Exception as e:
        print(f"Error reading payload: {e}")

if __name__ == '__main__':
    check_integrity()
