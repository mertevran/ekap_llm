#!/usr/bin/env python3
"""EKAP–İSBAK Çift Yönlü Eşleştirme Pipeline Ana Çalıştırıcısı.

Desteklenen modlar:
    --mode profile        : Bir profil için uygun ihaleleri bul
    --mode tender         : Bir ihale için uygun profilleri bul
    --mode tender-batch   : İlk N aktif ihale için profil araması
    --mode all-profiles   : Tüm aktif profiller için ihale araması

Kullanım örnekleri:
    PYTHONPATH=. python scripts/run_matching_pipeline.py \\
        --mode profile --profile-code AUS-01 --top-k 10

    PYTHONPATH=. python scripts/run_matching_pipeline.py \\
        --mode tender --ikn 2026/1296877 --top-k 3

    PYTHONPATH=. python scripts/run_matching_pipeline.py \\
        --mode tender-batch --limit 20 --top-k 3 --retrieval-only

    PYTHONPATH=. python scripts/run_matching_pipeline.py \\
        --mode all-profiles --top-k 10 --retrieval-only
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Proje kökü PYTHONPATH'e ekle (PYTHONPATH=. ile de çalışır)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Temel yardımcı
# ---------------------------------------------------------------------------

def _make_run_id(mode: str, identifier: str = "") -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    safe_id = str(identifier).replace("/", "-").replace("\\", "-").replace(" ", "_")
    if safe_id:
        return f"{ts}_{mode}_{safe_id}"
    return f"{ts}_{mode}"


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )


logger = logging.getLogger("run_matching_pipeline")


# ---------------------------------------------------------------------------
# FAISS store yükleme (bir kez başlangıçta)
# ---------------------------------------------------------------------------

def _load_stores(args: argparse.Namespace):
    from app.config.isbak_rag_settings import get_isbak_rag_settings
    from app.vector_store.faiss_store import FaissVectorStore

    settings = get_isbak_rag_settings()

    tender_path = args.tender_faiss_path or settings.faiss_tender_path
    tender_collection = args.tender_collection or settings.faiss_tender_collection
    profile_path = args.profile_faiss_path or settings.faiss_profile_path
    profile_collection = args.profile_collection or settings.faiss_profile_collection

    logger.info("İhale FAISS indeksi yükleniyor: %s / %s", tender_path, tender_collection)
    tender_store = FaissVectorStore(path=tender_path, collection_name=tender_collection)

    logger.info("Profil FAISS indeksi yükleniyor: %s / %s", profile_path, profile_collection)
    profile_store = FaissVectorStore(path=profile_path, collection_name=profile_collection)

    logger.info(
        "İndeksler yüklendi — ihale: %d vektör, profil: %d vektör",
        tender_store.count(),
        profile_store.count(),
    )
    return tender_store, profile_store, settings


def _load_profile_loader():
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
    return IsbakProfileLoader()


# ---------------------------------------------------------------------------
# Rapor yardımcıları
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            if hasattr(rec, "model_dump"):
                f.write(json.dumps(rec.model_dump(), ensure_ascii=False) + "\n")
            elif isinstance(rec, dict):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            else:
                f.write(json.dumps(vars(rec), ensure_ascii=False) + "\n")


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(data, "model_dump"):
        out = data.model_dump()
    elif isinstance(data, dict):
        out = data
    else:
        out = vars(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def _write_csv(path: Path, records: list[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(records[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def _flatten_match(m) -> dict:
    """ProfileTenderMatch veya TenderProfileMatch'i CSV satırına dönüştürür."""
    base = m.model_dump() if hasattr(m, "model_dump") else vars(m)
    # score_breakdown iç içe — düzleştir
    breakdown = base.pop("score_breakdown", {}) or {}
    for k, v in breakdown.items():
        base[f"score_{k}"] = v
    # Liste alanları → virgülle birleştir
    for key in ("evidence_chunk_ids", "evidence_sections", "evidence_texts",
                "tender_chunk_ids", "profile_section_ids"):
        if key in base and isinstance(base[key], list):
            base[key] = "|".join(str(x) for x in base[key][:5])
    return base


