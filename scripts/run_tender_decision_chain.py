#!/usr/bin/env python
"""
Hazır profil ve ihale FAISS indeksleri üzerinde tek modelli karar zinciri.

Akış:
  Profil FAISS vektörleri
  → ihale FAISS aday araması
  → ihale bazında kanıt birleştirme
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
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import DecisionValidationContext
from app.decision.ollama_decision_model import OllamaDecisionModel
from app.decision.tender_batch import (
    ProfileCandidateMatch,
    group_unique_tenders,
    tender_identity,
)
from app.decision.validator import IsbakDeterministicValidator
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
        default=None,
        help="Tüm çalışma için üretilecek azami karar sayısı.",
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
        with httpx.Client(timeout=10.0) as client:
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
        include_supporting_profiles=False,
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

    compact_context = remove_empty_values(evaluation_context)
    company_context = json.dumps(
        compact_context,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    company_context = compact_text(
        company_context,
        settings.max_company_context_chars,
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


def release_ollama_model(*, base_url: str, model_name: str) -> None:
    """Toplu kararlar tamamlandığında Qwen modelini Ollama belleğinden çıkarır."""

    try:
        with httpx.Client(timeout=15.0) as client:
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
        profile_loader=loader,
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
            prompt_version="isbak_qwen_decision_v3",
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

    decision_candidates = unique_candidates
    submitted_to_model = 0

    if not args.retrieval_only:
        if pipeline is None:
            raise RuntimeError("Karar hattı oluşturulamadı.")

        for unique_item in decision_candidates:
            if (
                args.max_decisions is not None
                and len(decisions) >= args.max_decisions
            ):
                break

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
            tender_context = render_tender_context(matcher, candidate)
            valid_chunk_ids = [
                chunk.chunk_id
                for chunk in candidate.evidence_chunks
                if chunk.chunk_id
            ]
            profile_data = loader.load_profile(profile_code)
            profile_signals = profile_data.get(
                "ihale_kategori_sinyalleri",
                {},
            )
            if not isinstance(profile_signals, dict):
                profile_signals = {}
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
            )
            submitted_to_model += 1

            try:
                decision = pipeline.run(
                    tender_id=candidate.tender_id,
                    ikn=candidate.ikn,
                    tender_name=candidate.tender_name,
                    authority_name=candidate.idare_adi,
                    category_code=profile_code,
                    primary_profile_code=profile_code,
                    secondary_profile_codes=supporting_codes,
                    tender_context=tender_context,
                    company_context=company_context,
                    evaluation_rules=evaluation_rules,
                    evidence_count=len(candidate.evidence_chunks),
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
        "elapsed_seconds": round(elapsed, 3),
        "profiles_requested": len(profile_codes),
        "candidate_rows": len(retrieval_rows),
        "unique_tender_candidates": len(unique_candidates),
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
        "indexes": {
            "tender_vectors": tender_store.count(),
            "profile_vectors": profile_store.count(),
        },
        "retrieval_report": str(retrieval_path),
        "unique_tender_report": str(unique_retrieval_path),
    }
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
