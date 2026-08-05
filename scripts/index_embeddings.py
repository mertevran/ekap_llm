from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.vector_store.qdrant_store import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_QDRANT_PATH,
    QdrantVectorStore,
    VectorRecord,
    deterministic_point_id,
)

VECTOR_KEYS = ("embedding", "vector", "dense_vector", "embeddings")
RECORD_LIST_KEYS = ("records", "items", "chunks", "embeddings", "data")
TEXT_KEYS = ("text", "content", "chunk_text", "metin")
ID_KEYS = ("chunk_id", "id", "point_id")
IKN_KEYS = ("ikn", "tender_ikn")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gömme vektörlerini Qdrant'a kaydeder.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Gömme vektörü JSON dosyası. Verilmezse outputs içindeki en yeni dosya kullanılır.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_QDRANT_PATH,
        help="Qdrant yerel veri dizini.",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help="Qdrant koleksiyon adı.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Toplu kayıt büyüklüğü.",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Mevcut koleksiyonu silip yeniden oluşturur.",
    )
    return parser.parse_args()


def find_latest_embeddings_file() -> Path:
    files = sorted(
        Path("outputs").glob("embeddings_*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not files:
        raise FileNotFoundError("outputs dizininde embeddings_*.json dosyası bulunamadı.")
    return files[-1]


def extract_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = None
        for key in RECORD_LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                records = value
                break

        if records is None:
            raise ValueError(
                "JSON içinde kayıt listesi bulunamadı. "
                f"Aranan alanlar: {', '.join(RECORD_LIST_KEYS)}"
            )
    else:
        raise ValueError("JSON kök değeri liste veya nesne olmalıdır.")

    if not all(isinstance(record, dict) for record in records):
        raise ValueError("Kayıt listesindeki tüm elemanlar nesne olmalıdır.")

    return records


def first_present(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def extract_vector(record: dict[str, Any]) -> list[float]:
    raw_vector = first_present(record, VECTOR_KEYS)

    if not isinstance(raw_vector, list) or not raw_vector:
        raise ValueError(
            f"Kayıtta geçerli vektör bulunamadı. Aranan alanlar: {', '.join(VECTOR_KEYS)}"
        )

    try:
        return [float(value) for value in raw_vector]
    except (TypeError, ValueError) as exc:
        raise ValueError("Vektör yalnızca sayısal değerlerden oluşmalıdır.") from exc


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]

    return str(value)


def build_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = {key: json_safe(value) for key, value in record.items() if key not in VECTOR_KEYS}

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            payload.setdefault(str(key), value)

    return payload


def build_vector_records(
    records: list[dict[str, Any]],
) -> tuple[list[VectorRecord], int]:
    vector_records: list[VectorRecord] = []
    expected_size: int | None = None

    for index, record in enumerate(records):
        vector = extract_vector(record)

        if expected_size is None:
            expected_size = len(vector)
        elif len(vector) != expected_size:
            raise ValueError(
                f"{index}. kaydın vektör boyutu {len(vector)}, beklenen boyut {expected_size}."
            )

        payload = build_payload(record)
        source_id = first_present(record, ID_KEYS)
        ikn = first_present(record, IKN_KEYS)

        if ikn is None and isinstance(record.get("metadata"), dict):
            ikn = first_present(record["metadata"], IKN_KEYS)

        text = first_present(record, TEXT_KEYS)
        if text is None and isinstance(record.get("metadata"), dict):
            text = first_present(record["metadata"], TEXT_KEYS)

        point_id = deterministic_point_id(
            ikn,
            source_id,
            index if source_id is None else None,
            text,
        )

        payload.setdefault("source_index", index)

        vector_records.append(
            VectorRecord(
                point_id=point_id,
                vector=vector,
                payload=payload,
            )
        )

    if expected_size is None:
        raise ValueError("İndekslenecek kayıt bulunamadı.")

    return vector_records, expected_size


def main() -> None:
    args = parse_args()
    input_path = args.input or find_latest_embeddings_file()

    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    raw_records = extract_records(data)
    vector_records, vector_size = build_vector_records(raw_records)

    store = QdrantVectorStore(
        path=args.path,
        collection_name=args.collection,
    )

    try:
        store.ensure_collection(
            vector_size=vector_size,
            recreate=args.recreate,
        )
        inserted = store.upsert_records(
            vector_records,
            batch_size=args.batch_size,
        )
        total = store.count()
    finally:
        store.close()

    print("Qdrant indeksleme tamamlandı")
    print("-" * 60)
    print("Girdi dosyası :", input_path)
    print("Koleksiyon    :", args.collection)
    print("Vektör boyutu :", vector_size)
    print("İşlenen kayıt :", inserted)
    print("Toplam kayıt  :", total)


if __name__ == "__main__":
    main()