# ---------------------------------------------------------------------------
# Profil modu
# ---------------------------------------------------------------------------

def run_profile_mode(args: argparse.Namespace) -> int:
    """Bir profil için ihale araması."""
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_code = args.profile_code
    if not profile_code:
        logger.error("--profile-code gereklidir.")
        return 1

    run_id = _make_run_id("profile", profile_code)
    report_base = Path(args.report_dir) / run_id
    report_base.mkdir(parents=True, exist_ok=True)

    tender_store, profile_store, settings = _load_stores(args)
    profile_loader = _load_profile_loader()

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
        profile_loader=profile_loader,
    )

    t0 = time.perf_counter()
    logger.info("Profil araması başlıyor: %s top_k=%d", profile_code, args.top_k)

    try:
        matches = matcher.match(
            profile_code=profile_code,
            top_k=args.top_k,
            minimum_score=args.minimum_score,
        )
    except Exception as exc:
        logger.error("Profil araması başarısız: %s", exc)
        return 1

    elapsed = round(time.perf_counter() - t0, 3)
    logger.info(
        "Profil araması tamamlandı: %d ihale bulundu (%.2fs)", len(matches), elapsed
    )

    # Raporlar
    _write_jsonl(report_base / "profile_to_tender_matches.jsonl", matches)
    _write_csv(report_base / "profile_to_tender_matches.csv", [_flatten_match(m) for m in matches])

    summary = {
        "run_id": run_id,
        "profile_code": profile_code,
        "top_k": args.top_k,
        "minimum_score": args.minimum_score,
        "total_candidates": len(matches),
        "retrieval_only": args.retrieval_only,
        "prompt_versions": {
            "primary": None if args.retrieval_only else "isbak_qwen_decision_v3",
        },
        "elapsed_seconds": elapsed,
    }
    _write_json(report_base / "profile_summary.json", summary)

    # Konsol özet
    print(f"\n{'='*60}")
    print(f"Profil: {profile_code} | run_id: {run_id}")
    print(f"Bulunan ihale sayısı: {len(matches)}")
    for i, m in enumerate(matches[:5], 1):
        print(f"  {i}. [{m.retrieval_score:.4f}] {m.ikn} — {m.tender_name[:60]}")
    print(f"Rapor: {report_base}")
    print(f"{'='*60}\n")

    if args.retrieval_only:
        logger.info("--retrieval-only: LLM kararı atlandı.")
        return 0

    # LLM kararı
    decision_count = 0
    max_decisions = args.max_decisions if args.max_decisions else len(matches)

    from app.config import get_settings
    from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
    from app.decision.ollama_decision_model import OllamaDecisionModel
    from app.matching.context_builder import MatchContextBuilder
    from app.validation.isbak_rule_validator import IsbakRuleValidator

    app_settings = get_settings()
    context_builder = MatchContextBuilder(max_chars=settings.llm_context_max_chars)
    primary_model = OllamaDecisionModel(
        name=app_settings.qwen_model,
        host=app_settings.ollama_base_url,
        prompt_version="isbak_qwen_decision_v3",
    )
    pipeline = IsbakDecisionPipeline(
        primary_model=primary_model,
        validator=validator,
    )

    for m in matches[:max_decisions]:
        if decision_count >= max_decisions:
            break
        try:
            profile_data = profile_loader.load_profile(profile_code)
            ctx = context_builder.build_for_profile_match(
                match=m, profile_data=profile_data
            )
            pipeline.run(
                tender_id=m.tender_id,
                ikn=m.ikn,
                tender_name=m.tender_name,
                authority_name=m.authority_name,
                category_code=profile_code,
                primary_profile_code=profile_code,
                secondary_profile_codes=[],
                tender_context=ctx["tender_context"],
                company_context=ctx["company_context"],
                evaluation_rules={},
                evidence_count=len(m.evidence_chunk_ids),
                matching_mode="profile_to_tender",
                retrieval_score=m.retrieval_score,
                score_breakdown=m.score_breakdown.model_dump() if m.score_breakdown else {},
                valid_chunk_ids=m.evidence_chunk_ids,
            )
            decision_count += 1
            logger.info("Karar tamamlandı: %s", m.ikn)
        except Exception as exc:
            logger.error("Karar hatası [%s]: %s", m.ikn, exc)

    return 0


