from __future__ import annotations

import argparse
import json
import time

from app.config import get_settings
from app.domain import EmbeddedChunk, TenderChunkPackage
from app.rag import EmbeddingService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="İhale parçaları için BGE-M3 gömme vektörleri üretir."
    )

    parser.add_argument(
        "ikn",
        help="İhale kayıt numarası",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="İşlenecek parça sayısı. 0 tüm parçaları işler.",
    )

    return parser.parse_args()


def safe_ikn(ikn: str) -> str:
    return ikn.replace("/", "_").replace("\\", "_")


def main() -> int:
    args = parse_args()
    settings = get_settings()

    input_path = settings.output_dir / f"chunks_{safe_ikn(args.ikn)}.json"

    if not input_path.is_file():
        print(f"[HATA] Parça dosyası bulunamadı: {input_path}")
        return 1

    package = TenderChunkPackage.model_validate_json(input_path.read_text(encoding="utf-8"))

    chunks = package.chunks

    if args.limit > 0:
        chunks = chunks[: args.limit]

    if not chunks:
        print("[HATA] Vektöre dönüştürülecek parça bulunamadı.")
        return 1

    print("EKAP BGE-M3 Gömme Üretimi")
    print("-" * 50)
    print(f"İKN          : {args.ikn}")
    print(f"Parça sayısı : {len(chunks)}")
    print(f"Model        : {settings.embedding_model}")
    print(f"Cihaz        : {settings.embedding_device}")
    print()

    service = EmbeddingService()

    start_time = time.perf_counter()

    vectors = service.encode_texts([chunk.text for chunk in chunks])

    duration = time.perf_counter() - start_time

    embedded_chunks: list[EmbeddedChunk] = []

    for chunk, vector in zip(chunks, vectors, strict=True):
        embedded_chunks.append(
            EmbeddedChunk(
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                vector=vector,
                vector_size=len(vector),
                model_name=service.model_name,
                tender_id=chunk.tender_id,
                ikn=chunk.ikn,
                section_id=chunk.section_id,
                source_table=chunk.source_table,
                source_record_ids=chunk.source_record_ids,
            )
        )

    output_path = settings.output_dir / f"embeddings_{safe_ikn(args.ikn)}.json"

    output_data = {
        "ikn": args.ikn,
        "model": service.model_name,
        "device": service.device,
        "vector_size": service.vector_size,
        "chunk_count": len(embedded_chunks),
        "duration_seconds": round(duration, 3),
        "items": [item.model_dump() for item in embedded_chunks],
    }

    output_path.write_text(
        json.dumps(
            output_data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Vektör boyutu : {service.vector_size}")
    print(f"Üretilen sayı : {len(embedded_chunks)}")
    print(f"Toplam süre   : {duration:.2f} saniye")
    print(f"Çıktı         : {output_path}")
    print()
    print("Gömme vektörleri başarıyla üretildi.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
