#!/usr/bin/env python
"""Hazır profil ve ihale dizinleri üzerinde tek modelli karar zinciri.

Akış:
  Profil FAISS vektörleri
  → ihale FAISS aday araması
  → isteğe bağlı PostgreSQL kaynak yenilemesi
  → ihale bazında kanıt seçimi
  → Qwen karar
  → Python doğrulama
  → JSONL/CSV raporları

Örnek:
  PYTHONPATH=. python scripts/run_tender_decision_chain.py \
    --profile-code AUS-01 \
    --limit-per-profile 5
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
from app.config import get_settings
from app.config.isbak_rag_settings import get_isbak_rag_settings
from app.database.tender_repository import TenderRepository
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import DecisionValidationContext
from app.decision.ollama_decision_model import OllamaDecisionModel
from app.decision.tender_batch import (
    ProfileCandidateMatch,
    UniqueTenderCandidate,
    group_unique_tenders,
    tender_identity,
)
from app.decision.tender_source_context import (
    TenderSourceContextBuilder,
    render_database_tender_context,
)
from app.decision.validator import IsbakDeterministicValidator
from app.domain import TenderRecord
from app.reporting.decision_reporter import DecisionReporter
from app.retrieval.profile_vector_tender_matcher import (
    ProfileVectorTenderMatcher,
)
from app.vector_store.faiss_store import FaissVectorStore

LOGGER = logging.getLogger("run_tender_decision_chain")
DEFAULT_TENDER_FAISS_PATH = Path("storage/faiss")
DEFAULT_TENDER_COLLECTION = "ekap_tender_chunks"
DEFAULT_PROFILE_FAISS_PATH = Path("storage/faiss_profiles")
DEFAULT_PROFILE_COLLECTION = "isbak_company_profiles"
DEFAULT_REPORT_DIR = Path("reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profil FAISS vektörlerinden aday ihaleleri getirip "
            "Qwen + Python doğrulama karar zincirini çalıştırır."
        )
    )
    parser.add_argument(
        "--profile-code",
        action="append",
        default=[],
        help=(
            "Çalıştırılacak profil kodu. Birden fazla kez verilebilir. "
            "Verilmezse tüm aktif profiller kullanılır."
        ),
    )
    parser.add_argument(
        "--limit-per-profile",
        type=int,
        default=5,
        help="Her profil için modele gönderilecek en fazla aday ihale.",
    )
    parser.add_argument(
        "--max-decisions",
        type=int,
        default=5,
        help="Tüm çalışma için üretilecek azami karar sayısı.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help=(
            "Benzersiz aday ihaleleri verilen tohumla karıştırır. Aynı tohum "
            "aynı aday havuzunda aynı test grubunu üretir."
        ),
    )
    parser.add_argument(
        "--tender-faiss-path",
        default=str(DEFAULT_TENDER_FAISS_PATH),
        help="İhale FAISS klasörü.",
    )
    parser.add_argument(
        "--tender-collection",
        default=DEFAULT_TENDER_COLLECTION,
        help="İhale FAISS koleksiyon adı.",
    )
    parser.add_argument(
        "--profile-faiss-path",
        default=str(DEFAULT_PROFILE_FAISS_PATH),
        help="Profil FAISS klasörü.",
    )
    parser.add_argument(
        "--profile-collection",
        default=DEFAULT_PROFILE_COLLECTION,
        help="Profil FAISS koleksiyon adı.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="Karar raporlarının yazılacağı klasör.",
    )
    parser.add_argument(
        "--skip-secondary",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Yalnızca adayları getir; LLM karar zincirini çalıştırma.",
    )
    parser.add_argument(
        "--source-mode",
        choices=("faiss", "database"),
        default="faiss",
        help=(
            "Model karar bağlamının kaynağı. database seçildiğinde aday FAISS'ten "
            "bulunur, fakat gerçek ihale alanları model çağrısından önce PostgreSQL'den "
            "yeniden okunur."
        ),
    )
    parser.add_argument(
        "--selection-mode",
        choices=("candidate-pool", "database-random", "database-sequential", "faiss-distant"),
        default="candidate-pool",
        help=(
            "İhale seçim modu. candidate-pool mevcut FAISS tabanlı aday havuzunu kullanır. "
            "database-random ise PostgreSQL'den rastgele aktif ihale seçerek doğrudan mevcut profillerle değerlendirir. "
            "database-sequential: PostgreSQL'deki aktif ihaleleri en yeni kayıttan başlayarak deterministik biçimde sırayla değerlendirir.\n"
            "faiss-distant: FAISS içindeki ihaleler arasından tüm aktif profillere olan en iyi benzerliği düşük olan ihaleleri seçerek negatif/stres testi yapar."
        ),
    )
    parser.add_argument(
        "--distant-pool-size",
        type=int,
        default=500,
        help="faiss-distant modunda taranacak örneklem büyüklüğü.",
    )
    parser.add_argument(
        "--allow-inactive-database-records",
        action="store_true",
        help=(
            "Yalnız geçmişe dönük karşılaştırma testlerinde PostgreSQL kaydının "
            "aktif durum denetimini atlar. Canlı çalışmada kullanılmamalıdır."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Kayıt seviyesi.",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def validate_args(args: argparse.Namespace) -> None:
    if args.limit_per_profile <= 0:
        raise ValueError("--limit-per-profile pozitif olmalıdır.")
    if args.max_decisions is not None and args.max_decisions <= 0:
        raise ValueError("--max-decisions pozitif olmalıdır.")


def validate_faiss_store(store: FaissVectorStore, label: str) -> None:
    if not store.collection_exists():
        raise FileNotFoundError(
            f"{label} FAISS indeksi bulunamadı: {store.index_file}"
        )
    if store.index is None:
        raise RuntimeError(f"{label} FAISS indeksi belleğe yüklenemedi.")
    if store.index.ntotal <= 0:
        raise RuntimeError(f"{label} FAISS indeksi boş.")
    if store.index.ntotal != len(store.payloads):
        raise RuntimeError(
            f"{label} FAISS/payload uyumsuzluğu: "
            f"vektör={store.index.ntotal}, payload={len(store.payloads)}"
        )


def validate_ollama(
    *,
    base_url: str,
    required_models: list[str],
) -> None:
    try:
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            response = client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Ollama servisine ulaşılamadı: {base_url}"
        ) from exc

    installed = {
        str(item.get("name") or "")
        for item in response.json().get("models", [])
    }
    missing = [model for model in required_models if model not in installed]
    if missing:
        raise RuntimeError(
            "Ollama üzerinde gerekli model bulunamadı: "
            + ", ".join(missing)
        )

def _get_vectors_for_faiss_ids(index: Any, faiss_ids: list[int]) -> list[Any]:
    """
    Güvenli FAISS vektör çekme fonksiyonu.
    IndexIDMap tipi destekleniyorsa internal mapping kurarak reconstruct çağırır.
    Gerçek hataları (IndexError vb.) dışarı fırlatır, yutmaz.
    """
    import faiss

    if not faiss_ids:
        return []

    # Eğer index direkt olarak reconstruct destekliyorsa
    if not isinstance(index, faiss.IndexIDMap) and not isinstance(index, faiss.IndexIDMap2):
        vectors = []
        for pid in faiss_ids:
            vectors.append(index.reconstruct(pid))
        return vectors

    # IndexIDMap söz konusuysa, external -> internal mapping kur
    try:
        id_array = faiss.vector_to_array(index.id_map)
    except Exception as e:
        raise RuntimeError(f"IndexIDMap üzerinden id_map çıkarılamadı: {e}") from e

    # External ID -> Internal Row numarası (ilk eşleşeni alır)
    ext_to_int = {ext_id: int_id for int_id, ext_id in enumerate(id_array)}

    vectors = []
    for pid in faiss_ids:
        int_id = ext_to_int.get(pid)
        if int_id is None:
            raise KeyError(f"External FAISS ID {pid} id_map içinde bulunamadı.")


        # Sub-index üzerinden asıl vektörü çek
        try:
            vec = index.index.reconstruct(int_id)
            vectors.append(vec)
        except Exception as e:
            raise RuntimeError(f"Internal ID {int_id} için vektör çekilemedi: {e}") from e

    return vectors

def selected_profiles(
    loader: IsbakProfileLoader,
    requested_codes: list[str],
) -> list[str]:
    active_entries = loader.list_profiles(active_only=True)
    active_codes = [
        str(entry.get("profil_kodu") or "").strip().upper()
        for entry in active_entries
        if str(entry.get("profil_kodu") or "").strip()
    ]

    if not requested_codes:
        return active_codes

    requested = list(
        dict.fromkeys(
            str(code).strip().upper()
            for code in requested_codes
            if str(code).strip()
        )
    )
    unknown = [code for code in requested if code not in active_codes]
    if unknown:
        raise ValueError(
            "Aktif profil listesinde bulunmayan kodlar: "
            + ", ".join(unknown)
        )
    return requested



def compact_text(value: Any, max_chars: int) -> str:
    """Metni tek satırlı ve sınırlı uzunlukta döndürür."""
    if value is None:
        return ""

    text = " ".join(str(value).split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def remove_empty_values(value: Any) -> Any:
    """İç içe yapılardaki boş değerleri kaldırır."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            normalized = remove_empty_values(item)
            if normalized not in (None, "", [], {}):
                cleaned[str(key)] = normalized
        return cleaned

    if isinstance(value, list):
        cleaned_items = [remove_empty_values(item) for item in value]
        return [
            item
            for item in cleaned_items
            if item not in (None, "", [], {})
        ]

    return value