# ---------------------------------------------------------------------------
# İhale modu
# ---------------------------------------------------------------------------

def run_tender_mode(args: argparse.Namespace) -> int:
    """Bir ihale için profil araması."""
    from app.matching.decision_aggregator import DecisionAggregator
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    ikn = args.ikn
    if not ikn:
        logger.error("--ikn gereklidir.")
        return 1

    run_id = _make_run_id("tender", ikn)
    report_base = Path(args.report_dir) / run_id
    report_base.mkdir(parents=True, exist_ok=True)

    tender_store, profile_store, settings = _load_stores(args)
    profile_loader = _load_profile_loader()

    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
        profile_loader=profile_loader,
    )

    t0 = time.perf_counter()
    logger.info("İhale araması başlıyor: %s top_k=%d", ikn, args.top_k)

    try:
        matches = matcher.match(
            ikn=ikn,
            top_k=args.top_k,
            minimum_score=args.minimum_score,
        )
    except Exception as exc:
        logger.error("İhale araması başarısız: %s", exc)
        return 1

    elapsed = round(time.perf_counter() - t0, 3)
    logger.info(
        "İhale araması tamamlandı: %d profil bulundu (%.2fs)", len(matches), elapsed
    )

    _write_jsonl(report_base / "tender_to_profile_matches.jsonl", matches)
    _write_csv(report_base / "tender_to_profile_matches.csv", [_flatten_match(m) for m in matches])

    summary = {
        "run_id": run_id,
        "ikn": ikn,
        "top_k": args.top_k,
        "minimum_score": args.minimum_score,
        "total_profiles": len(matches),
        "retrieval_only": args.retrieval_only,
        "prompt_versions": {
            "primary": None if args.retrieval_only else "isbak_qwen_decision_v3",
        },
        "elapsed_seconds": elapsed,
    }
    _write_json(report_base / "tender_summary.json", summary)

    print(f"\n{'='*60}")
    print(f"İhale: {ikn} | run_id: {run_id}")
    print(f"Bulunan profil sayısı: {len(matches)}")
    for i, m in enumerate(matches, 1):
        print(f"  {i}. [{m.retrieval_score:.4f}] {m.profile_code} — {m.profile_name}")
    print(f"Rapor: {report_base}")
    print(f"{'='*60}\n")

    if args.retrieval_only:
        logger.info("--retrieval-only: LLM kararı atlandı.")
        return 0

    # LLM kararları
    max_decisions = args.max_decisions if args.max_decisions else len(matches)
    pipeline_decisions = []
    errors = []

    from app.config import get_settings
    from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
    from app.decision.ollama_decision_model import OllamaDecisionModel
    from app.matching.context_builder import MatchContextBuilder
    from app.validation.isbak_rule_validator import IsbakRuleValidator

    app_settings = get_settings()
    context_builder = MatchContextBuilder(max_chars=settings.llm_context_max_chars)
    primary_model = OllamaDecisionModel(
        name=app_settings.qwen_model, host=app_settings.ollama_base_url,
        prompt_version="isbak_qwen_decision_v3",
    )
    validator = IsbakRuleValidator()
    pipeline = IsbakDecisionPipeline(
        primary_model=primary_model, validator=validator,
    )

    for m in matches[:max_decisions]:
        try:
            profile_data = profile_loader.load_profile(m.profile_code)
            ctx = context_builder.build_for_tender_match(
                match=m, profile_data=profile_data
            )
            decision = pipeline.run(
                tender_id=m.tender_id,
                ikn=m.ikn,
                tender_name=m.tender_name,
                authority_name=m.authority_name,
                category_code=m.profile_code,
                primary_profile_code=m.profile_code,
                secondary_profile_codes=[],
                tender_context=ctx["tender_context"],
                company_context=ctx["company_context"],
                evaluation_rules={},
                evidence_count=len(m.tender_chunk_ids),
                matching_mode="tender_to_profile" if args.mode == "tender" else "profile_to_tender",
                retrieval_score=m.retrieval_score,
                score_breakdown=m.score_breakdown.model_dump() if m.score_breakdown else {},
                valid_chunk_ids=m.tender_chunk_ids,
            )
            pipeline_decisions.append(decision)
            logger.info("Karar tamamlandı: %s → %s", m.profile_code, decision.final_decision)
        except Exception as exc:
            logger.error("Karar hatası [%s]: %s", m.profile_code, exc)
            errors.append({
                "profile_code": m.profile_code,
                "ikn": ikn,
                "stage": "decision",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })

    # Nihai karar
    if pipeline_decisions:
        aggregator = DecisionAggregator()
        best_score = max((m.retrieval_score for m in matches), default=0.0)
        tender_final = aggregator.aggregate_from_pipeline_decisions(
            tender_id=matches[0].tender_id if matches else "",
            ikn=ikn,
            tender_name=matches[0].tender_name if matches else "",
            authority_name=matches[0].authority_name if matches else "",
            best_retrieval_score=best_score,
            pipeline_decisions=pipeline_decisions,
        )
        _write_json(report_base / "tender_final_decision.json", tender_final)

    if errors:
        _write_jsonl(report_base / "errors.jsonl", errors)

    return 0


