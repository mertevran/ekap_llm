from __future__ import annotations

import argparse
from pathlib import Path

from app.config import get_settings
from app.database import TenderNotFoundError, TenderRepository
from app.documents import TenderChunker, TenderDocumentBuilder
from app.domain import TenderChunkPackage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("İhale verisini RAG için düzenli metin parçalarına dönüştürür.")
    )

    parser.add_argument(
        "ikn",
        help="İhale kayıt numarası",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=1800,
        help="Bir parçadaki en fazla karakter sayısı",
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=200,
        help="Ardışık parçalar arasındaki örtüşme",
    )

    return parser.parse_args()


def build_output_path(
    output_dir: Path,
    ikn: str,
) -> Path:
    safe_ikn = ikn.replace("/", "_").replace("\\", "_")
    return output_dir / f"chunks_{safe_ikn}.json"


def main() -> int:
    args = parse_args()
    settings = get_settings()

    repository = TenderRepository()
    document_builder = TenderDocumentBuilder()
    chunker = TenderChunker(
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
    )

    print("EKAP İhale Belgesi ve Parça Oluşturma")
    print("-" * 55)
    print(f"İKN             : {args.ikn}")
    print(f"En fazla boyut  : {args.max_chars}")
    print(f"Örtüşme         : {args.overlap_chars}")
    print()

    try:
        tender = repository.get_by_ikn(args.ikn)
        document = document_builder.build(tender)
        chunks = chunker.chunk_document(document)
    except TenderNotFoundError as exc:
        print(f"[HATA] {exc}")
        return 1
    except Exception as exc:
        print(f"[HATA] Parçalar oluşturulamadı: {exc}")
        return 1

    package = TenderChunkPackage(
        document=document,
        chunks=chunks,
    )

    output_path = build_output_path(
        settings.output_dir,
        tender.ikn,
    )

    output_path.write_text(
        package.model_dump_json(indent=2),
        encoding="utf-8",
    )

    total_characters = sum(len(chunk.text) for chunk in chunks)

    source_counts: dict[str, int] = {}

    for chunk in chunks:
        source_counts[chunk.source_table] = source_counts.get(chunk.source_table, 0) + 1

    print(f"İhale adı       : {document.title}")
    print(f"Bölüm sayısı    : {len(document.sections)}")
    print(f"Parça sayısı    : {len(chunks)}")
    print(f"Toplam karakter : {total_characters}")
    print()

    for source_table, count in sorted(source_counts.items()):
        print(f"{source_table:<28}: {count} parça")

    print()
    print(f"JSON çıktısı    : {output_path}")
    print("İhale metin parçaları başarıyla oluşturuldu.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
