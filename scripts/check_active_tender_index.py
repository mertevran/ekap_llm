from __future__ import annotations

import argparse
from pathlib import Path

from app.vector_store.qdrant_store import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_QDRANT_PATH,
    QdrantVectorStore,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aktif ihale Qdrant koleksiyonunu denetler.")
    parser.add_argument(
        "--qdrant-path",
        type=Path,
        default=DEFAULT_QDRANT_PATH,
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = QdrantVectorStore(
        path=args.qdrant_path,
        collection_name=args.collection,
    )

    try:
        if not store.collection_exists():
            raise SystemExit(f"Koleksiyon bulunamadı: {args.collection}")

        total = store.count()
        samples = store.scroll(limit=args.sample_size)

        print("Koleksiyon :", args.collection)
        print("Toplam kayıt:", total)
        print("-" * 100)

        for index, item in enumerate(samples, start=1):
            payload = item["payload"]
            print(
                f"{index}. İKN={payload.get('ikn', '-')} | "
                f"Tür={payload.get('source_table', '-')} | "
                f"Parça={payload.get('chunk_id', '-')}"
            )
            print(
                "   Başlık:",
                str(payload.get("title", "-"))[:180],
            )
            print(
                "   Metin :",
                str(payload.get("text", "-"))[:300],
            )
            print()
    finally:
        store.close()


if __name__ == "__main__":
    main()
