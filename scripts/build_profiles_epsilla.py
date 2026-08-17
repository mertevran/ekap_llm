#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.indexing.embedder import BgeM3Embedder
from app.retrieval.epsilla_tender_retriever import EpsillaClient

# FAISS ile birebir aynı profil metni/parçalama mantığı kullanılır.
from scripts.build_profiles_faiss import (
    build_profile_chunks,
    canonical_json,
    load_profiles,
    sha256_text,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PROFILES_PATH = PROJECT_ROOT / "config" / "isbak" / "profiles"
DEFAULT_DB_NAME = "EkapTestDB"
DEFAULT_TABLE_NAME = "IsbakProfiles"
DEFAULT_VECTOR_DIM = 1024

REPORT_PATH = PROJECT_ROOT / "reports" / "epsilla_profile_build_report.json"


def deterministic_id(
    profile_code: str,
    section: str,
) -> str:
    raw = f"isbak-profile|{profile_code}|{section}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_profile_table(
    epsilla: EpsillaClient,
    *,
    table_name: str,
    vector_dimension: int,
    recreate: bool,
) -> None:
    """
    Epsilla profil tablosunu hazırlar.

    Üretim FAISS dosyalarına dokunmaz.
    Yalnızca EkapTestDB içindeki IsbakProfiles tablosunu yönetir.
    """

    raw = epsilla.raw

    if recreate:
        try:
            raw.drop_table(table_name=table_name)
            print(f"[EPSILLA] Eski tablo silindi: {table_name}")
        except Exception:
            # Tablo yoksa hata değildir.
            pass

    status_code, response = raw.create_table(
        table_name=table_name,
        table_fields=[
            {
                "name": "id",
                "dataType": "STRING",
                "primaryKey": True,
            },
            {
                "name": "document_type",
                "dataType": "STRING",
            },
            {
                "name": "profile_code",
                "dataType": "STRING",
            },
            {
                "name": "profile_name",
                "dataType": "STRING",
            },
            {
                "name": "profile_family",
                "dataType": "STRING",
            },
            {
                "name": "profile_version",
                "dataType": "STRING",
            },
            {
                "name": "section",
                "dataType": "STRING",
            },
            {
                "name": "section_order",
                "dataType": "INT",
            },
            {
                "name": "text",
                "dataType": "STRING",
            },
            {
                "name": "source_path",
                "dataType": "STRING",
            },
            {
                "name": "source_hash",
                "dataType": "STRING",
            },
            {
                "name": "embedding_content_hash",
                "dataType": "STRING",
            },
            {
                "name": "embedding_model",
                "dataType": "STRING",
            },
            {
                "name": "negative_terms",
                "dataType": "STRING",
            },
            {
                "name": "supporting_profiles",
                "dataType": "STRING",
            },
            {
                "name": "embedding",
                "dataType": "VECTOR_FLOAT",
                "dimensions": vector_dimension,
            },
        ],
    )

    if status_code == 409:
        if recreate:
            raise RuntimeError(
                f"'{table_name}' tablosu silinmesine rağmen hâlâ mevcut."
            )

        print(f"[EPSILLA] Tablo zaten var: {table_name}")
        return

    if status_code not in (200, 201):
        raise RuntimeError(
            "Epsilla profil tablosu oluşturulamadı: "
            f"status={status_code}, response={response}"
        )

    print(
        f"[EPSILLA] Tablo oluşturuldu: "
        f"{table_name} | dim={vector_dimension}"
    )


