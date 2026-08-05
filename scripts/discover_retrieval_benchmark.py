from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from statistics import mean
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

DEFAULT_QUERY_FILE = Path("tests/data/retrieval_benchmark_queries.json")

DEFAULT_OUTPUT_FILE = Path("benchmark_results/retrieval_discovery.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Karma bilgi getirme benchmark sorgularını çalıştırır ve etiketleme için sonuç üretir."
        )
    )

    parser.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_QUERY_FILE,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=40,
    )
    parser.add_argument(
        "--minimum-semantic-score",
        type=float,
        default=0.55,
    )
    parser.add_argument(
        "--minimum-final-score",
        type=float,
        default=0.50,
    )
    parser.add_argument(
        "--max-chunks-per-tender",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--semantic-weight",
        type=float,
        default=0.75,
    )
    parser.add_argument(
        "--lexical-weight",
        type=float,
        default=0.25,
    )
    parser.add_argument(
        "--title-weight",
        type=float,
        default=0.70,
    )
    parser.add_argument(
        "--text-weight",
        type=float,
        default=0.30,
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_QDRANT_PATH,
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_EMBEDDING_MODEL,
    )
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=DEFAULT_MODEL_CACHE,
    )
    parser.add_argument(
        "--device",
        default="cpu",
    )
    parser.add_argument(
        "--embed-batch-size",
        type=int,
        default=4,
    )

    return parser.parse_args()


def load_queries(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Sorgu dosyası bulunamadı: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))

    queries = raw.get("queries")

    if not isinstance(queries, list) or not queries:
        raise ValueError("Sorgu dosyasında dolu bir queries listesi olmalıdır.")

    required_fields = {
        "id",
        "category",
        "query",
    }

    normalized: list[dict[str, str]] = []

    for index, item in enumerate(
        queries,
        start=1,
    ):
        if not isinstance(item, dict):
            raise ValueError(f"{index}. sorgu nesne biçiminde değildir.")

        missing = required_fields - item.keys()

        if missing:
            raise ValueError(f"{index}. sorguda eksik alanlar: {sorted(missing)}")

        query = str(item["query"]).strip()

        if not query:
            raise ValueError(f"{index}. sorgunun metni boştur.")

        normalized.append(
            {
                "id": str(item["id"]),
                "category": str(item["category"]),
                "query": query,
            }
        )

    return normalized


def select_title(
    payload: dict[str, Any],
) -> str:
    title = payload.get("title")

    if isinstance(title, str) and title.strip():
        return title.strip()

    metadata = payload.get("metadata")

    if isinstance(metadata, dict):
        document_title = metadata.get("document_title")

        if isinstance(document_title, str) and document_title.strip():
            return document_title.strip()

    return ""


def select_text(
    payload: dict[str, Any],
) -> str:
    value = payload.get("text")

    if isinstance(value, str):
        return value.strip()

    return ""


def serialize_result(
    result: dict[str, Any],
    rank: int,
) -> dict[str, Any]:
    payload = result.get("payload") or {}

    return {
        "rank": rank,
        "id": str(result.get("id", "")),
        "ikn": str(payload.get("ikn", "")),
        "title": select_title(payload),
        "source_table": str(payload.get("source_table", "")),
        "semantic_score": round(
            float(result["semantic_score"]),
            6,
        ),
        "lexical_score": round(
            float(result["lexical_score"]),
            6,
        ),
        "title_overlap": round(
            float(result["title_overlap"]),
            6,
        ),
        "text_overlap": round(
            float(result["text_overlap"]),
            6,
        ),
        "final_score": round(
            float(result["final_score"]),
            6,
        ),
        "text_preview": select_text(payload)[:700],
        "relevant": None,
        "relevance_note": "",
    }


def main() -> None:
    args = parse_args()
    queries = load_queries(args.queries)

    config = RetrievalConfig(
        final_limit=args.limit,
        candidate_limit=args.candidate_limit,
        minimum_semantic_score=(args.minimum_semantic_score),
        minimum_final_score=(args.minimum_final_score),
        max_chunks_per_tender=(args.max_chunks_per_tender),
        semantic_weight=args.semantic_weight,
        lexical_weight=args.lexical_weight,
        title_weight=args.title_weight,
        text_weight=args.text_weight,
    )

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

    records: list[dict[str, Any]] = []
    durations: list[float] = []

    try:
        if not store.collection_exists():
            raise SystemExit(f"Koleksiyon bulunamadı: {args.collection}")

        retriever = SemanticRetriever(
            embedder=embedder,
            vector_store=store,
            config=config,
        )

        for item in queries:
            started = time.perf_counter()

            results = retriever.retrieve(item["query"])

            elapsed_ms = (time.perf_counter() - started) * 1000

            durations.append(elapsed_ms)

            serialized_results = [
                serialize_result(
                    result,
                    rank,
                )
                for rank, result in enumerate(
                    results,
                    start=1,
                )
            ]

            records.append(
                {
                    **item,
                    "duration_ms": round(
                        elapsed_ms,
                        3,
                    ),
                    "result_count": len(serialized_results),
                    "results": serialized_results,
                }
            )

            print(f"{item['id']} | {elapsed_ms:.1f} ms | {len(results)} sonuç | {item['query']}")

    finally:
        store.close()

    output = {
        "benchmark_type": "retrieval_discovery",
        "config": {
            "final_limit": config.final_limit,
            "candidate_limit": config.candidate_limit,
            "minimum_semantic_score": config.minimum_semantic_score,
            "minimum_final_score": config.minimum_final_score,
            "max_chunks_per_tender": config.max_chunks_per_tender,
            "semantic_weight": config.semantic_weight,
            "lexical_weight": config.lexical_weight,
            "title_weight": config.title_weight,
            "text_weight": config.text_weight,
        },
        "query_count": len(records),
        "timing": {
            "mean_ms": round(
                mean(durations),
                3,
            )
            if durations
            else 0.0,
            "minimum_ms": round(
                min(durations),
                3,
            )
            if durations
            else 0.0,
            "maximum_ms": round(
                max(durations),
                3,
            )
            if durations
            else 0.0,
        },
        "queries": records,
    }

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.output.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Keşif çıktısı kaydedildi: {args.output}")


if __name__ == "__main__":
    main()
