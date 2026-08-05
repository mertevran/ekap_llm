#!/usr/bin/env python
"""
scripts/build_profiles_faiss.py

İSBAK profillerini JSON dosyalarından okuyarak:
1. Profil içeriğini sabit bölümlere ayırır.
2. BGE-M3 ile normalize edilmiş gömme vektörleri üretir.
3. Profilleri ayrı bir FAISS indeksine yazar.
4. llm_rag.profile_index_state tablosunda işlem durumunu izler.
5. Değişmemiş profilleri tekrar vektörlemez.

Varsayılan çıktılar:
  storage/faiss_profiles/isbak_company_profiles.index
  storage/faiss_profiles/isbak_company_profiles_payloads.pkl
  reports/profile_faiss_build_report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import get_settings
from app.database.connection import get_connection
from app.indexing.embedder import BgeM3Embedder
from app.vector_store.faiss_store import (
    FaissVectorStore,
    VectorRecord,
    deterministic_point_id,
)

DEFAULT_PROFILES_PATH = Path("config/isbak/profiles")
DEFAULT_FAISS_PATH = Path("storage/faiss_profiles")
DEFAULT_COLLECTION = "isbak_company_profiles"
DEFAULT_STATE_TABLE = "llm_rag.profile_index_state"
DEFAULT_CHUNKING_VERSION = "profile-sections-v1"
DEFAULT_EMBEDDING_VERSION = "bge-m3-normalized-v1"
DEFAULT_INDEX_VERSION = "profile-faiss-v1"
REPORT_PATH = Path("reports/profile_faiss_build_report.json")


@dataclass(frozen=True)
class ProfileChunk:
    section: str
    text: str


@dataclass
class BuildStats:
    discovered: int = 0
    selected: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    chunk_count: int = 0
    faiss_total: int = 0
    elapsed_seconds: float = 0.0


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_json(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def list_text(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, list):
        return "\n".join(f"- {str(value).strip()}" for value in values if str(value).strip())
    return str(values).strip()


def object_list_text(values: Any) -> str:
    if not values:
        return ""
    lines: list[str] = []
    for value in values:
        if isinstance(value, dict):
            parts: list[str] = []
            for key, item in value.items():
                if item in (None, "", [], {}):
                    continue
                if isinstance(item, list):
                    rendered = ", ".join(str(entry) for entry in item)
                else:
                    rendered = str(item)
                parts.append(f"{key}: {rendered}")
            if parts:
                lines.append("- " + " | ".join(parts))
        elif str(value).strip():
            lines.append(f"- {str(value).strip()}")
    return "\n".join(lines)


def build_profile_chunks(profile: dict[str, Any]) -> list[ProfileChunk]:
    signals = profile.get("ihale_kategori_sinyalleri") or {}
    context_policy = profile.get("context_policy") or {}

    identity = "\n".join(
        part
        for part in [
            f"Profil kodu: {profile.get('profil_kodu', '')}",
            f"Profil adı: {profile.get('profil_adi', '')}",
            f"Profil ailesi: {profile.get('profil_ailesi', '')}",
            f"Açıklama: {profile.get('description_expanded', '')}",
            "Birincil yetkinlikler:\n" + list_text(profile.get("birincil_yetkinlikler")),
            "Destekleyici profiller:\n" + list_text(profile.get("destekleyici_profiller")),
        ]
        if part.strip()
    )

    capacity = "\n".join(
        part
        for part in [
            "Ürünler ve hizmetler:\n" + list_text(profile.get("urunler_ve_hizmetler")),
            "Personel kapasitesi:\n" + object_list_text(profile.get("personel_kapasitesi")),
            "Belgeler:\n" + object_list_text(profile.get("belgeler")),
            "Tamamlanan projeler:\n" + object_list_text(profile.get("tamamlanan_projeler")),
            "İş deneyim belgeleri:\n" + object_list_text(profile.get("is_deneyim_belgeleri")),
            "Ekipman ve altyapı:\n" + object_list_text(profile.get("ekipman_ve_altyapi")),
            "Teknolojiler:\n" + list_text(profile.get("teknolojiler")),
            "İş ortakları:\n" + object_list_text(profile.get("is_ortaklari")),
            "Hizmet bölgeleri:\n" + list_text(profile.get("hizmet_bolgeleri")),
            "Kapasite sınırları:\n" + list_text(profile.get("kapasite_sinirlari")),
        ]
        if part.split(":\n", 1)[-1].strip()
    )

    tender_signals = "\n".join(
        part
        for part in [
            f"Profil: {profile.get('profil_kodu', '')} - {profile.get('profil_adi', '')}",
            "Güçlü ihale terimleri:\n" + list_text(signals.get("guclu_terimler")),
            "Destekleyici ihale terimleri:\n" + list_text(signals.get("destekleyici_terimler")),
            "Genel terimler:\n" + list_text(signals.get("genel_terimler")),
            "Negatif terimler:\n" + list_text(signals.get("negatif_terimler")),
            "OKAS kodları:\n" + list_text(signals.get("okas_kodlari")),
            "OKAS kod ön ekleri:\n" + list_text(signals.get("okas_kod_on_ekleri")),
            f"OKAS metin desteği zorunlu: {signals.get('okas_metin_destegi_zorunlu', False)}",
            "Eylem fiilleri:\n" + list_text(profile.get("action_verbs")),
        ]
        if part.strip()
    )

    terminology = "\n".join(
        part
        for part in [
            f"Profil: {profile.get('profil_kodu', '')} - {profile.get('profil_adi', '')}",
            "Kısaltmalar ve teknik terimler:\n"
            + object_list_text(profile.get("abbreviations_and_jargon")),
            "Teknik ekipman:\n" + object_list_text(profile.get("technical_equipment")),
            f"Bağlam kullanım amacı: {context_policy.get('usage', '')}",
            f"Şirket kanıtı mı: {context_policy.get('is_company_evidence', False)}",
            f"Daraltma notu: {profile.get('daraltma_notu', '')}",
        ]
        if part.strip()
    )

    return [
        ProfileChunk(section="identity_and_capabilities", text=identity.strip()),
        ProfileChunk(section="capacity_and_evidence", text=capacity.strip() or identity.strip()),
        ProfileChunk(section="tender_signals", text=tender_signals.strip()),
        ProfileChunk(section="terminology_and_context", text=terminology.strip()),
    ]


def load_profiles(path: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not path.is_dir():
        raise FileNotFoundError(f"Profil klasörü bulunamadı: {path}")

    loaded: list[tuple[Path, dict[str, Any]]] = []
    seen_codes: set[str] = set()

    for file_path in sorted(path.glob("*.json")):
        data = json.loads(file_path.read_text(encoding="utf-8"))
        profile_code = str(data.get("profil_kodu") or "").strip()
        profile_name = str(data.get("profil_adi") or "").strip()

        if not profile_code:
            raise ValueError(f"profil_kodu eksik: {file_path}")
        if not profile_name:
            raise ValueError(f"profil_adi eksik: {file_path}")
        if profile_code in seen_codes:
            raise ValueError(f"Tekrarlanan profil kodu: {profile_code}")

        seen_codes.add(profile_code)
        loaded.append((file_path, data))

    if not loaded:
        raise ValueError(f"Profil bulunamadı: {path}")

    return loaded


def get_state(profile_code: str) -> dict[str, Any] | None:
    query = f"""
        SELECT
            profile_code,
            source_hash,
            embedding_content_hash,
            embedding_model,
            embedding_version,
            chunking_version,
            vector_backend,
            vector_collection,
            index_version,
            index_status,
            chunk_count
        FROM {DEFAULT_STATE_TABLE}
        WHERE profile_code = %s
    """
    with get_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (profile_code,))
            return cursor.fetchone()


def upsert_pending(
    *,
    profile: dict[str, Any],
    source_path: Path,
    source_hash: str,
    embedding_content_hash: str,
    model_name: str,
    device: str,
    collection: str,
) -> None:
    signals = profile.get("ihale_kategori_sinyalleri") or {}
    context_policy = profile.get("context_policy") or {}

    query = f"""
        INSERT INTO {DEFAULT_STATE_TABLE} (
            profile_code,
            profile_name,
            profile_family,
            profile_version,
            profile_status,
            narrowing_note,
            description_expanded,
            data_status,
            last_profile_update,
            primary_capabilities,
            supporting_profiles,
            products_and_services,
            personnel_capacity,
            documents,
            completed_projects,
            work_experience_documents,
            equipment_and_infrastructure,
            technologies,
            business_partners,
            service_regions,
            capacity_limits,
            data_sources,
            strong_terms,
            supporting_terms,
            negative_terms,
            general_terms,
            okas_codes,
            okas_code_prefixes,
            okas_text_support_required,
            context_usage,
            is_company_evidence,
            abbreviations_and_jargon,
            technical_equipment,
            action_verbs,
            source_path,
            source_hash,
            metadata_hash,
            embedding_content_hash,
            raw_profile,
            embedding_model,
            embedding_version,
            embedding_device,
            chunking_version,
            vector_backend,
            vector_collection,
            index_version,
            chunk_count,
            index_status,
            is_active,
            processing_started_at,
            processing_completed_at,
            indexed_at,
            deleted_at,
            error_message,
            first_seen_at,
            last_seen_at,
            created_at,
            updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, 'faiss', %s, %s,
            0, 'pending', TRUE,
            NULL, NULL, NULL, NULL, NULL,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT (profile_code)
        DO UPDATE SET
            profile_name = EXCLUDED.profile_name,
            profile_family = EXCLUDED.profile_family,
            profile_version = EXCLUDED.profile_version,
            profile_status = EXCLUDED.profile_status,
            narrowing_note = EXCLUDED.narrowing_note,
            description_expanded = EXCLUDED.description_expanded,
            data_status = EXCLUDED.data_status,
            last_profile_update = EXCLUDED.last_profile_update,
            primary_capabilities = EXCLUDED.primary_capabilities,
            supporting_profiles = EXCLUDED.supporting_profiles,
            products_and_services = EXCLUDED.products_and_services,
            personnel_capacity = EXCLUDED.personnel_capacity,
            documents = EXCLUDED.documents,
            completed_projects = EXCLUDED.completed_projects,
            work_experience_documents = EXCLUDED.work_experience_documents,
            equipment_and_infrastructure = EXCLUDED.equipment_and_infrastructure,
            technologies = EXCLUDED.technologies,
            business_partners = EXCLUDED.business_partners,
            service_regions = EXCLUDED.service_regions,
            capacity_limits = EXCLUDED.capacity_limits,
            data_sources = EXCLUDED.data_sources,
            strong_terms = EXCLUDED.strong_terms,
            supporting_terms = EXCLUDED.supporting_terms,
            negative_terms = EXCLUDED.negative_terms,
            general_terms = EXCLUDED.general_terms,
            okas_codes = EXCLUDED.okas_codes,
            okas_code_prefixes = EXCLUDED.okas_code_prefixes,
            okas_text_support_required = EXCLUDED.okas_text_support_required,
            context_usage = EXCLUDED.context_usage,
            is_company_evidence = EXCLUDED.is_company_evidence,
            abbreviations_and_jargon = EXCLUDED.abbreviations_and_jargon,
            technical_equipment = EXCLUDED.technical_equipment,
            action_verbs = EXCLUDED.action_verbs,
            source_path = EXCLUDED.source_path,
            source_hash = EXCLUDED.source_hash,
            metadata_hash = EXCLUDED.metadata_hash,
            embedding_content_hash = EXCLUDED.embedding_content_hash,
            raw_profile = EXCLUDED.raw_profile,
            embedding_model = EXCLUDED.embedding_model,
            embedding_version = EXCLUDED.embedding_version,
            embedding_device = EXCLUDED.embedding_device,
            chunking_version = EXCLUDED.chunking_version,
            vector_backend = EXCLUDED.vector_backend,
            vector_collection = EXCLUDED.vector_collection,
            index_version = EXCLUDED.index_version,
            chunk_count = 0,
            index_status = 'pending',
            is_active = TRUE,
            processing_started_at = NULL,
            processing_completed_at = NULL,
            indexed_at = NULL,
            deleted_at = NULL,
            error_message = NULL,
            last_seen_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
    """

    metadata_for_hash = {
        "profil_kodu": profile.get("profil_kodu"),
        "profil_adi": profile.get("profil_adi"),
        "profil_ailesi": profile.get("profil_ailesi"),
        "profil_surumu": profile.get("profil_surumu"),
        "durum": profile.get("durum"),
    }
    metadata_hash = sha256_text(canonical_json(metadata_for_hash))

    values = (
        profile.get("profil_kodu"),
        profile.get("profil_adi"),
        profile.get("profil_ailesi") or "",
        profile.get("profil_surumu"),
        profile.get("durum"),
        profile.get("daraltma_notu"),
        profile.get("description_expanded"),
        profile.get("veri_durumu"),
        profile.get("son_guncelleme"),
        Jsonb(profile.get("birincil_yetkinlikler") or []),
        Jsonb(profile.get("destekleyici_profiller") or []),
        Jsonb(profile.get("urunler_ve_hizmetler") or []),
        Jsonb(profile.get("personel_kapasitesi") or []),
        Jsonb(profile.get("belgeler") or []),
        Jsonb(profile.get("tamamlanan_projeler") or []),
        Jsonb(profile.get("is_deneyim_belgeleri") or []),
        Jsonb(profile.get("ekipman_ve_altyapi") or []),
        Jsonb(profile.get("teknolojiler") or []),
        Jsonb(profile.get("is_ortaklari") or []),
        Jsonb(profile.get("hizmet_bolgeleri") or []),
        Jsonb(profile.get("kapasite_sinirlari") or []),
        Jsonb(profile.get("veri_kaynaklari") or []),
        Jsonb(signals.get("guclu_terimler") or []),
        Jsonb(signals.get("destekleyici_terimler") or []),
        Jsonb(signals.get("negatif_terimler") or []),
        Jsonb(signals.get("genel_terimler") or []),
        Jsonb(signals.get("okas_kodlari") or []),
        Jsonb(signals.get("okas_kod_on_ekleri") or []),
        bool(signals.get("okas_metin_destegi_zorunlu", False)),
        context_policy.get("usage"),
        bool(context_policy.get("is_company_evidence", False)),
        Jsonb(profile.get("abbreviations_and_jargon") or []),
        Jsonb(profile.get("technical_equipment") or []),
        Jsonb(profile.get("action_verbs") or []),
        str(source_path.as_posix()),
        source_hash,
        metadata_hash,
        embedding_content_hash,
        Jsonb(profile),
        model_name,
        DEFAULT_EMBEDDING_VERSION,
        device,
        DEFAULT_CHUNKING_VERSION,
        collection,
        DEFAULT_INDEX_VERSION,
    )

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, values)
        connection.commit()


def mark_processing(profile_code: str) -> None:
    query = f"""
        UPDATE {DEFAULT_STATE_TABLE}
        SET
            index_status = 'processing',
            processing_started_at = CURRENT_TIMESTAMP,
            last_seen_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE profile_code = %s
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (profile_code,))
        connection.commit()


