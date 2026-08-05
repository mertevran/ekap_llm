from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.indexing.embedder import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MODEL_CACHE,
    BgeM3Embedder,
)
from app.retrieval.semantic_retriever import (
    RetrievalConfig,
    SemanticRetriever,
)
from app.vector_store.qdrant_store import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_QDRANT_PATH,
    QdrantVectorStore,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Qdrant üzerinde anlamsal ve sözcüksel puanları birleştirerek karma arama yapar."
        )
    )

    parser.add_argument(
        "query",
        help="Aranacak metin.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Döndürülecek en fazla sonuç sayısı.",
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=40,
        help="Qdrant'tan ilk aşamada alınacak aday sayısı.",
    )
    parser.add_argument(
        "--minimum-semantic-score",
        type=float,
        default=0.55,
        help="En düşük anlamsal benzerlik puanı.",
    )
    parser.add_argument(
        "--minimum-final-score",
        type=float,
        default=0.50,
        help="En düşük karma nihai puan.",
    )
    parser.add_argument(
        "--max-chunks-per-tender",
        type=int,
        default=2,
        help="Aynı İKN'den alınabilecek en fazla parça sayısı.",
    )
    parser.add_argument(
        "--semantic-weight",
        type=float,
        default=0.75,
        help="Nihai puanda anlamsal benzerliğin ağırlığı.",
    )
    parser.add_argument(
        "--lexical-weight",
        type=float,
        default=0.25,
        help="Nihai puanda sözcüksel eşleşmenin ağırlığı.",
    )
    parser.add_argument(
        "--title-weight",
        type=float,
        default=0.70,
        help="Sözcüksel puanda başlık eşleşmesinin ağırlığı.",
    )
    parser.add_argument(
        "--text-weight",
        type=float,
        default=0.30,
        help="Sözcüksel puanda parça metni eşleşmesinin ağırlığı.",
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
        "--model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="Sorgu gömme vektörü modeli.",
    )
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=DEFAULT_MODEL_CACHE,
        help="Model önbellek dizini.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Model çalışma aygıtı: cpu veya cuda.",
    )
    parser.add_argument(
        "--embed-batch-size",
        type=int,
        default=4,
        help="Gömme vektörü grup büyüklüğü.",
    )

    return parser.parse_args()


def select_text(payload: dict[str, Any]) -> str:
    value = payload.get("text")

    if isinstance(value, str) and value.strip():
        return value.strip()

    return "(Metin alanı bulunamadı.)"


def select_title(payload: dict[str, Any]) -> str:
    value = payload.get("title")

    if isinstance(value, str) and value.strip():
        return value.strip()

    metadata = payload.get("metadata")

    if isinstance(metadata, dict):
        document_title = metadata.get("document_title")

        if isinstance(document_title, str) and document_title.strip():
            return document_title.strip()

    return "(Başlık bulunamadı.)"


def main() -> None:
    args = parse_args()

    embedder = BgeM3Embedder(
        model_name=args.model,
        device=args.device,
        cache_folder=args.model_cache,
        batch_size=args.embed_batch_size,
        show_progress_bar=False,
    )

    store = QdrantVectorStore(
        path=args.path,
        collection_name=args.collection,
    )

    try:
        if not store.collection_exists():
            raise SystemExit(f"Koleksiyon bulunamadı: {args.collection}")

        retriever = SemanticRetriever(
            embedder=embedder,
            vector_store=store,
            config=RetrievalConfig(
                final_limit=args.limit,
                candidate_limit=args.candidate_limit,
                minimum_semantic_score=(args.minimum_semantic_score),
                minimum_final_score=(args.minimum_final_score),
                max_chunks_per_tender=(args.max_chunks_per_tender),
                semantic_weight=args.semantic_weight,
                lexical_weight=args.lexical_weight,
                title_weight=args.title_weight,
                text_weight=args.text_weight,
            ),
        )

        results = retriever.retrieve(args.query)

    finally:
        store.close()

    print(f"Sorgu: {args.query}")
    print(
        "Minimum anlamsal puan:",
        f"{args.minimum_semantic_score:.2f}",
    )
    print(
        "Minimum nihai puan:",
        f"{args.minimum_final_score:.2f}",
    )
    print(
        "İKN başına en fazla parça:",
        args.max_chunks_per_tender,
    )
    print(f"Sonuç sayısı: {len(results)}")
    print("-" * 100)

    if not results:
        print("Yeterince ilgili ihale parçası bulunamadı.")
        return

    for index, result in enumerate(results, start=1):
        payload = result["payload"]

        print(f"{index}. Nihai puan: {result['final_score']:.4f}")
        print(
            "   Anlamsal puan :",
            f"{result['semantic_score']:.4f}",
        )
        print(
            "   Sözcüksel puan:",
            f"{result['lexical_score']:.4f}",
        )
        print(
            "   Başlık örtüşmesi:",
            f"{result['title_overlap']:.4f}",
        )
        print(
            "   Metin örtüşmesi :",
            f"{result['text_overlap']:.4f}",
        )
        print(f"   İKN       : {payload.get('ikn', '-')}")
        print(f"   Başlık    : {select_title(payload)}")
        print(
            "   Kaynak    :",
            payload.get("source_table", "-"),
        )
        print(f"   Metin     : {select_text(payload)[:500]}")
        print()


if __name__ == "__main__":
    main()
