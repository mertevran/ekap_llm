from __future__ import annotations

import argparse
import json

from app.vector_store.qdrant_store import (
    QdrantVectorStore,
)


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--qdrant-path",
        default="storage/qdrant_isbak",
    )

    parser.add_argument(
        "--collection-name",
        default="ekap_isbak_tender_chunks_v1",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    args = parser.parse_args()

    store = QdrantVectorStore(
        path=args.qdrant_path,
        collection_name=args.collection_name,
    )

    try:
        print(
            "Koleksiyon mevcut:",
            store.collection_exists(),
        )

        if not store.collection_exists():
            return 1

        print(
            "Toplam kayıt:",
            store.count(),
        )

        print()
        print("Örnek kayıtlar:")

        for item in store.scroll(limit=args.limit):
            payload = item["payload"]

            print(
                json.dumps(
                    {
                        "id": item["id"],
                        "ikn": payload.get("ikn"),
                        "title": payload.get("title"),
                        "section_id": (payload.get("section_id")),
                        "primary_profile_code": (payload.get("primary_profile_code")),
                        "profile_codes": (payload.get("profile_codes")),
                        "classification_status": (payload.get("classification_status")),
                        "text_preview": str(payload.get("text", ""))[:180],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        return 0

    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