# ---------------------------------------------------------------------------
# Toplu ihale modu
# ---------------------------------------------------------------------------

def run_tender_batch_mode(args: argparse.Namespace) -> int:
    """İlk N aktif ihale için profil araması."""
    from psycopg.rows import dict_row

    from app.database.connection import get_connection
    from app.matching.decision_aggregator import DecisionAggregator
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    limit = args.limit or 20
    run_id = _make_run_id("tender-batch", str(limit))
    report_base = Path(args.report_dir) / run_id
    report_base.mkdir(parents=True, exist_ok=True)

    tender_store, profile_store, settings = _load_stores(args)
    profile_loader = _load_profile_loader()

    # Aktif ihaleleri veritabanından al
    logger.info("Aktif ihaleler alınıyor (limit=%d)...", limit)
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT tender_id, ikn
                FROM llm_rag.tender_index_state
                WHERE is_active = TRUE
                  AND index_status = 'indexed'
                  AND chunk_count > 0
                ORDER BY first_seen_at ASC, tender_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            active_rows = cur.fetchall()

    logger.info("Değerlendirilecek ihale sayısı: %d", len(active_rows))

    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
        profile_loader=profile_loader,
    )

    all_matches = []
    all_final_decisions = []
    errors = []
    decision_count = 0
    max_decisions = args.max_decisions if args.max_decisions else (len(active_rows) * args.top_k)

    t0 = time.perf_counter()

    from app.config import get_settings as _get_settings
    from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
    from app.decision.ollama_decision_model import OllamaDecisionModel
    from app.matching.context_builder import MatchContextBuilder
    from app.validation.isbak_rule_validator import IsbakRuleValidator

    app_settings = _get_settings()

    if not args.retrieval_only:
        primary_model = OllamaDecisionModel(
            name=app_settings.qwen_model, host=app_settings.ollama_base_url,
            prompt_version="isbak_qwen_decision_v3",
        )
        validator = IsbakRuleValidator()
        pipeline = IsbakDecisionPipeline(
            primary_model=primary_model, validator=validator,
        )
        context_builder = MatchContextBuilder(max_chars=settings.llm_context_max_chars)
        aggregator = DecisionAggregator()

    for row in active_rows:
        ikn = str(row.get("ikn") or row.get("tender_id") or "")
        tender_id = str(row.get("tender_id") or "")
        try:
            matches = matcher.match(
                tender_id=tender_id or None,
                ikn=ikn or None,
                top_k=args.top_k,
                minimum_score=args.minimum_score,
            )
            all_matches.extend(matches)

            if not args.retrieval_only and matches:
                pipe_decisions = []
                for m in matches:
                    if decision_count >= max_decisions:
                        break
                    try:
                        profile_data = profile_loader.load_profile(m.profile_code)
                        ctx = context_builder.build_for_tender_match(
                            match=m, profile_data=profile_data
                        )
                        decision = pipeline.run(
                            tender_id=m.tender_id,
                            ikn=m.ikn,
                            tender_name=m.tender_name,
                            authority_name=m.authority_name,
                            category_code=m.profile_code,
                            primary_profile_code=m.profile_code,
                            secondary_profile_codes=[],
                            tender_context=ctx["tender_context"],
                            company_context=ctx["company_context"],
                            evaluation_rules={},
                            evidence_count=len(m.tender_chunk_ids),
                            matching_mode="tender_to_profile",
                            retrieval_score=m.retrieval_score,
                            score_breakdown=m.score_breakdown.model_dump() if m.score_breakdown else {},
                            valid_chunk_ids=m.tender_chunk_ids,
                        )
                        pipe_decisions.append(decision)
                        decision_count += 1
                    except Exception as exc:
                        logger.error("Karar hatası [%s/%s]: %s", ikn, m.profile_code, exc)
                        errors.append({
                            "ikn": ikn,
                            "tender_id": tender_id,
                            "profile_code": m.profile_code,
                            "stage": "primary_model",
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        })

                if pipe_decisions:
                    best_score = max((m.retrieval_score for m in matches), default=0.0)
                    final = aggregator.aggregate_from_pipeline_decisions(
                        tender_id=matches[0].tender_id,
                        ikn=ikn,
                        tender_name=matches[0].tender_name,
                        authority_name=matches[0].authority_name,
                        best_retrieval_score=best_score,
                        pipeline_decisions=pipe_decisions,
                    )
                    all_final_decisions.append(final)

        except KeyError:
            logger.debug("İhale FAISS'te bulunamadı: %s", ikn)
        except Exception as exc:
            logger.error("İhale işleme hatası [%s]: %s", ikn, exc)
            errors.append({
                "ikn": ikn,
                "tender_id": tender_id,
                "profile_code": "",
                "stage": "retrieval",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })

    elapsed = round(time.perf_counter() - t0, 3)

    # Raporlar
    _write_jsonl(report_base / "tender_profile_matches.jsonl", all_matches)
    _write_csv(
        report_base / "tender_profile_matches.csv",
        [_flatten_match(m) for m in all_matches],
    )

    if all_final_decisions:
        _write_csv(
            report_base / "tender_final_decisions.csv",
            [d.model_dump() for d in all_final_decisions],
        )

    if errors:
        _write_jsonl(report_base / "failures.jsonl", errors)

    run_summary = {
        "run_id": run_id,
        "mode": "tender-batch",
        "total_tenders": len(active_rows),
        "total_matches": len(all_matches),
        "total_decisions": len(all_final_decisions),
        "errors": len(errors),
        "retrieval_only": args.retrieval_only,
        "prompt_versions": {
            "primary": None if args.retrieval_only else "isbak_qwen_decision_v3",
        },
        "elapsed_seconds": elapsed,
    }
    _write_json(report_base / "run_summary.json", run_summary)

    print(f"\n{'='*60}")
    print(f"Toplu ihale modu | run_id: {run_id}")
    print(f"İşlenen ihale: {len(active_rows)}")
    print(f"Eşleşme: {len(all_matches)} | Karar: {len(all_final_decisions)}")
    print(f"Hata: {len(errors)} | Süre: {elapsed:.1f}s")
    print(f"Rapor: {report_base}")
    print(f"{'='*60}\n")

    return 1 if errors else 0