def render_tender_context(
    matcher: ProfileVectorTenderMatcher,
    candidate: Any,
) -> str:
    del matcher
    settings = get_settings()
    parts = [
        f"İhale Adı: {compact_text(candidate.tender_name, 500)}",
        f"İdare: {compact_text(candidate.idare_adi, 400)}",
        f"OKAS Kodları: {compact_text(', '.join(candidate.okas_codes), 700)}",
    ]
    total_chars = sum(len(part) for part in parts)

    for chunk in candidate.evidence_chunks:
        if not chunk.chunk_id or not chunk.text.strip():
            continue
        header = (
            f"[KAYNAK | chunk_id: {chunk.chunk_id} | "
            f"bölüm: {chunk.section_id or chunk.chunk_title}]"
        )
        remaining = settings.max_tender_context_chars - total_chars - len(header) - 4
        if remaining <= 0:
            break
        text = compact_text(chunk.text, remaining)
        entry = f"{header}\n{text}"
        parts.append(entry)
        total_chars += len(entry) + 2

    return "\n\n".join(parts)[: settings.max_tender_context_chars]


def render_company_context(
    loader: IsbakProfileLoader,
    profile_code: str,
    supporting_profile_codes: list[str] | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    settings = get_settings()
    evaluation_context = loader.build_evaluation_context(
        primary_code=profile_code,
        secondary_codes=supporting_profile_codes or [],
        include_supporting_profiles=True,
        recursive_supporting_profiles=False,
        include_supporting_profile_documents=False,
    )

    primary_code = profile_code.strip().upper()
    loaded_codes = [
        str(code).strip().upper()
        for code in evaluation_context.get(
            "destekleyici_profil_kodlari",
            [],
        )
        if str(code).strip()
    ]
    secondary_codes = [
        code
        for code in loaded_codes
        if code != primary_code
    ]

    compact_context_json = remove_empty_values(evaluation_context)
    legacy_raw_company_context_str = json.dumps(
        compact_context_json,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    legacy_company_context_str = compact_text(
        legacy_raw_company_context_str,
        settings.max_company_context_chars,
    )

    from app.pipeline.isbak_tender_analysis_service import format_company_context_compact
    compact_company_context_str, compact_stats = format_company_context_compact(evaluation_context)

    if settings.qwen_compact_company_context:
        company_context = compact_company_context_str
        selected_mode = "compact"
        fallback_reason = ""

        # Integrity Guard
        birincil_profil = evaluation_context.get("birincil_profil", {})

        def check_field(field_value: Any, field_name: str) -> bool:
            nonlocal selected_mode, fallback_reason, company_context
            if not field_value:
                return True

            if isinstance(field_value, str):
                if field_value not in company_context:
                    selected_mode = "legacy_fallback"
                    fallback_reason = f"missing_{field_name}"
                    company_context = legacy_company_context_str
                    return False
                return True

            if isinstance(field_value, list):
                seen = set()
                unique_items = []
                for item in field_value:
                    item_str = str(item)
                    if item_str not in seen:
                        seen.add(item_str)
                        unique_items.append(item_str)

                for item in unique_items:
                    if str(item) not in company_context:
                        selected_mode = "legacy_fallback"
                        fallback_reason = f"missing_{field_name}"
                        company_context = legacy_company_context_str
                        return False
                return True

            if isinstance(field_value, dict):
                for k, v in field_value.items():
                    if v and str(v) not in company_context:
                        selected_mode = "legacy_fallback"
                        fallback_reason = f"missing_{field_name}"
                        company_context = legacy_company_context_str
                        return False
                return True

            return True

        if selected_mode == "compact":
            check_field(birincil_profil.get("profil_adi"), "profile_name")
            check_field(birincil_profil.get("birincil_yetkinlikler"), "primary_capabilities")
            check_field(birincil_profil.get("description_expanded"), "description_expanded")
            check_field(birincil_profil.get("technical_equipment"), "technical_equipment")
            check_field(birincil_profil.get("abbreviations_and_jargon"), "abbreviations_and_jargon")
            check_field(birincil_profil.get("action_verbs"), "action_verbs")
            check_field(birincil_profil.get("urunler_ve_hizmetler"), "urunler_ve_hizmetler")
            check_field(birincil_profil.get("teknolojiler"), "teknolojiler")
            check_field(birincil_profil.get("kapasite_sinirlari"), "kapasite_sinirlari")

        signals = birincil_profil.get("ihale_kategori_sinyalleri", {})
        if isinstance(signals, dict) and selected_mode == "compact":
            check_field(signals.get("guclu_terimler"), "strong_terms")
            check_field(signals.get("destekleyici_terimler"), "destekleyici_terimler")
            check_field(signals.get("negatif_terimler"), "negative_terms")
            check_field(signals.get("okas_kodlari"), "okas_kodlari")
            check_field(signals.get("okas_kod_on_ekleri"), "okas_kod_on_ekleri")

        log_msg = (
            f"[COMPANY_CONTEXT_MODE] "
            f"setting_value={settings.qwen_compact_company_context} "
            f"selected_mode={selected_mode} "
            f"legacy_raw_chars={len(legacy_raw_company_context_str)} "
            f"legacy_final_chars={len(legacy_company_context_str)} "
            f"compact_final_chars={len(compact_company_context_str)} "
            f"selected_chars={len(company_context)} "
            f"compact_integrity_passed={selected_mode == 'compact'}"
        )
        if fallback_reason:
            log_msg += f" fallback_reason={fallback_reason}"
        LOGGER.info(log_msg)
    else:
        company_context = legacy_company_context_str
        selected_mode = "legacy"
        LOGGER.info(
            f"[COMPANY_CONTEXT_MODE] "
            f"setting_value={settings.qwen_compact_company_context} "
            f"selected_mode={selected_mode} "
            f"legacy_raw_chars={len(legacy_raw_company_context_str)} "
            f"legacy_final_chars={len(legacy_company_context_str)} "
            f"compact_final_chars={len(compact_company_context_str)} "
            f"selected_chars={len(company_context)} "
            f"compact_integrity_passed=False"
        )

    return (
        company_context,
        evaluation_context.get("degerlendirme_kurallari", {}),
        secondary_codes,
    )


def write_retrieval_report(
    *,
    report_dir: Path,
    rows: list[dict[str, Any]],
    filename: str = "profile_tender_candidates.jsonl",
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / filename
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def normalize_ikn(value: Any) -> str:
    """İKN değerini kaynaklar arası eşleştirme için tek biçime getirir."""

    return "".join(str(value or "").upper().split())


def prepare_database_sources(
    *,
    repository: TenderRepository,
    candidates: list[UniqueTenderCandidate],
    report_dir: Path,
    active_status_values: list[str],
    allow_inactive: bool,
) -> tuple[dict[str, TenderRecord], Path, list[dict[str, str]]]:
    """Karar adaylarını toplu okur ve güvenli kaynak ön kontrolü üretir."""

    schema = repository.validate_required_schema()
    requested_ikns = list(
        dict.fromkeys(
            str(item.candidate.ikn).strip()
            for item in candidates
            if str(item.candidate.ikn).strip()
        )
    )
    records = repository.get_by_ikns(requested_ikns)
    records_by_ikn = {
        normalize_ikn(record.ikn): record
        for record in records
        if normalize_ikn(record.ikn)
    }
    normalized_active_statuses = {
        " ".join(str(value).split()).casefold()
        for value in active_status_values
        if str(value).strip()
    }

    eligible: dict[str, TenderRecord] = {}
    missing_ikns: list[str] = []
    inactive_ikns: list[str] = []
    failures: list[dict[str, str]] = []
    for candidate in candidates:
        ikn = str(candidate.candidate.ikn or "").strip()
        key = normalize_ikn(ikn)
        record = records_by_ikn.get(key)
        if record is None:
            missing_ikns.append(ikn or candidate.tender_key)
            failures.append(
                {
                    "profile_code": candidate.primary_profile_code,
                    "ikn": ikn,
                    "stage": "database_source",
                    "error": "Aday ihale PostgreSQL kaynak tablolarında bulunamadı.",
                }
            )
            continue

        status = " ".join(str(record.ihale_durumu or "").split()).casefold()
        if (
            not allow_inactive
            and normalized_active_statuses
            and status not in normalized_active_statuses
        ):
            inactive_ikns.append(ikn)
            failures.append(
                {
                    "profile_code": candidate.primary_profile_code,
                    "ikn": ikn,
                    "stage": "database_active_status",
                    "error": (
                        "PostgreSQL kaydının ihale durumu aktif durum listesinde "
                        f"değil: {record.ihale_durumu or 'boş'}"
                    ),
                }
            )
            continue
        eligible[key] = record

    schema_tables = dict(schema.get("tables") or {})
    preflight = {
        "timestamp": datetime.now(UTC).isoformat(),
        "source_mode": "database",
        "schema": str(schema.get("schema") or "public"),
        "validated_tables": sorted(schema_tables),
        "requested_tenders": len(candidates),
        "requested_ikns": len(requested_ikns),
        "loaded_tenders": len(records),
        "eligible_tenders": len(eligible),
        "missing_ikns": missing_ikns,
        "inactive_ikns": inactive_ikns,
        "inactive_records_allowed": allow_inactive,
        "credentials_written_to_report": False,
    }
    preflight_path = report_dir / "database_source_preflight.json"
    preflight_path.write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return eligible, preflight_path, failures


def release_ollama_model(*, base_url: str, model_name: str) -> None:
    """Toplu kararlar tamamlandığında Qwen modelini Ollama belleğinden çıkarır."""

    try:
        with httpx.Client(timeout=15.0, trust_env=False) as client:
            response = client.post(
                f"{base_url.rstrip('/')}/api/generate",
                json={"model": model_name, "keep_alive": 0},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        LOGGER.warning("Qwen modeli bellekten çıkarılamadı: %s", exc)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    validate_args(args)

    started = time.perf_counter()
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    if settings.automatic_positive_decisions_enabled:
        raise RuntimeError(
            "AUTOMATIC_POSITIVE_DECISIONS_ENABLED güvenlik nedeniyle true "
            "olamaz; uygun kararlarında insan onayı zorunludur."
        )
    rag_settings = get_isbak_rag_settings()
    loader = IsbakProfileLoader()

    tender_store = FaissVectorStore(
        path=args.tender_faiss_path,
        collection_name=args.tender_collection,
    )
    profile_store = FaissVectorStore(
        path=args.profile_faiss_path,
        collection_name=args.profile_collection,
    )
    validate_faiss_store(tender_store, "İhale")
    validate_faiss_store(profile_store, "Profil")

    matcher = ProfileVectorTenderMatcher(
        tender_vector_store=tender_store,
        profile_vector_store=profile_store,
        settings=rag_settings,
    )

    profile_codes = selected_profiles(loader, args.profile_code)
    LOGGER.info(
        "Başlatılıyor | profil=%s | ihale_vektörü=%s | profil_vektörü=%s",
        len(profile_codes),
        tender_store.count(),
        profile_store.count(),
    )

    primary_model = None
    if not args.retrieval_only:
        validate_ollama(
            base_url=settings.ollama_base_url,
            required_models=[settings.qwen_model],
        )
        primary_model = OllamaDecisionModel(
            name=settings.qwen_model,
            host=settings.ollama_base_url,
            prompt_version="isbak_qwen_decision_v4_compact",
        )

        pipeline = IsbakDecisionPipeline(
            primary_model=primary_model,
            validator=IsbakDeterministicValidator(),
        )
    else:
        pipeline = None

    decisions = []
    retrieval_rows: list[dict[str, Any]] = []
    candidate_matches: list[ProfileCandidateMatch] = []
    evaluated_pairs: set[tuple[str, str]] = set()
    failures: list[dict[str, str]] = []

    unique_candidates: list[UniqueTenderCandidate] = []
    unique_rows: list[dict[str, Any]] = []
    database_random_selection_report: list[dict[str, Any]] = []
    faiss_distant_selection_report: list[dict[str, Any]] = []
    active_tender_pool_size = 0
    random_tenders_examined = 0
    random_tenders_missing_faiss = 0
    random_tenders_with_semantic_evidence = 0
    random_tenders_submitted_to_model = 0
    distant_tenders_examined = 0
    distant_tenders_selected = 0
    distant_best_scores: list[float] = []

    if args.selection_mode == "candidate-pool":
        for profile_code in profile_codes:
            LOGGER.info("Profil değerlendiriliyor: %s", profile_code)

            try:
                candidates = matcher.retrieve_profile(
                    profile_code=profile_code,
                    limit=args.limit_per_profile,
                )
            except Exception as exc:
                LOGGER.exception("%s aday getirme hatası", profile_code)
                failures.append(
                    {
                        "profile_code": profile_code,
                        "stage": "retrieval",
                        "error": str(exc),
                    }
                )
                continue

            LOGGER.info("%s için %s aday bulundu.", profile_code, len(candidates))

            for rank, candidate in enumerate(candidates, start=1):
                pair = (profile_code, tender_identity(candidate))
                if pair in evaluated_pairs:
                    continue
                evaluated_pairs.add(pair)
                candidate_matches.append(
                    ProfileCandidateMatch(
                        profile_code=profile_code,
                        retrieval_rank=rank,
                        candidate=candidate,
                    )
                )

                retrieval_rows.append(
                    {
                        "profile_code": profile_code,
                        "rank": rank,
                        "tender_id": candidate.tender_id,
                        "ikn": candidate.ikn,
                        "tender_name": candidate.tender_name,
                        "authority_name": candidate.idare_adi,
                        "score": candidate.scores.final,
                        "score_breakdown": candidate.scores.__dict__,
                        "evidence_chunk_ids": [
                            chunk.chunk_id
                            for chunk in candidate.evidence_chunks
                        ],
                    }
                )

        unique_candidates = group_unique_tenders(
            candidate_matches,
            max_evidence_chunks=max(
                1,
                int(rag_settings.faiss_max_chunks_per_tender),
            ),
        )
        if args.random_seed is not None:
            random.Random(args.random_seed).shuffle(unique_candidates)
        unique_rows = [
            {
                "tender_key": item.tender_key,
                "tender_id": item.candidate.tender_id,
                "ikn": item.candidate.ikn,
                "tender_name": item.candidate.tender_name,
                "authority_name": item.candidate.idare_adi,
                "primary_profile_code": item.primary_profile_code,
                "supporting_profile_codes": item.supporting_profile_codes,
                "profile_match_scores": item.profile_match_scores,
                "evidence_chunk_ids": [
                    chunk.chunk_id
                    for chunk in item.candidate.evidence_chunks
                    if chunk.chunk_id
                ],
            }
            for item in unique_candidates
        ]

        LOGGER.info(
            "Adaylar tekilleştirildi | profil-ihale=%s | benzersiz_ihale=%s",
            len(candidate_matches),
            len(unique_candidates),
        )
    elif args.selection_mode in ("database-random", "database-sequential"):
        # database-random / database-sequential mode
        from app.indexing.active_tender_indexer import is_isbak_tender
        repository = TenderRepository()
        active_tenders = repository.get_active_tenders()
        active_tender_pool_size = len(active_tenders)

        is_sequential = args.selection_mode == "database-sequential"

        if is_sequential:
            # Deterministik: created_at DESC, id DESC (newest-first)
            # created_at None olanlar sona atar (NULLS LAST davranışı — datetime.min sentinel)
            from datetime import datetime as _datetime
            _dt_min = _datetime.min
            active_tenders.sort(
                key=lambda t: (
                    t.created_at.replace(tzinfo=None) if t.created_at else _dt_min,
                    str(t.id),
                ),
                reverse=True,
            )
            LOGGER.info(
                "[SELECTION_MODE] mode=database-sequential source=PostgreSQL "
                "requested=%s active_tender_pool=%s order=newest-first",
                args.max_decisions,
                active_tender_pool_size,
            )
        else:
            random.Random(args.random_seed).shuffle(active_tenders)
            LOGGER.info(
                "[SELECTION_MODE] mode=database-random source=PostgreSQL "
                "requested=%s active_tender_pool=%s random_seed=%s",
                args.max_decisions,
                active_tender_pool_size,
                args.random_seed,
            )

        random_tenders_examined = 0
        random_tenders_missing_faiss = 0
        random_tenders_with_semantic_evidence = 0
        random_tenders_submitted_to_model = 0
        selection_rank = 0

        from app.matching.score_aggregator import ScoreAggregator
        from app.retrieval.isbak_tender_retriever import ChunkEvidence, ScoreBreakdown, TenderSearchResult
        import numpy as np
        import faiss
        from collections import defaultdict

        scorer = ScoreAggregator(settings=rag_settings)

        # O(1) lookup table for FAISS payloads
        faiss_payloads_by_tender = defaultdict(list)
        for pid, payload in tender_store.payloads.items():
            p_ikn = normalize_ikn(payload.get("ikn"))
            p_tid = str(payload.get("tender_id") or "").strip()
            if p_ikn:
                faiss_payloads_by_tender[p_ikn].append((pid, payload))
            if p_tid and p_tid != p_ikn:
                faiss_payloads_by_tender[p_tid].append((pid, payload))

        for tender in active_tenders:
            if args.max_decisions is not None and selection_rank >= args.max_decisions:
                break

            random_tenders_examined += 1
            examined_rank = random_tenders_examined
            log_prefix = "DATABASE_SEQUENTIAL_SELECTION" if is_sequential else "DATABASE_RANDOM_SELECTION"

            # İSBAK kendi ihalelerini atla (selection_rank tüketmez)
            if is_isbak_tender(tender.idare_adi):
                LOGGER.info(
                    "[%s] examined_rank=%s ikn=%s tender_id=%s status=skipped reason=isbak_own_tender",
                    log_prefix, examined_rank, tender.ikn, tender.id,
                )
                continue

            tender_ikn = normalize_ikn(tender.ikn)
            tender_id_str = str(tender.id)

            chunk_vectors = []
            chunk_payloads = []

            faiss_vector_extraction_error = None

            matched_items = faiss_payloads_by_tender.get(tender_ikn, [])
            if not matched_items and tender_id_str:
                matched_items = faiss_payloads_by_tender.get(tender_id_str, [])

            if matched_items:
                matched_pids = [pid for pid, _ in matched_items]
                try:
                    chunk_vectors = _get_vectors_for_faiss_ids(tender_store.index, matched_pids)
                    chunk_payloads = [payload for _, payload in matched_items]
                except Exception as e:
                    faiss_vector_extraction_error = e
                    LOGGER.error(
                        "[FAISS_VECTOR_EXTRACTION_ERROR] ikn=%s tender_id=%s exception_type=%s exception_message=%s",
                        tender.ikn, tender.id, type(e).__name__, str(e)
                    )

            semantic_evidence_available = len(chunk_vectors) > 0
            tender_vecs = None
            if semantic_evidence_available:
                tender_vecs = np.array(chunk_vectors, dtype=np.float32)
                faiss.normalize_L2(tender_vecs)

            best_profile = None
            best_score = -1.0
            best_candidate = None
            top_profiles = []
            top_str = ""

            if not semantic_evidence_available:
                if len(matched_items) == 0:
                    random_tenders_missing_faiss += 1
                    selection_status = "missing_faiss_evidence"
                    skipped_reason = "tender_not_in_faiss_snapshot"
                else:
                    random_tenders_missing_faiss += 1
                    selection_status = "faiss_vector_error"
                    skipped_reason = "faiss_vector_extraction_error"

                LOGGER.info(
                    "[%s] examined_rank=%s ikn=%s semantic_evidence_available=False status=skipped reason=%s",
                    log_prefix, examined_rank, tender.ikn, skipped_reason
                )
            else:
                selection_rank += 1
                random_tenders_with_semantic_evidence += 1
                random_tenders_submitted_to_model += 1
                selection_status = "selected"
                skipped_reason = ""
                LOGGER.info(
                    "[%s] examined_rank=%s selection_rank=%s ikn=%s tender_id=%s title=%s semantic_evidence_available=True status=selected",
                    log_prefix, examined_rank, selection_rank, tender.ikn, tender.id, tender.adi,
                )

            for p_code in profile_codes:
                if not semantic_evidence_available:
                    continue
                p_code_norm = str(p_code).strip().upper()
                p_meta = matcher._load_profile_meta(p_code_norm)
                strong_terms = p_meta.get("strong_terms", [])
                negative_terms = p_meta.get("negative_terms", [])
                okas_prefixes = p_meta.get("okas_prefixes", [])

                raw_scores = []
                if semantic_evidence_available and tender_vecs is not None:
                    p_entries = matcher._profile_entries(p_code_norm)
                    if p_entries:
                        p_vecs = []
                        for faiss_id, _ in p_entries:
                            try:
                                p_vecs.append(matcher._reconstruct_profile_vector(faiss_id))
                            except Exception:
                                pass
                        if p_vecs:
                            p_vecs_np = np.array(p_vecs, dtype=np.float32)
                            faiss.normalize_L2(p_vecs_np)
                            sims = np.dot(tender_vecs, p_vecs_np.T)
                            raw_scores = sims.max(axis=1).tolist()

                if not raw_scores:
                    raw_scores = [0.0]

                # query terms
                p_entries = matcher._profile_entries(p_code_norm)
                composite_query = "\n\n".join(
                    str(payload.get("text") or "").strip()
                    for _, payload in p_entries
                    if str(payload.get("text") or "").strip()
                )
                query_terms = matcher._query_terms_for_subclass(composite_query)

                if chunk_payloads:
                    from app.vector_store.faiss_vector_reader import FaissVectorReader
                    reader = FaissVectorReader()
                    section_types = [str(reader.resolve_section_type(c) or "") for c in chunk_payloads]
                    okas_codes = []
                    for c in chunk_payloads:
                        okas_codes.extend(reader.resolve_okas_codes(c))
                    evidence_texts = [str(c.get("text") or "")[:500] for c in chunk_payloads]
                    tender_name_val = str(tender.adi or "")
                else:
                    section_types = []
                    okas_codes = []
                    evidence_texts = []
                    tender_name_val = str(tender.adi or "")

                breakdown = scorer.compute(
                    raw_scores=raw_scores,
                    section_types=section_types,
                    okas_codes=list(dict.fromkeys(okas_codes)),
                    query_terms=query_terms,
                    tender_name=tender_name_val,
                    profile_okas_prefixes=okas_prefixes,
                    strong_terms=strong_terms,
                    negative_terms=negative_terms,
                    evidence_texts=evidence_texts,
                )

                top_profiles.append((p_code_norm, p_meta.get("name", ""), breakdown.final_score))

                if breakdown.final_score > best_score:
                    best_score = breakdown.final_score

                    scores_obj = ScoreBreakdown(
                        max_chunk=breakdown.max_similarity,
                        top_chunks_mean=breakdown.top_similarity_mean,
                        section_diversity=breakdown.section_diversity,
                        okas_support=breakdown.okas_support,
                        title_support=breakdown.title_support,
                        final=breakdown.final_score,
                        negative_penalty=breakdown.negative_term_penalty,
                    )

                    evidence_chunks = []
                    for idx, c in enumerate(chunk_payloads):
                        full_text = str(c.get("text") or "")
                        evidence_chunks.append(
                            ChunkEvidence(
                                chunk_id=str(c.get("chunk_id") or ""),
                                section_id=str(c.get("section_id") or ""),
                                chunk_title=str(c.get("title") or ""),
                                semantic_score=round(raw_scores[idx], 4) if idx < len(raw_scores) else 0.0,
                                text=full_text,
                                text_preview=full_text[:200],
                            )
                        )

                    best_candidate = TenderSearchResult(
                        tender_id=str(tender.id),
                        ikn=str(tender.ikn),
                        tender_name=tender_name_val,
                        chunk_title=evidence_chunks[0].chunk_title if evidence_chunks else "",
                        primary_profile_code=p_code_norm,
                        profile_codes=[p_code_norm],
                        classification_status="",
                        idare_adi=str(tender.idare_adi or ""),
                        il=str(tender.il or ""),
                        ihale_tarihi=str(tender.ihale_tarihi or ""),
                        ihale_turu=str(tender.ihale_turu or ""),
                        okas_codes=list(dict.fromkeys(okas_codes)),
                        section_ids=list(set(st for st in section_types if st)),
                        scores=scores_obj,
                        evidence_chunks=evidence_chunks,
                    )
                    best_profile = (p_code_norm, p_meta.get("name", ""))

            top_profiles.sort(key=lambda x: x[2], reverse=True)
            top_3 = top_profiles[:3]
            top_str = " | ".join(f"{code} {score:.2f}" for code, name, score in top_3)

            if best_candidate:
                unique_item = UniqueTenderCandidate(
                    tender_key=tender_identity(best_candidate),
                    primary_profile_code=best_candidate.primary_profile_code,
                    supporting_profile_codes=[],
                    profile_match_scores={best_candidate.primary_profile_code: best_candidate.scores.final},
                    profile_matches=[ProfileCandidateMatch(
                        profile_code=best_candidate.primary_profile_code,
                        retrieval_rank=selection_rank,
                        candidate=best_candidate,
                    )],
                    candidate=best_candidate,
                )
                unique_candidates.append(unique_item)

            database_random_selection_report.append({
                "selection_rank": selection_rank if selection_status == "selected" else "",
                "examined_rank": examined_rank,
                "tender_id": str(tender.id),
                "ikn": str(tender.ikn),
                "tender_title": str(tender.adi),
                "tender_status": str(tender.ihale_durumu),
                "semantic_evidence_available": semantic_evidence_available,
                "selection_status": selection_status,
                "skipped_reason": skipped_reason,
                "selected_profile_code": best_profile[0] if best_profile else "",
                "selected_profile_name": best_profile[1] if best_profile else "",
                "selected_profile_score": round(best_score, 4) if best_score >= 0 else 0.0,
                "top_profile_candidates": top_str,
            })

    elif args.selection_mode == "faiss-distant":
        from app.indexing.active_tender_indexer import is_isbak_tender
        from app.matching.score_aggregator import ScoreAggregator
        from app.retrieval.isbak_tender_retriever import ChunkEvidence, ScoreBreakdown, TenderSearchResult
        import numpy as np
        import faiss

        scorer = ScoreAggregator(settings=rag_settings)
        
        # Deduplicate all FAISS payloads by tender_id/ikn
        all_unique_tenders = {}
        for pid, payload in tender_store.payloads.items():
            t_id = str(payload.get("tender_id") or "").strip()
            t_ikn = normalize_ikn(payload.get("ikn"))
            key = t_ikn if t_ikn else t_id
            if not key:
                continue
            if key not in all_unique_tenders:
                all_unique_tenders[key] = []
            all_unique_tenders[key].append((pid, payload))
            
        unique_keys = list(all_unique_tenders.keys())
        unique_keys.sort() # For determinism before random
        rnd = random.Random(args.random_seed)
        rnd.shuffle(unique_keys)
        
        pool_size = min(args.distant_pool_size, len(unique_keys))
        sampled_keys = unique_keys[:pool_size]
        
        LOGGER.info(
            "[SELECTION_MODE] mode=faiss-distant source=FAISS "
            "requested=%s distant_pool_size=%s random_seed=%s",
            args.max_decisions, pool_size, args.random_seed
        )
        
        scored_candidates = []
        
        for idx, key in enumerate(sampled_keys, start=1):
            distant_tenders_examined += 1
            matched_items = all_unique_tenders[key]
            first_payload = matched_items[0][1]
            tender_id_val = str(first_payload.get("tender_id") or "")
            tender_ikn_val = str(first_payload.get("ikn") or "")
            authority_val = str(first_payload.get("idare_adi") or "")
            title_val = str(first_payload.get("adi") or "")
            
            if is_isbak_tender(authority_val):
                faiss_distant_selection_report.append({
                    "examined_rank": distant_tenders_examined,
                    "selection_rank": "",
                    "tender_id": tender_id_val,
                    "ikn": tender_ikn_val,
                    "title": title_val,
                    "best_profile_code": "",
                    "best_profile_score": "",
                    "status": "skipped",
                    "reason": "isbak_own_tender",
                })
                continue
                
            matched_pids = [pid for pid, _ in matched_items]
            try:
                chunk_vectors = _get_vectors_for_faiss_ids(tender_store.index, matched_pids)
                chunk_payloads = [payload for _, payload in matched_items]
            except Exception as e:
                LOGGER.error("FAISS extract error for %s: %s", key, e)
                continue
                
            if not chunk_vectors:
                continue
                
            tender_vecs = np.array(chunk_vectors, dtype=np.float32)
            faiss.normalize_L2(tender_vecs)
            
            best_profile = None
            best_score = -1.0
            best_candidate = None
            
            for p_code in profile_codes:
                p_code_norm = str(p_code).strip().upper()
                p_meta = matcher._load_profile_meta(p_code_norm)
                p_entries = matcher._profile_entries(p_code_norm)
                
                raw_scores = [0.0]
                if p_entries:
                    p_vecs = []
                    for faiss_id, _ in p_entries:
                        try:
                            p_vecs.append(matcher._reconstruct_profile_vector(faiss_id))
                        except Exception:
                            pass
                    if p_vecs:
                        p_vecs_np = np.array(p_vecs, dtype=np.float32)
                        faiss.normalize_L2(p_vecs_np)
                        sims = np.dot(tender_vecs, p_vecs_np.T)
                        raw_scores = sims.max(axis=1).tolist()
                        
                composite_query = "\n\n".join(
                    str(payload.get("text") or "").strip()
                    for _, payload in p_entries
                    if str(payload.get("text") or "").strip()
                )
                query_terms = matcher._query_terms_for_subclass(composite_query)
                
                from app.vector_store.faiss_vector_reader import FaissVectorReader
                reader = FaissVectorReader()
                section_types = [str(reader.resolve_section_type(c) or "") for c in chunk_payloads]
                okas_codes = []
                for c in chunk_payloads:
                    okas_codes.extend(reader.resolve_okas_codes(c))
                evidence_texts = [str(c.get("text") or "")[:500] for c in chunk_payloads]
                
                breakdown = scorer.compute(
                    raw_scores=raw_scores,
                    section_types=section_types,
                    okas_codes=list(dict.fromkeys(okas_codes)),
                    query_terms=query_terms,
                    tender_name=title_val,
                    profile_okas_prefixes=p_meta.get("okas_prefixes", []),
                    strong_terms=p_meta.get("strong_terms", []),
                    negative_terms=p_meta.get("negative_terms", []),
                    evidence_texts=evidence_texts,
                )
                
                if breakdown.final_score > best_score:
                    best_score = breakdown.final_score
                    
                    scores_obj = ScoreBreakdown(
                        max_chunk=breakdown.max_similarity,
                        top_chunks_mean=breakdown.top_similarity_mean,
                        section_diversity=breakdown.section_diversity,
                        okas_support=breakdown.okas_support,
                        title_support=breakdown.title_support,
                        final=breakdown.final_score,
                        negative_penalty=breakdown.negative_term_penalty,
                    )
                    
                    evidence_chunks = []
                    for c_idx, c in enumerate(chunk_payloads):
                        full_text = str(c.get("text") or "")
                        evidence_chunks.append(
                            ChunkEvidence(
                                chunk_id=str(c.get("chunk_id") or ""),
                                section_id=str(c.get("section_id") or ""),
                                chunk_title=str(c.get("title") or ""),
                                semantic_score=round(raw_scores[c_idx], 4) if c_idx < len(raw_scores) else 0.0,
                                text=full_text,
                                text_preview=full_text[:200],
                            )
                        )
                        
                    best_candidate = TenderSearchResult(
                        tender_id=tender_id_val,
                        ikn=tender_ikn_val,
                        tender_name=title_val,
                        chunk_title=evidence_chunks[0].chunk_title if evidence_chunks else "",
                        primary_profile_code=p_code_norm,
                        profile_codes=[p_code_norm],
                        classification_status="",
                        idare_adi=authority_val,
                        il=str(first_payload.get("il") or ""),
                        ihale_tarihi=str(first_payload.get("ihale_tarihi") or ""),
                        ihale_turu=str(first_payload.get("ihale_turu") or ""),
                        okas_codes=list(dict.fromkeys(okas_codes)),
                        section_ids=list(set(st for st in section_types if st)),
                        scores=scores_obj,
                        evidence_chunks=evidence_chunks,
                    )
                    best_profile = p_code_norm
                    
            if best_candidate:
                scored_candidates.append({
                    "best_score": best_score,
                    "best_profile": best_profile,
                    "candidate": best_candidate,
                    "examined_rank": distant_tenders_examined,
                })
                
        # Sort by best_profile_score ASC
        scored_candidates.sort(key=lambda x: x["best_score"])
        
        selection_rank = 0
        for item in scored_candidates:
            if args.max_decisions is not None and selection_rank >= args.max_decisions:
                break
                
            selection_rank += 1
            distant_tenders_selected += 1
            best_score = item["best_score"]
            distant_best_scores.append(best_score)
            c = item["candidate"]
            best_profile = item["best_profile"]
            
            LOGGER.info(
                "[FAISS_DISTANT_SELECTION] examined_rank=%s selection_rank=%s ikn=%s tender_id=%s title=%s best_profile_code=%s best_profile_score=%.4f status=selected",
                item["examined_rank"], selection_rank, c.ikn, c.tender_id, c.tender_name, best_profile, best_score
            )
            
            faiss_distant_selection_report.append({
                "selection_rank": selection_rank,
                "examined_rank": item["examined_rank"],
                "tender_id": c.tender_id,
                "ikn": c.ikn,
                "title": c.tender_name,
                "best_profile_code": best_profile,
                "best_profile_score": round(best_score, 4),
                "status": "selected",
                "reason": "",
            })
            
            unique_item = UniqueTenderCandidate(
                tender_key=tender_identity(c),
                primary_profile_code=best_profile,
                supporting_profile_codes=[],
                profile_match_scores={best_profile: c.scores.final},
                profile_matches=[ProfileCandidateMatch(
                    profile_code=best_profile,
                    retrieval_rank=selection_rank,
                    candidate=c,
                )],
                candidate=c,
            )
            unique_candidates.append(unique_item)




    decision_candidates = (
        unique_candidates[: args.max_decisions]
        if args.max_decisions is not None
        else unique_candidates
    )
    submitted_to_model = 0
    database_records: dict[str, TenderRecord] = {}
    database_preflight_path: Path | None = None
    evidence_selection_distribution: Counter[str] = Counter()

    if args.source_mode == "database":
        repository = TenderRepository()
        (
            database_records,
            database_preflight_path,
            database_failures,
        ) = prepare_database_sources(
            repository=repository,
            candidates=decision_candidates,
            report_dir=report_dir,
            active_status_values=settings.active_tender_status_values,
            allow_inactive=args.allow_inactive_database_records,
        )
        failures.extend(database_failures)
        LOGGER.info(
            "PostgreSQL kaynak ön kontrolü tamamlandı | aday=%s | uygun_kayıt=%s",
            len(decision_candidates),
            len(database_records),
        )

    if not args.retrieval_only:
        if pipeline is None:
            raise RuntimeError("Karar hattı oluşturulamadı.")

        context_builder = TenderSourceContextBuilder()
        for unique_item in decision_candidates:
            candidate = unique_item.candidate
            profile_code = unique_item.primary_profile_code
            company_context, evaluation_rules, loaded_support_codes = (
                render_company_context(
                    loader,
                    profile_code,
                    unique_item.supporting_profile_codes,
                )
            )
            supporting_codes = list(
                dict.fromkeys(
                    [
                        *unique_item.supporting_profile_codes,
                        *loaded_support_codes,
                    ]
                )
            )
            profile_data = loader.load_profile(profile_code)
            profile_signals = profile_data.get(
                "ihale_kategori_sinyalleri",
                {},
            )
            if not isinstance(profile_signals, dict):
                profile_signals = {}

            if args.source_mode == "database":
                source_record = database_records.get(
                    normalize_ikn(candidate.ikn)
                )
                if source_record is None:
                    continue
                try:
                    source_context = context_builder.build(
                        source_record,
                        profile_signals=profile_signals,
                    )
                    tender_context = render_database_tender_context(
                        source_context,
                        max_chars=settings.max_tender_context_chars,
                    )
                except Exception as exc:
                    LOGGER.exception(
                        "%s / %s PostgreSQL karar bağlamı hatası",
                        profile_code,
                        candidate.ikn,
                    )
                    failures.append(
                        {
                            "profile_code": profile_code,
                            "ikn": candidate.ikn,
                            "stage": "database_context",
                            "error": str(exc),
                        }
                    )
                    continue

                valid_chunk_ids = [
                    chunk.chunk_id
                    for chunk in source_context.selected_evidence_chunks
                    if chunk.chunk_id
                ]
                validation_context = source_context.validation_context(
                    profile_signals=profile_signals,
                    retrieval_score=candidate.scores.final,
                    profile_name=profile_data.get("profil_adi", ""),
                    primary_capabilities=profile_data.get("birincil_yetkinlikler", []),
                    profile_description=profile_data.get("description_expanded", ""),
                    technical_equipment=profile_data.get("technical_equipment", []),
                    abbreviations_and_jargon=profile_data.get("abbreviations_and_jargon", []),
                    action_verbs=profile_data.get("action_verbs", []),
                )
                evidence_count = len(
                    source_context.selected_evidence_chunks
                )
                evidence_selection_distribution[
                    source_context.selection.case_type
                ] += 1
                tender_id = str(source_record.id)
                tender_ikn = str(source_record.ikn)
                tender_name = str(source_record.adi or "")
                authority_name = str(source_record.idare_adi or "")
            else:
                tender_context = render_tender_context(matcher, candidate)
                valid_chunk_ids = [
                    chunk.chunk_id
                    for chunk in candidate.evidence_chunks
                    if chunk.chunk_id
                ]
                validation_context = DecisionValidationContext(
                    tender_name=candidate.tender_name,
                    tender_type=candidate.ihale_turu,
                    tender_okas_codes=list(candidate.okas_codes),
                    evidence_text_by_chunk={
                        chunk.chunk_id: chunk.text
                        for chunk in candidate.evidence_chunks
                        if chunk.chunk_id and chunk.text.strip()
                    },
                    profile_signals=profile_signals,
                    retrieval_score=candidate.scores.final,
                    profile_name=profile_data.get("profil_adi", ""),
                    primary_capabilities=profile_data.get("birincil_yetkinlikler", []),
                    profile_description=profile_data.get("description_expanded", ""),
                    technical_equipment=profile_data.get("technical_equipment", []),
                    abbreviations_and_jargon=profile_data.get("abbreviations_and_jargon", []),
                    action_verbs=profile_data.get("action_verbs", []),
                )
                evidence_count = len(candidate.evidence_chunks)
                tender_id = candidate.tender_id
                tender_ikn = candidate.ikn
                tender_name = candidate.tender_name
                authority_name = candidate.idare_adi
            submitted_to_model += 1

            try:
                decision = pipeline.run(
                    tender_id=tender_id,
                    ikn=tender_ikn,
                    tender_name=tender_name,
                    authority_name=authority_name,
                    category_code=profile_code,
                    primary_profile_code=profile_code,
                    secondary_profile_codes=supporting_codes,
                    tender_context=tender_context,
                    company_context=company_context,
                    evaluation_rules=evaluation_rules,
                    evidence_count=evidence_count,
                    retrieval_score=candidate.scores.final,
                    score_breakdown=candidate.scores.__dict__,
                    valid_chunk_ids=valid_chunk_ids,
                    validation_context=validation_context,
                    evaluated_profile_codes=[
                        profile_code,
                        *supporting_codes,
                    ],
                    profile_match_scores=unique_item.profile_match_scores,
                )
                decisions.append(decision)
                LOGGER.info(
                    "%s | %s | profil=%s | skor=%.4f | karar=%s",
                    unique_item.tender_key,
                    candidate.ikn,
                    profile_code,
                    candidate.scores.final,
                    decision.final_decision,
                )
            except Exception as exc:
                LOGGER.exception(
                    "%s / %s karar hatası",
                    profile_code,
                    candidate.ikn,
                )
                failures.append(
                    {
                        "profile_code": profile_code,
                        "ikn": candidate.ikn,
                        "stage": "decision",
                        "error": str(exc),
                    }
                )

        release_ollama_model(
            base_url=settings.ollama_base_url,
            model_name=settings.qwen_model,
        )

    retrieval_path = write_retrieval_report(
        report_dir=report_dir,
        rows=retrieval_rows,
    )
    unique_retrieval_path = write_retrieval_report(
        report_dir=report_dir,
        rows=unique_rows,
        filename="unique_tender_candidates.jsonl",
    )

    if decisions:
        DecisionReporter(output_dir=str(report_dir)).write_reports(decisions)

    elapsed = time.perf_counter() - started
    distribution = Counter(
        decision.final_decision for decision in decisions
    )
    summary = {
        "status": (
            "completed"
            if not failures
            else "completed_with_errors"
        ),
        "timestamp": datetime.now(UTC).isoformat(),
        "source_mode": args.source_mode,
        "selection_mode": args.selection_mode,
        "random_seed": args.random_seed,
        "elapsed_seconds": round(elapsed, 3),
        "profiles_requested": len(profile_codes),
        "candidate_rows": len(retrieval_rows),
        "unique_tender_candidates": len(unique_candidates),
        "active_tender_pool_size": active_tender_pool_size,
        "random_tenders_requested": args.max_decisions if args.selection_mode in ("database-random", "database-sequential") else None,
        "random_tenders_examined": random_tenders_examined if args.selection_mode in ("database-random", "database-sequential") else None,
        "random_tenders_missing_faiss": random_tenders_missing_faiss if args.selection_mode in ("database-random", "database-sequential") else None,
        "random_tenders_with_semantic_evidence": random_tenders_with_semantic_evidence if args.selection_mode in ("database-random", "database-sequential") else None,
        "random_tenders_submitted_to_model": random_tenders_submitted_to_model if args.selection_mode in ("database-random", "database-sequential") else None,
        "distant_pool_size": args.distant_pool_size if args.selection_mode == "faiss-distant" else None,
        "distant_tenders_examined": distant_tenders_examined if args.selection_mode == "faiss-distant" else None,
        "distant_tenders_selected": distant_tenders_selected if args.selection_mode == "faiss-distant" else None,
        "distant_best_score_min": round(min(distant_best_scores), 4) if distant_best_scores else None,
        "distant_best_score_max": round(max(distant_best_scores), 4) if distant_best_scores else None,
        "distant_best_score_avg": round(sum(distant_best_scores) / len(distant_best_scores), 4) if distant_best_scores else None,
        "unique_tenders_submitted_to_model": (
            submitted_to_model
        ),
        "decisions": len(decisions),
        "decision_distribution": dict(distribution),
        "failures": failures,
        "models": {
            "primary": (
                None if args.retrieval_only else settings.qwen_model
            ),
            "secondary": None,
        },
        "decision_architecture": "qwen_python_single_model",
        "prompt_versions": {
            "primary": (
                primary_model.prompt_version
                if primary_model is not None
                else None
            ),
            "secondary": None,
        },
        "database_source": {
            "preflight_report": (
                str(database_preflight_path)
                if database_preflight_path is not None
                else None
            ),
            "eligible_records": len(database_records),
        },
        "evidence_selection_distribution": dict(
            evidence_selection_distribution
        ),
        "indexes": {
            "tender_vectors": tender_store.count(),
            "profile_vectors": profile_store.count(),
        },
        "retrieval_report": str(retrieval_path),
        "unique_tender_report": str(unique_retrieval_path),
        "public_csv_report": str(report_dir / "tender_public_decisions.csv"),
    }

    if args.selection_mode in ("database-random", "database-sequential"):
        mode_slug = "sequential" if args.selection_mode == "database-sequential" else "random"
        db_random_report_path = report_dir / f"database_{mode_slug}_selection.csv"
        import csv
        with open(db_random_report_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "selection_rank", "examined_rank", "tender_id", "ikn", "tender_title", "tender_status",
                "semantic_evidence_available", "selection_status", "skipped_reason",
                "selected_profile_code", "selected_profile_name", "selected_profile_score",
                "top_profile_candidates"
            ])
            for row in database_random_selection_report:
                writer.writerow([
                    row["selection_rank"],
                    row["examined_rank"],
                    row["tender_id"],
                    row["ikn"],
                    row["tender_title"],
                    row["tender_status"],
                    row["semantic_evidence_available"],
                    row["selection_status"],
                    row["skipped_reason"],
                    row["selected_profile_code"],
                    row["selected_profile_name"],
                    row["selected_profile_score"],
                    row["top_profile_candidates"],
                ])
        summary[f"database_{mode_slug}_selection_csv"] = str(db_random_report_path)
        
    if args.selection_mode == "faiss-distant":
        distant_report_path = report_dir / "faiss_distant_selection.csv"
        import csv
        with open(distant_report_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "selection_rank", "examined_rank", "tender_id", "ikn", "title",
                "best_profile_code", "best_profile_score", "status", "reason"
            ])
            for row in faiss_distant_selection_report:
                writer.writerow([
                    row["selection_rank"],
                    row["examined_rank"],
                    row["tender_id"],
                    row["ikn"],
                    row["title"],
                    row["best_profile_code"],
                    row["best_profile_score"],
                    row["status"],
                    row["reason"],
                ])
        summary["faiss_distant_selection_csv"] = str(distant_report_path)
        
    summary_path = report_dir / "tender_decision_run_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n[KARAR ZİNCİRİ ÖZETİ]")
    print(f"Profil: {len(profile_codes)}")
    print(f"Profil-ihale adayı: {len(retrieval_rows)}")
    print(f"Benzersiz ihale adayı: {len(unique_candidates)}")
    print(f"Karar: {len(decisions)}")
    print(f"Dağılım: {dict(distribution)}")
    print(f"Hata: {len(failures)}")
    print(f"Süre: {elapsed:.2f} saniye")
    print(f"Özet raporu: {summary_path}")

    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nİşlem kullanıcı tarafından durduruldu.", file=sys.stderr)
        raise SystemExit(130)
