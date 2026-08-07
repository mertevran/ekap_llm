from __future__ import annotations

import pickle
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import faiss
import numpy as np

DEFAULT_FAISS_PATH = Path("storage/faiss")
DEFAULT_COLLECTION_NAME = "ekap_tender_chunks"


def deterministic_point_id(*parts: object) -> str:
    normalized = "|".join(str(part) for part in parts if part is not None)
    if not normalized:
        raise ValueError("Kimlik üretmek için en az bir değer gereklidir.")
    return str(uuid5(NAMESPACE_URL, normalized))


@dataclass(frozen=True)
class VectorRecord:
    point_id: str
    vector: list[float]
    payload: dict[str, Any]


class FaissVectorStore:
    """FAISS kullanarak vektör kayıtlarını yönetir."""

    def __init__(
        self,
        *,
        path: str | Path = DEFAULT_FAISS_PATH,
        collection_name: str = DEFAULT_COLLECTION_NAME,
    ) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.index_file = self.path / f"{collection_name}.index"
        self.payload_file = self.path / f"{collection_name}_payloads.pkl"

        self.index = None
        self.payloads: dict[int, dict[str, Any]] = {}
        self.uuid_to_id: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if self.collection_exists():
            self.index = faiss.read_index(str(self.index_file))
            if self.payload_file.exists():
                with open(self.payload_file, "rb") as f:
                    data = pickle.load(f)
                    self.payloads = data.get("payloads", {})
                    self.uuid_to_id = data.get("uuid_to_id", {})
            else:
                self.payloads = {}
                self.uuid_to_id = {}
        else:
            self.index = None
            self.payloads = {}
            self.uuid_to_id = {}

    def _save(self) -> None:
        if self.index is not None:
            assert self.index.ntotal == len(self.payloads), (
                f"FAISS ntotal ({self.index.ntotal}) != payload ({len(self.payloads)})"
            )

            temp_index = self.index_file.with_suffix(".index.tmp")
            temp_payload = self.payload_file.with_suffix(".pkl.tmp")

            faiss.write_index(self.index, str(temp_index))
            with open(temp_payload, "wb") as f:
                pickle.dump({"payloads": self.payloads, "uuid_to_id": self.uuid_to_id}, f)

            temp_index.replace(self.index_file)
            temp_payload.replace(self.payload_file)

    def close(self) -> None:
        self._save()

    def collection_exists(self) -> bool:
        return self.index_file.exists()

    def count(self) -> int:
        if self.index is None:
            return 0
        return self.index.ntotal

    def ensure_collection(
        self,
        *,
        vector_size: int,
        recreate: bool = False,
    ) -> None:
        if vector_size <= 0:
            raise ValueError("vector_size pozitif olmalıdır.")

        exists = self.collection_exists()

        if exists and recreate:
            if self.index_file.exists():
                self.index_file.unlink()
            if self.payload_file.exists():
                self.payload_file.unlink()
            exists = False
            self.index = None
            self.payloads = {}
            self.uuid_to_id = {}

        if not exists:
            # Cosine similarity uses FlatIP + L2 normalization
            base_index = faiss.IndexFlatIP(vector_size)
            self.index = faiss.IndexIDMap(base_index)
            self._save()
            return

        if self.index is not None and self.index.d != vector_size:
            raise ValueError(
                f"Koleksiyon vektör boyutu uyuşmuyor: "
                f"mevcut={self.index.d}, beklenen={vector_size}. "
                "Koleksiyonu yeniden oluşturmak için recreate=True kullanın."
            )

    def upsert_records(
        self,
        records: Iterable[VectorRecord],
        *,
        batch_size: int = 64,
    ) -> int:
        if self.index is None:
            raise RuntimeError("Koleksiyon oluşturulmadı.")

        if batch_size <= 0:
            raise ValueError("batch_size pozitif olmalıdır.")

        total = 0
        current_vectors = []
        current_ids = []

        for record in records:
            if record.point_id in self.uuid_to_id:
                existing_id = self.uuid_to_id[record.point_id]
                id_selector = faiss.IDSelectorBatch([existing_id])
                self.index.remove_ids(id_selector)
                if existing_id in self.payloads:
                    del self.payloads[existing_id]
                del self.uuid_to_id[record.point_id]

            new_id = len(self.uuid_to_id) + 1
            while new_id in self.payloads:
                new_id += 1

            self.uuid_to_id[record.point_id] = new_id
            self.payloads[new_id] = record.payload

            vec = np.array(record.vector, dtype=np.float32)
            faiss.normalize_L2(vec.reshape(1, -1))

            current_vectors.append(vec)
            current_ids.append(new_id)
            total += 1

            if len(current_vectors) >= batch_size:
                vecs = np.vstack(current_vectors)
                ids = np.array(current_ids, dtype=np.int64)
                self.index.add_with_ids(vecs, ids)
                current_vectors.clear()
                current_ids.clear()

        if current_vectors:
            vecs = np.vstack(current_vectors)
            ids = np.array(current_ids, dtype=np.int64)
            self.index.add_with_ids(vecs, ids)

        self._save()
        return total

    def delete_by_tender_id(
        self,
        tender_id: str,
    ) -> None:
        normalized_tender_id = str(tender_id).strip()
        if not normalized_tender_id:
            raise ValueError("tender_id boş olamaz.")

        if self.index is None:
            return

        ids_to_remove = []
        for pid, payload in list(self.payloads.items()):
            t_id = str(payload.get("tender_id") or payload.get("ikn") or "").strip()
            if t_id == normalized_tender_id:
                ids_to_remove.append(pid)
                for uuid_str, val in list(self.uuid_to_id.items()):
                    if val == pid:
                        del self.uuid_to_id[uuid_str]
                        break
                del self.payloads[pid]

        if ids_to_remove:
            id_selector = faiss.IDSelectorBatch(ids_to_remove)
            self.index.remove_ids(id_selector)
            self._save()

    def reconstruct_vector(self, faiss_id: int) -> list[float]:
        """Dış FAISS kimliğine ait vektörü güvenli biçimde yeniden oluşturur."""

        if self.index is None:
            raise RuntimeError("Koleksiyon belleğe yüklenemedi.")
        if faiss_id not in self.payloads:
            raise KeyError(f"FAISS kimliği yük verisinde bulunamadı: {faiss_id}")

        if hasattr(self.index, "id_map"):
            external_ids = faiss.vector_to_array(self.index.id_map)
            positions = np.flatnonzero(external_ids == int(faiss_id))
            if positions.size == 0:
                raise KeyError(f"FAISS kimliği indeks içinde bulunamadı: {faiss_id}")
            vector = self.index.index.reconstruct(int(positions[0]))
        else:
            vector = self.index.reconstruct(int(faiss_id))

        return np.asarray(vector, dtype=np.float32).tolist()

    def search_subset(
        self,
        *,
        query_vector: list[float],
        allowed_ids: Iterable[int],
        limit: int,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Yalnız verilen FAISS kimlikleri içinde tam kosinüs araması yapar."""

        if limit <= 0:
            raise ValueError("limit pozitif olmalıdır.")
        if self.index is None or self.index.ntotal == 0:
            return []

        normalized_ids = list(
            dict.fromkeys(
                int(faiss_id)
                for faiss_id in allowed_ids
                if int(faiss_id) in self.payloads
            )
        )
        if not normalized_ids:
            return []

        query = np.asarray(query_vector, dtype=np.float32).reshape(1, -1)
        if query.shape[1] != self.index.d:
            raise ValueError(
                "Sorgu vektör boyutu koleksiyonla uyuşmuyor: "
                f"sorgu={query.shape[1]}, koleksiyon={self.index.d}"
            )
        faiss.normalize_L2(query)

        matrix = np.vstack(
            [
                np.asarray(self.reconstruct_vector(faiss_id), dtype=np.float32)
                for faiss_id in normalized_ids
            ]
        )
        faiss.normalize_L2(matrix)
        scores = matrix @ query[0]
        ranked = sorted(
            zip(normalized_ids, scores, strict=True),
            key=lambda item: (-float(item[1]), item[0]),
        )

        results: list[dict[str, Any]] = []
        for faiss_id, score_value in ranked:
            score = float(score_value)
            if score_threshold is not None and score < score_threshold:
                continue
            results.append(
                {
                    "id": str(faiss_id),
                    "score": score,
                    "payload": self.payloads[faiss_id],
                }
            )
            if len(results) >= limit:
                break
        return results

    def search(
        self,
        *,
        query_vector: list[float],
        limit: int,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        if self.index is None or self.index.ntotal == 0:
            return []

        vec = np.array(query_vector, dtype=np.float32).reshape(1, -1)
        faiss.normalize_L2(vec)

        scores, ids = self.index.search(vec, limit)

        results = []
        for i in range(len(ids[0])):
            pid = int(ids[0][i])
            score = float(scores[0][i])

            if pid == -1:
                break

            if score_threshold is not None and score < score_threshold:
                continue

            if pid in self.payloads:
                results.append(
                    {
                        "id": str(pid),  # Return string ID for compatibility if needed
                        "score": score,
                        "payload": self.payloads[pid],
                    }
                )

        return results