def build_records(
    *,
    profiles: list[tuple[Path, dict[str, Any]]],
    embedder: BgeM3Embedder,
    embedding_model: str,
) -> tuple[
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    """
    FAISS profil oluşturucudaki build_profile_chunks() kullanılarak
    Epsilla kayıtlarını hazırlar.
    """

    prepared_chunks: list[dict[str, Any]] = []

    for source_path, profile in profiles:
        profile_code = str(profile["profil_kodu"]).strip()
        profile_name = str(profile["profil_adi"]).strip()

        chunks = build_profile_chunks(profile)

        if len(chunks) != 4:
            raise RuntimeError(
                f"{profile_code}: beklenen profil parça sayısı 4, "
                f"gerçek={len(chunks)}"
            )

        source_hash = sha256_text(
            canonical_json(profile)
        )

        embedding_content_hash = sha256_text(
            canonical_json(
                [
                    {
                        "section": chunk.section,
                        "text": chunk.text,
                    }
                    for chunk in chunks
                ]
            )
        )

        signals = (
            profile.get("ihale_kategori_sinyalleri")
            or {}
        )

        negative_terms = [
            str(value)
            for value in signals.get("negatif_terimler", [])
            if str(value).strip()
        ]

        supporting_profiles = [
            str(value)
            for value in profile.get("destekleyici_profiller", [])
            if str(value).strip()
        ]

        for order, chunk in enumerate(chunks):
            text = str(chunk.text).strip()

            if not text:
                raise RuntimeError(
                    f"{profile_code}/{chunk.section}: boş profil parçası."
                )

            prepared_chunks.append(
                {
                    "profile_code": profile_code,
                    "profile_name": profile_name,
                    "profile_family": str(
                        profile.get("profil_ailesi") or ""
                    ),
                    "profile_version": str(
                        profile.get("profil_surumu") or ""
                    ),
                    "section": chunk.section,
                    "section_order": order,
                    "text": text,
                    "source_path": str(
                        source_path.relative_to(PROJECT_ROOT)
                        if source_path.is_relative_to(PROJECT_ROOT)
                        else source_path
                    ),
                    "source_hash": source_hash,
                    "embedding_content_hash": (
                        embedding_content_hash
                    ),
                    "negative_terms": negative_terms,
                    "supporting_profiles": (
                        supporting_profiles
                    ),
                }
            )

    print(
        f"[PROFILE] Profil sayısı: {len(profiles)}"
    )
    print(
        f"[PROFILE] Toplam profil parçası: "
        f"{len(prepared_chunks)}"
    )

    texts = [
        item["text"]
        for item in prepared_chunks
    ]

    print(
        f"[EMBEDDING] BGE-M3 başlıyor | "
        f"metin={len(texts)} | cihaz=cpu"
    )

    started = time.perf_counter()

    vectors = embedder.embed(texts)

    elapsed = time.perf_counter() - started

    if len(vectors) != len(prepared_chunks):
        raise RuntimeError(
            "Profil gömme sayısı uyuşmuyor: "
            f"metin={len(prepared_chunks)}, "
            f"vektör={len(vectors)}"
        )

    if not vectors:
        raise RuntimeError(
            "Profil vektörü üretilemedi."
        )

    vector_dim = len(vectors[0])

    if vector_dim != DEFAULT_VECTOR_DIM:
        raise RuntimeError(
            f"Beklenmeyen vektör boyutu: "
            f"{vector_dim}, beklenen={DEFAULT_VECTOR_DIM}"
        )

    print(
        f"[EMBEDDING] TAMAMLANDI | "
        f"vektör={len(vectors)} | "
        f"dim={vector_dim} | "
        f"süre={elapsed:.3f}s"
    )

    records: list[dict[str, Any]] = []
    records_by_profile: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    for item, vector in zip(
        prepared_chunks,
        vectors,
        strict=True,
    ):
        profile_code = item["profile_code"]

        record = {
            "id": deterministic_id(
                profile_code,
                item["section"],
            ),
            "document_type": "company_profile",
            "profile_code": profile_code,
            "profile_name": item["profile_name"],
            "profile_family": item["profile_family"],
            "profile_version": item["profile_version"],
            "section": item["section"],
            "section_order": item["section_order"],
            "text": item["text"],
            "source_path": item["source_path"],
            "source_hash": item["source_hash"],
            "embedding_content_hash": (
                item["embedding_content_hash"]
            ),
            "embedding_model": embedding_model,
            "negative_terms": json.dumps(
                item["negative_terms"],
                ensure_ascii=False,
            ),
            "supporting_profiles": json.dumps(
                item["supporting_profiles"],
                ensure_ascii=False,
            ),
            "embedding": list(vector),
        }

        records.append(record)

        records_by_profile.setdefault(
            profile_code,
            [],
        ).append(record)

    return records, records_by_profile


def insert_batches(
    epsilla: EpsillaClient,
    *,
    table_name: str,
    records: list[dict[str, Any]],
    batch_size: int,
) -> None:
    total = 0

    for start in range(
        0,
        len(records),
        batch_size,
    ):
        batch = records[
            start:start + batch_size
        ]

        epsilla.insert_records(
            table_name,
            batch,
        )

        total += len(batch)

        print(
            f"[EPSILLA_INSERT] "
            f"{total}/{len(records)} kayıt"
        )


def validate_profiles(
    epsilla: EpsillaClient,
    *,
    table_name: str,
    records_by_profile: dict[
        str,
        list[dict[str, Any]],
    ],
) -> dict[str, Any]:
    """
    Her profil için kendi gerçek vektörüyle Epsilla araması yapar.

    Aynı profil ilk sonuçlar içerisinde bulunmuyorsa
    doğrulama başarısızdır.
    """

    passed: list[str] = []
    failed: list[dict[str, Any]] = []

    for profile_code in sorted(
        records_by_profile
    ):
        profile_records = (
            records_by_profile[profile_code]
        )

        # Identity/capability parçası öncelikli.
        probe = next(
            (
                item
                for item in profile_records
                if item["section"]
                == "identity_and_capabilities"
            ),
            profile_records[0],
        )

        result = epsilla.query(
            table_name=table_name,
            query_field="embedding",
            query_vector=probe["embedding"],
            limit=4,
            response_fields=[
                "id",
                "profile_code",
                "profile_name",
                "section",
                "text",
            ],
        )

        returned_codes = [
            str(row.get("profile_code") or "")
            for row in result
        ]

        if profile_code not in returned_codes:
            failed.append(
                {
                    "profile_code": profile_code,
                    "returned_codes": (
                        returned_codes
                    ),
                }
            )

            print(
                f"[DOĞRULAMA-HATA] "
                f"{profile_code} | "
                f"dönen={returned_codes}"
            )
            continue

        passed.append(profile_code)

        print(
            f"[DOĞRULAMA-OK] "
            f"{profile_code} | "
            f"top4={returned_codes}"
        )

    return {
        "expected_profiles": len(
            records_by_profile
        ),
        "passed_profiles": len(passed),
        "failed_profiles": len(failed),
        "passed_codes": passed,
        "failures": failed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "İSBAK iş profillerini BGE-M3 ile "
            "Epsilla test veritabanına indeksler."
        )
    )

    parser.add_argument(
        "--profiles-path",
        default=str(DEFAULT_PROFILES_PATH),
    )

    parser.add_argument(
        "--host",
        default=os.environ.get(
            "EPSILLA_HOST",
            "localhost",
        ),
    )

    parser.add_argument(
        "--port",
        type=int,
        default=int(
            os.environ.get(
                "EPSILLA_PORT",
                "8888",
            )
        ),
    )

    parser.add_argument(
        "--db",
        default=DEFAULT_DB_NAME,
    )

    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE_NAME,
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--recreate",
        action="store_true",
        help=(
            "IsbakProfiles tablosunu silip "
            "yeniden oluşturur."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    started = time.perf_counter()

    REPORT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report: dict[str, Any] = {
        "status": "running",
        "db": args.db,
        "table": args.table,
        "profiles_path": args.profiles_path,
        "device": "cpu",
    }

    try:
        settings = get_settings()

        profiles = load_profiles(
            Path(args.profiles_path)
        )

        if len(profiles) != 20:
            raise RuntimeError(
                "Profil sayısı 20 değil: "
                f"{len(profiles)}"
            )

        codes = [
            str(profile["profil_kodu"])
            for _, profile in profiles
        ]

        duplicate_codes = [
            code
            for code, count
            in Counter(codes).items()
            if count > 1
        ]

        if duplicate_codes:
            raise RuntimeError(
                "Tekrarlanan profil kodları: "
                f"{duplicate_codes}"
            )

        print("=" * 72)
        print(
            "İSBAK PROFİLLERİ → EPSILLA"
        )
        print("=" * 72)
        print(
            f"Profil dizini : "
            f"{args.profiles_path}"
        )
        print(
            f"Profil sayısı : "
            f"{len(profiles)}"
        )
        print(
            f"Model         : "
            f"{settings.embedding_model}"
        )
        print(
            "Cihaz         : cpu"
        )
        print(
            f"Epsilla       : "
            f"{args.host}:{args.port}"
        )
        print(
            f"DB / tablo    : "
            f"{args.db} / {args.table}"
        )
        print("=" * 72)

        # CPU-only zorunlu.
        embedder = BgeM3Embedder(
            model_name=settings.embedding_model,
            device="cpu",
            cache_folder=(
                settings.model_cache_path
            ),
            batch_size=(
                settings.embedding_batch_size
            ),
            show_progress_bar=True,
        )

        records, records_by_profile = (
            build_records(
                profiles=profiles,
                embedder=embedder,
                embedding_model=(
                    settings.embedding_model
                ),
            )
        )

        if len(records) != 80:
            raise RuntimeError(
                "20 profil x 4 parça = 80 "
                "kayıt bekleniyordu; "
                f"gerçek={len(records)}"
            )

        epsilla = EpsillaClient(
            host=str(args.host),
            port=int(args.port),
        )

        epsilla.connect()
        epsilla.use_db(args.db)

        create_profile_table(
            epsilla,
            table_name=args.table,
            vector_dimension=(
                DEFAULT_VECTOR_DIM
            ),
            recreate=args.recreate,
        )

        insert_batches(
            epsilla,
            table_name=args.table,
            records=records,
            batch_size=args.batch_size,
        )

        validation = validate_profiles(
            epsilla,
            table_name=args.table,
            records_by_profile=(
                records_by_profile
            ),
        )

        if (
            validation["passed_profiles"]
            != 20
        ):
            raise RuntimeError(
                "Profil doğrulaması başarısız: "
                f"{validation}"
            )

        elapsed = (
            time.perf_counter()
            - started
        )

        report.update(
            {
                "status": "completed",
                "profile_count": 20,
                "profile_chunk_count": 80,
                "vector_dimension": (
                    DEFAULT_VECTOR_DIM
                ),
                "embedding_model": (
                    settings.embedding_model
                ),
                "validation": validation,
                "elapsed_seconds": elapsed,
            }
        )

        REPORT_PATH.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print()
        print("=" * 72)
        print(
            "BAŞARILI: 20/20 PROFİL "
            "EPSILLA'YA YAZILDI VE "
            "DOĞRULANDI"
        )
        print(
            "Toplam profil parçası : 80"
        )
        print(
            "Vektör boyutu         : 1024"
        )
        print(
            f"Toplam süre           : "
            f"{elapsed:.2f}s"
        )
        print(
            f"Rapor                 : "
            f"{REPORT_PATH}"
        )
        print("=" * 72)

        return 0

    except Exception as exc:
        report.update(
            {
                "status": "failed",
                "error": str(exc),
                "elapsed_seconds": (
                    time.perf_counter()
                    - started
                ),
            }
        )

        REPORT_PATH.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            f"[HATA] {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