# ---------------------------------------------------------------------------
# Tüm profiller modu
# ---------------------------------------------------------------------------

def run_all_profiles_mode(args: argparse.Namespace) -> int:
    """Tüm aktif profiller için ihale araması."""
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    run_id = _make_run_id("all-profiles")
    report_base = Path(args.report_dir) / run_id
    report_base.mkdir(parents=True, exist_ok=True)

    tender_store, profile_store, settings = _load_stores(args)
    profile_loader = _load_profile_loader()

    active_profiles = profile_loader.list_profiles(active_only=True)
    logger.info("Aktif profil sayısı: %d", len(active_profiles))

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
        profile_loader=profile_loader,
    )

    all_matches = []
    errors = []
    t0 = time.perf_counter()

    for profile_entry in active_profiles:
        profile_code = str(profile_entry.get("profil_kodu", "")).strip().upper()
        if not profile_code:
            continue
        try:
            matches = matcher.match(
                profile_code=profile_code,
                top_k=args.top_k,
                minimum_score=args.minimum_score,
            )
            all_matches.extend(matches)
            logger.info("Profil %s: %d ihale bulundu", profile_code, len(matches))
        except Exception as exc:
            logger.error("Profil hatası [%s]: %s", profile_code, exc)
            errors.append({
                "profile_code": profile_code,
                "stage": "retrieval",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })

    elapsed = round(time.perf_counter() - t0, 3)

    _write_jsonl(report_base / "all_profiles_matches.jsonl", all_matches)
    _write_csv(report_base / "all_profiles_matches.csv", [_flatten_match(m) for m in all_matches])

    if errors:
        _write_jsonl(report_base / "failures.jsonl", errors)

    run_summary = {
        "run_id": run_id,
        "mode": "all-profiles",
        "total_profiles": len(active_profiles),
        "total_matches": len(all_matches),
        "errors": len(errors),
        "elapsed_seconds": elapsed,
    }
    _write_json(report_base / "run_summary.json", run_summary)

    print(f"\n{'='*60}")
    print(f"Tüm profiller modu | run_id: {run_id}")
    print(f"İşlenen profil: {len(active_profiles)}")
    print(f"Toplam eşleşme: {len(all_matches)} | Hata: {len(errors)}")
    print(f"Rapor: {report_base}")
    print(f"{'='*60}\n")

    return 1 if errors else 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="EKAP–İSBAK Çift Yönlü Eşleştirme Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--mode",
        required=True,
        choices=["profile", "tender", "tender-batch", "all-profiles"],
        help="Çalışma modu",
    )
    p.add_argument("--profile-code", default="", help="Profil kodu (profile modu için)")
    p.add_argument("--ikn", default="", help="İhale Kayıt Numarası (tender modu için)")
    p.add_argument("--top-k", type=int, default=10, help="Döndürülecek en fazla sonuç")
    p.add_argument("--limit", type=int, default=20, help="tender-batch için ihale sayısı")
    p.add_argument(
        "--minimum-score",
        type=float,
        default=None,
        dest="minimum_score",
        help="Minimum puan eşiği",
    )
    p.add_argument("--retrieval-only", action="store_true", help="LLM kararı yapma")

    p.add_argument("--max-decisions", type=int, default=0, help="Maksimum LLM karar sayısı")
    p.add_argument("--force", action="store_true", help="Mevcut kararı yeniden oluştur")
    p.add_argument("--report-dir", default="reports", help="Rapor dizini")
    p.add_argument("--log-level", default="INFO", help="Log seviyesi")
    p.add_argument("--tender-faiss-path", default="", help="İhale FAISS dizin yolu")
    p.add_argument("--tender-collection", default="", help="İhale FAISS koleksiyon adı")
    p.add_argument("--profile-faiss-path", default="", help="Profil FAISS dizin yolu")
    p.add_argument("--profile-collection", default="", help="Profil FAISS koleksiyon adı")
    return p


# ---------------------------------------------------------------------------
# Ana giriş noktası
# ---------------------------------------------------------------------------

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _setup_logging(args.log_level)

    logger.info("Mod: %s", args.mode)

    mode_map = {
        "profile": run_profile_mode,
        "tender": run_tender_mode,
        "tender-batch": run_tender_batch_mode,
        "all-profiles": run_all_profiles_mode,
    }

    fn = mode_map.get(args.mode)
    if fn is None:
        logger.error("Bilinmeyen mod: %s", args.mode)
        return 1

    try:
        return fn(args)
    except KeyboardInterrupt:
        logger.warning("Kullanıcı tarafından iptal edildi.")
        return 130
    except Exception as exc:
        logger.error("Beklenmeyen hata: %s", exc)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