def mark_indexed(profile_code: str, chunk_count: int) -> None:
    query = f"""
        UPDATE {DEFAULT_STATE_TABLE}
        SET
            index_status = 'indexed',
            chunk_count = %s,
            indexed_at = CURRENT_TIMESTAMP,
            processing_completed_at = CURRENT_TIMESTAMP,
            last_seen_at = CURRENT_TIMESTAMP,
            error_message = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE profile_code = %s
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (chunk_count, profile_code))
        connection.commit()


def mark_failed(profile_code: str, error_message: str) -> None:
    query = f"""
        UPDATE {DEFAULT_STATE_TABLE}
        SET
            index_status = 'failed',
            processing_completed_at = CURRENT_TIMESTAMP,
            error_message = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE profile_code = %s
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (error_message[:4000], profile_code))
        connection.commit()


def mark_missing_profiles_inactive(active_codes: list[str]) -> None:
    query = f"""
        UPDATE {DEFAULT_STATE_TABLE}
        SET
            is_active = FALSE,
            index_status = 'deleted',
            deleted_at = CURRENT_TIMESTAMP,
            last_seen_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE is_active = TRUE
          AND NOT (profile_code = ANY(%s))
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (active_codes,))
        connection.commit()


def should_skip(
    state: dict[str, Any] | None,
    *,
    recreate: bool,
    source_hash: str,
    embedding_content_hash: str,
    model_name: str,
    collection: str,
) -> bool:
    if recreate or state is None:
        return False

    return all(
        [
            state.get("index_status") == "indexed",
            state.get("source_hash") == source_hash,
            state.get("embedding_content_hash") == embedding_content_hash,
            state.get("embedding_model") == model_name,
            state.get("embedding_version") == DEFAULT_EMBEDDING_VERSION,
            state.get("chunking_version") == DEFAULT_CHUNKING_VERSION,
            state.get("vector_backend") == "faiss",
            state.get("vector_collection") == collection,
            state.get("index_version") == DEFAULT_INDEX_VERSION,
            int(state.get("chunk_count") or 0) == 4,
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="İSBAK profillerini BGE-M3 ile FAISS'e indeksle"
    )
    parser.add_argument(
        "--profiles-path",
        default=str(DEFAULT_PROFILES_PATH),
        help="Profil JSON dosyalarının bulunduğu klasör",
    )
    parser.add_argument(
        "--faiss-path",
        default=str(DEFAULT_FAISS_PATH),
        help="Profil FAISS dosyalarının yazılacağı klasör",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help="Profil FAISS koleksiyon adı",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="İşlenecek maksimum profil sayısı",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="BGE-M3 gömme toplu işlem boyutu",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Profil FAISS indeksini sıfırdan oluştur ve tüm profilleri yeniden işle",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Veritabanına ve FAISS'e yazmadan profil biçimini doğrula",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = time.perf_counter()
    stats = BuildStats()

    profiles_path = Path(args.profiles_path)
    faiss_path = Path(args.faiss_path)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        settings = get_settings()
        profiles = load_profiles(profiles_path)
        stats.discovered = len(profiles)

        if args.limit is not None:
            if args.limit <= 0:
                raise ValueError("--limit pozitif olmalıdır.")
            profiles = profiles[: args.limit]

        stats.selected = len(profiles)
        active_codes = [str(profile["profil_kodu"]) for _, profile in profiles]

        prepared: list[
            tuple[Path, dict[str, Any], list[ProfileChunk], str, str, dict[str, Any] | None]
        ] = []

        for source_path, profile in profiles:
            chunks = build_profile_chunks(profile)
            source_hash = sha256_text(canonical_json(profile))
            embedding_content_hash = sha256_text(
                canonical_json(
                    [{"section": chunk.section, "text": chunk.text} for chunk in chunks]
                )
            )
            state = None if args.dry_run else get_state(str(profile["profil_kodu"]))
            prepared.append(
                (
                    source_path,
                    profile,
                    chunks,
                    source_hash,
                    embedding_content_hash,
                    state,
                )
            )

        if args.dry_run:
            for source_path, profile, chunks, source_hash, content_hash, _ in prepared:
                print(
                    f"[KONTROL] {profile['profil_kodu']} | "
                    f"parça={len(chunks)} | dosya={source_path.name} | "
                    f"source_hash={source_hash[:12]} | content_hash={content_hash[:12]}"
                )
            stats.chunk_count = sum(len(item[2]) for item in prepared)
            stats.elapsed_seconds = time.perf_counter() - started_at
            report = {
                "status": "dry_run_completed",
                **stats.__dict__,
                "timestamp": utc_now().isoformat(),
            }
            REPORT_PATH.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(
                f"[TAMAMLANDI] Dry-run | Profil: {stats.selected} | "
                f"Parça: {stats.chunk_count}"
            )
            return 0

        embedder = BgeM3Embedder(
            model_name=settings.embedding_model,
            device=settings.embedding_device,
            batch_size=args.batch_size,
            show_progress_bar=True,
        )
        vector_store = FaissVectorStore(
            path=faiss_path,
            collection_name=args.collection,
        )
        vector_store.ensure_collection(
            vector_size=embedder.vector_size,
            recreate=args.recreate,
        )

        for source_path, profile, chunks, source_hash, content_hash, state in prepared:
            profile_code = str(profile["profil_kodu"])

            if should_skip(
                state,
                recreate=args.recreate,
                source_hash=source_hash,
                embedding_content_hash=content_hash,
                model_name=settings.embedding_model,
                collection=args.collection,
            ):
                stats.skipped += 1
                print(f"[ATLANDI] {profile_code} | değişiklik yok")
                continue

            try:
                upsert_pending(
                    profile=profile,
                    source_path=source_path,
                    source_hash=source_hash,
                    embedding_content_hash=content_hash,
                    model_name=settings.embedding_model,
                    device=settings.embedding_device,
                    collection=args.collection,
                )
                mark_processing(profile_code)

                texts = [chunk.text for chunk in chunks]
                vectors = embedder.embed(texts)

                records: list[VectorRecord] = []
                for order, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                    point_id = deterministic_point_id(
                        "isbak-profile",
                        profile_code,
                        chunk.section,
                    )
                    payload = {
                        "document_type": "company_profile",
                        "profile_code": profile_code,
                        "profile_name": profile.get("profil_adi"),
                        "profile_family": profile.get("profil_ailesi"),
                        "profile_version": profile.get("profil_surumu"),
                        "section": chunk.section,
                        "section_order": order,
                        "text": chunk.text,
                        "source_path": str(source_path.as_posix()),
                        "source_hash": source_hash,
                        "embedding_content_hash": content_hash,
                        "embedding_model": settings.embedding_model,
                        "embedding_device": settings.embedding_device,
                        "chunking_version": DEFAULT_CHUNKING_VERSION,
                        "vector_collection": args.collection,
                    }
                    records.append(
                        VectorRecord(
                            point_id=point_id,
                            vector=vector,
                            payload=payload,
                        )
                    )

                written = vector_store.upsert_records(
                    records,
                    batch_size=max(1, args.batch_size),
                )
                mark_indexed(profile_code, written)

                stats.processed += 1
                stats.chunk_count += written
                print(
                    f"[İŞLENDİ] {profile_code} | "
                    f"Parça: {written} | FAISS toplam: {vector_store.count()}"
                )
            except Exception as exc:
                stats.failed += 1
                mark_failed(profile_code, str(exc))
                print(f"[HATA] {profile_code}: {exc}", file=sys.stderr)

        if args.limit is None:
            mark_missing_profiles_inactive(active_codes)

        vector_store.close()
        stats.faiss_total = vector_store.count()
        stats.elapsed_seconds = time.perf_counter() - started_at

        status = "completed" if stats.failed == 0 else "completed_with_errors"
        report = {
            "status": status,
            "profiles_path": str(profiles_path),
            "faiss_path": str(faiss_path),
            "collection": args.collection,
            "embedding_model": settings.embedding_model,
            "embedding_device": settings.embedding_device,
            "chunking_version": DEFAULT_CHUNKING_VERSION,
            **stats.__dict__,
            "timestamp": utc_now().isoformat(),
        }
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(
            f"[TAMAMLANDI] Profil: {stats.processed} | "
            f"Atlandı: {stats.skipped} | Hata: {stats.failed} | "
            f"Parça: {stats.chunk_count} | FAISS toplam: {stats.faiss_total}"
        )
        return 0 if stats.failed == 0 else 1

    except Exception as exc:
        report = {
            "status": "failed",
            "error": str(exc),
            "timestamp": utc_now().isoformat(),
        }
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"HATA: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
