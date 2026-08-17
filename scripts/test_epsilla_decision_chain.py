#!/usr/bin/env python
"""Epsilla Vektör Veritabanı Teknoloji Testi — Uçtan Uca Karar Zinciri

Mimari:
  - PostgreSQL'den 10 aktif ihale çekilir (İSBAK kendi ihaleleri hariç).
  - Her ihale TenderDocumentBuilder + SectionAwareChunker ile chunk'lanır.
  - Chunk'lar BGE-M3 (CPU) ile embedding yapılır ve Epsilla'ya yazılır.
  - Karar aşamasında, AYNI İHALENİN kendi chunk'ları Epsilla'dan retrieval ile geri alınır.
  - PostgreSQL raw_tender_context karar modeline değil; Epsilla chunk metinleri verilir.
  - Puanlama mevcut ScoreAggregator + profil meta (strong_terms, negative_terms) kullanır.
  - Profil eşleştirme IsbakTenderProfileClassifier + IsbakProfileLoader ile yapılır.
  - Karar: OllamaDecisionModel (Qwen) + IsbakDeterministicValidator.

ÖNEMLI:
  - FAISS dosyalarına dokunulmaz.
  - Üretim kodunda hiçbir dosya değiştirilmez.
  - Test tamamlandıktan sonra mevcut FAISS sistemi olduğu gibi çalışır.

Çalıştırma:
    PYTHONPATH=. .venv/bin/python scripts/test_epsilla_decision_chain.py

Ön koşullar:
    .venv/bin/pip install pyepsilla
    docker run --pull=always -d -p 8888:8888 epsilla/epsilla
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Proje kökü
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Ayarlar — .env üzerinden
# ---------------------------------------------------------------------------
from app.config import get_settings
from app.config.isbak_rag_settings import get_isbak_rag_settings

_settings = get_settings()
_rag_settings = get_isbak_rag_settings()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("epsilla_test")

# ---------------------------------------------------------------------------
# psutil (isteğe bağlı)
# ---------------------------------------------------------------------------
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False
    logger.warning("psutil kurulu değil; sistem kaynakları ölçülemeyecek.")


# ===========================================================================
# YARDIMCI
# ===========================================================================


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _elapsed(start: float) -> float:
    return round(time.perf_counter() - start, 3)


class StageTimer:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._start: dict[str, float] = {}

    def begin(self, stage: str) -> None:
        self._start[stage] = time.perf_counter()
        self._data[stage] = {"start_mem_mb": _snapshot_memory(), "start_ts": _ts()}
        logger.info(f"[{stage}] ─── BAŞLADI")

    def end(self, stage: str, extra: dict[str, Any] | None = None) -> float:
        elapsed = _elapsed(self._start.get(stage, time.perf_counter()))
        self._data[stage].update(
            elapsed_s=elapsed,
            end_mem_mb=_snapshot_memory(),
            end_ts=_ts(),
        )
        if extra:
            self._data[stage].update(extra)
        logger.info(f"[{stage}] ─── TAMAMLANDI ({elapsed:.3f}s)")
        return elapsed

    def fail(self, stage: str, error: Exception) -> None:
        elapsed = _elapsed(self._start.get(stage, time.perf_counter()))
        self._data[stage].update(elapsed_s=elapsed, error=str(error), end_ts=_ts())
        logger.error(f"[{stage}] ─── BAŞARISIZ ({elapsed:.3f}s): {error}")

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


def _snapshot_memory() -> float:
    if _PSUTIL_AVAILABLE and psutil is not None:
        try:
            return round(psutil.Process().memory_info().rss / 1024 / 1024, 2)
        except Exception:
            pass
    return -1.0


def _system_resources() -> dict[str, Any]:
    result: dict[str, Any] = {"ts": _ts()}
    if not _PSUTIL_AVAILABLE or psutil is None:
        return result
    try:
        vm = psutil.virtual_memory()
        result.update(
            ram_total_mb=round(vm.total / 1024 / 1024, 1),
            ram_used_mb=round(vm.used / 1024 / 1024, 1),
            ram_percent=vm.percent,
        )
    except Exception:
        pass
    try:
        sw = psutil.swap_memory()
        result.update(
            swap_total_mb=round(sw.total / 1024 / 1024, 1),
            swap_used_mb=round(sw.used / 1024 / 1024, 1),
            swap_percent=sw.percent,
        )
    except Exception:
        pass
    try:
        result["cpu_percent"] = psutil.cpu_percent(interval=0.1)
    except Exception:
        pass
    try:
        result["python_rss_mb"] = round(psutil.Process().memory_info().rss / 1024 / 1024, 2)
    except Exception:
        pass
    return result


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"Rapor: {path}")


def _write_jsonl(path: Path, records: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    logger.info(f"JSONL rapor: {path} ({len(records)} kayıt)")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"CSV rapor: {path}")


# ---------------------------------------------------------------------------
# İSBAK kendi ihalelerini filtrele — mevcut sistemle aynı liste
# ---------------------------------------------------------------------------
_EXCLUDED_KEYWORDS: list[str] = [
    kw.lower() for kw in _rag_settings.excluded_authorities
]


def _is_isbak_tender(tender: Any) -> bool:
    authority = str(getattr(tender, "idare_adi", "") or "").lower()
    return any(kw in authority for kw in _EXCLUDED_KEYWORDS)


# ---------------------------------------------------------------------------
# Profil meta yükleme — ScoreAggregator için
# ---------------------------------------------------------------------------

def _load_profile_meta(
    profile_loader: Any, profile_code: str
) -> dict[str, Any]:
    """Profil dosyasından strong_terms, negative_terms, okas_prefixes yükle."""
    try:
        from app.company_profiles.isbak_profile import IsbakProfile
        profile_data = profile_loader.load_profile(profile_code)
        profile_obj = IsbakProfile.model_validate(profile_data)
        signals = profile_obj.ihale_kategori_sinyalleri
        return {
            "name": profile_obj.profil_adi,
            "strong_terms": signals.guclu_terimler,
            "negative_terms": signals.negatif_terimler,
            "okas_prefixes": signals.okas_kod_on_ekleri,
            "okas_text_support_required": bool(
                getattr(signals, "okas_metin_destegi_zorunlu", False)
            ),
        }
    except Exception as exc:
        logger.debug(f"Profil meta yüklenemedi ({profile_code}): {exc}")
        return {}


# ===========================================================================
# ANA TEST
# ===========================================================================



def _epsilla_match_profile(
    *,
    epsilla_client,
    embedder,
    tender,
    top_k: int = 20,
):
    """
    İhaleyi Epsilla IsbakProfiles tablosundaki profil parçalarıyla eşleştirir.

    Her profil 4 ayrı parça içerdiği için tek satır sonucu doğrudan seçmek
    yerine profile_code bazında gruplayarak skor üretir.
    """

    from collections import defaultdict

    parts = [
        str(getattr(tender, "adi", "") or ""),
        str(getattr(tender, "kapsam", "") or ""),
        str(getattr(tender, "ihale_turu", "") or ""),
    ]

    query_text = "\n".join(
        value.strip()
        for value in parts
        if value and value.strip()
    )

    if not query_text:
        raise RuntimeError("Profil eşleştirme sorgu metni boş.")

    vectors = embedder.embed([query_text])

    if not vectors:
        raise RuntimeError("Profil sorgu vektörü üretilemedi.")

    rows = epsilla_client.query(
        table_name="IsbakProfiles",
        query_field="embedding",
        query_vector=vectors[0],
        limit=top_k,
        response_fields=[
            "profile_code",
            "profile_name",
            "section",
            "text",
        ],
    )

    if not rows:
        raise RuntimeError("Epsilla profil araması sonuç döndürmedi.")

    grouped = defaultdict(list)

    for row in rows:
        code = str(row.get("profile_code") or "").strip()
        if not code:
            continue

        raw_distance = row.get("@distance")

        try:
            distance = float(raw_distance)
        except (TypeError, ValueError):
            distance = float("inf")

        # Epsilla küçük mesafeyi daha iyi sonuç olarak döndürüyor.
        # ScoreAggregator ve profil sıralaması yüksek skor = daha iyi
        # sözleşmesini kullandığı için mesafeyi [0,1] benzerlik skoruna
        # monoton biçimde dönüştürüyoruz.
        score = (
            0.0
            if distance == float("inf")
            else 1.0 / (1.0 + max(0.0, distance))
        )

        grouped[code].append(
            {
                "score": score,
                "section": str(row.get("section") or ""),
                "profile_name": str(row.get("profile_name") or ""),
            }
        )

    if not grouped:
        raise RuntimeError(
            "Epsilla profil sonuçlarında geçerli profile_code yok."
        )

    profile_scores = []

    for code, matches in grouped.items():
        scores = sorted(
            (x["score"] for x in matches),
            reverse=True,
        )

        max_score = scores[0]
        mean_score = sum(scores) / len(scores)
        support_count = len(scores)

        # Tek tesadüfi parçayı değil, birden fazla profil parçası desteğini
        # ödüllendiriyoruz.
        combined_score = (
            0.70 * max_score
            + 0.30 * mean_score
        )

        profile_scores.append(
            {
                "profile_code": code,
                "score": combined_score,
                "max_score": max_score,
                "mean_score": mean_score,
                "support_count": support_count,
                "matches": matches,
            }
        )

    profile_scores.sort(
        key=lambda item: (
            item["score"],
            item["support_count"],
        ),
        reverse=True,
    )

    primary = profile_scores[0]

    secondary = [
        item["profile_code"]
        for item in profile_scores[1:4]
        if item["score"] >= primary["score"] * 0.90
    ]

    return {
        "primary_profile_code": primary["profile_code"],
        "secondary_profile_codes": secondary,
        "primary_score": primary["score"],
        "ranking": profile_scores[:10],
        "query_text": query_text,
    }


def run_epsilla_test(
    tender_limit: int = 10,
    epsilla_host: str | None = None,
    epsilla_port: int | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    test_start = time.perf_counter()
    test_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    timer = StageTimer()

    if report_dir is None:
        report_dir = _PROJECT_ROOT / "reports" / f"epsilla_{tender_limit}_tender_test_{test_ts}"
    report_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Rapor dizini: {report_dir}")

    system_snapshots: list[dict[str, Any]] = []
    errors: list[str] = []

    def _snap() -> None:
        system_snapshots.append(_system_resources())

    # -----------------------------------------------------------------------
    # [POSTGRES] Bağlantı ve aktif ihale havuzu
    # -----------------------------------------------------------------------
    timer.begin("POSTGRES")
    _snap()
    all_tenders: list[Any] = []

    try:
        from app.database.tender_repository import TenderRepository
        repo = TenderRepository()
        schema_info = repo.validate_required_schema()
        logger.info(
            f"[POSTGRES] DB: {schema_info.get('database_name')}, "
            f"kullanıcı: {schema_info.get('database_user')}, "
            f"host: {_settings.database_host}:{_settings.database_port}"
        )
        all_tenders = repo.get_active_tenders()
        timer.end("POSTGRES", {"total_active": len(all_tenders)})
    except Exception as exc:
        timer.fail("POSTGRES", exc)
        errors.append(f"POSTGRES: {exc}")
        raise

    # -----------------------------------------------------------------------
    # [SELECTION] Gerçek karar hattına girebilecek ilk N aktif ihaleyi seç
    #
    # Kabul şartı:
    #   - İSBAK'ın kendi ihalesi olmayacak
    #   - Ayrıntılı TenderRecord yüklenecek
    #   - IsbakTenderProfileClassifier gerçek tam profil kodu üretecek
    #   - primary_profile_code None olmayacak
    #
    # Böylece test kümesine yalnızca Qwen karar hattına gerçekten
    # girebilecek ihaleler alınır.
    # -----------------------------------------------------------------------
    timer.begin("SELECTION")
    _snap()

    selected_tenders: list[Any] = []
    selection_classifications: dict[str, Any] = {}
    selection_rejections: list[dict[str, Any]] = []
    selection_scanned_count = 0
    selection_non_isbak_count = 0

    try:
        from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
        from app.classification.isbak_tender_profile_classifier import (
            IsbakTenderProfileClassifier,
        )

        selection_profile_loader = IsbakProfileLoader(
            base_path=_PROJECT_ROOT / "config" / "isbak"
        )
        selection_classifier = IsbakTenderProfileClassifier(
            selection_profile_loader
        )

        # Veritabanını tek tek sorgulamak yerine kontrollü gruplar halinde
        # ayrıntılı yükle.
        SELECTION_BATCH_SIZE = 50

        for batch_start in range(
            0,
            len(all_tenders),
            SELECTION_BATCH_SIZE,
        ):
            if len(selected_tenders) >= tender_limit:
                break

            source_batch = all_tenders[
                batch_start:batch_start + SELECTION_BATCH_SIZE
            ]

            # Önce ucuz idare filtresi.
            candidate_stub_batch = [
                tender
                for tender in source_batch
                if not _is_isbak_tender(tender)
            ]

            if not candidate_stub_batch:
                continue

            selection_non_isbak_count += len(candidate_stub_batch)

            batch_ikns = [
                str(getattr(tender, "ikn"))
                for tender in candidate_stub_batch
            ]

            # announcements, characteristics, okas_codes vb. dahil
            # gerçek ayrıntılı kayıtları getir.
            detailed_tenders = repo.get_by_ikns(batch_ikns)

            # Repository sıralamasına güvenme; DB sıralaması değişebilir.
            detailed_by_ikn = {
                str(getattr(tender, "ikn")): tender
                for tender in detailed_tenders
            }

            for ikn in batch_ikns:
                if len(selected_tenders) >= tender_limit:
                    break

                tender = detailed_by_ikn.get(ikn)
                if tender is None:
                    selection_rejections.append({
                        "ikn": ikn,
                        "reason": "detailed_record_missing",
                    })
                    continue

                selection_scanned_count += 1

                classification = selection_classifier.classify(tender)

                primary = classification.primary_profile_code

                if not primary:
                    selection_rejections.append({
                        "ikn": ikn,
                        "reason": "no_primary_profile",
                        "overall_status": classification.overall_status,
                        "review_profiles": classification.review_profile_codes,
                    })
                    continue

                # Güvenlik: grup kodu değil, tam profil kodu zorunlu.
                # Örn: OPS geçersiz, OPS-02 geçerli.
                if "-" not in primary:
                    selection_rejections.append({
                        "ikn": ikn,
                        "reason": "invalid_profile_code",
                        "primary_profile": primary,
                    })
                    continue

                selected_tenders.append(tender)
                selection_classifications[ikn] = classification

                logger.info(
                    f"[SELECTION_ACCEPT] "
                    f"{len(selected_tenders)}/{tender_limit} | "
                    f"IKN={ikn} | "
                    f"primary={primary} | "
                    f"status={classification.overall_status} | "
                    f"{str(getattr(tender, 'adi', ''))[:80]}"
                )

        # Profesyonel testte eksik örneklemle devam ETME.
        if len(selected_tenders) != tender_limit:
            raise RuntimeError(
                "Karar hattına girebilecek yeterli ihale bulunamadı: "
                f"beklenen={tender_limit}, "
                f"bulunan={len(selected_tenders)}, "
                f"taranan={selection_scanned_count}"
            )

        timer.end(
            "SELECTION",
            {
                "requested_count": tender_limit,
                "selected_count": len(selected_tenders),
                "scanned_detailed_count": selection_scanned_count,
                "non_isbak_examined": selection_non_isbak_count,
                "rejected_count": len(selection_rejections),
                "selected_ikns": [
                    str(getattr(t, "ikn"))
                    for t in selected_tenders
                ],
                "selected_profiles": {
                    ikn: classification.primary_profile_code
                    for ikn, classification
                    in selection_classifications.items()
                },
            },
        )

        logger.info(
            f"[SELECTION] TAM GEÇERLİ TEST KÜMESİ HAZIR | "
            f"seçilen={len(selected_tenders)} | "
            f"taranan={selection_scanned_count} | "
            f"reddedilen={len(selection_rejections)}"
        )

        for tender in selected_tenders:
            ikn = str(getattr(tender, "ikn"))
            classification = selection_classifications[ikn]

            logger.info(
                f"[SELECTION] IKN={ikn} | "
                f"primary={classification.primary_profile_code} | "
                f"secondary={classification.profile_codes[1:]} | "
                f"status={classification.overall_status} | "
                f"{str(getattr(tender, 'adi', ''))[:80]}"
            )

    except Exception as exc:
        timer.fail("SELECTION", exc)
        errors.append(f"SELECTION: {exc}")
        raise

    # -----------------------------------------------------------------------
    # [CHUNKING] TenderDocumentBuilder + SectionAwareChunker
    # -----------------------------------------------------------------------
    timer.begin("CHUNKING")
    _snap()
    all_chunks: list[dict[str, Any]] = []
    tender_chunk_map: dict[str, list[dict[str, Any]]] = {}  # ikn → chunks

    try:
        from app.indexing.document_builder import TenderDocumentBuilder
        from app.indexing.chunker import SectionAwareChunker

        doc_builder = TenderDocumentBuilder()
        chunker = SectionAwareChunker()

        for tender in selected_tenders:
            ikn = str(getattr(tender, "ikn"))
            document = doc_builder.build(tender)
            chunks = chunker.chunk_document(document)
            tender_chunk_map[ikn] = chunks
            all_chunks.extend(chunks)
            logger.info(
                f"[CHUNKING]   IKN={ikn} | başlık={str(getattr(tender,'adi',''))[:60]} | "
                f"chunk sayısı={len(chunks)}"
            )

        timer.end("CHUNKING", {"total_chunks": len(all_chunks)})
    except Exception as exc:
        timer.fail("CHUNKING", exc)
        errors.append(f"CHUNKING: {exc}")
        raise

    # -----------------------------------------------------------------------
    # [EMBEDDING] BGE-M3 / CPU
    # -----------------------------------------------------------------------
    timer.begin("EMBEDDING")
    _snap()
    embedder: Any = None
    all_embeddings: list[list[float]] = []
    vector_dim = 0

    try:
        from app.indexing.embedder import BgeM3Embedder

        embedder = BgeM3Embedder(
            model_name=_settings.embedding_model,
            device="cpu",
            cache_folder=_settings.model_cache_path,
            batch_size=_settings.embedding_batch_size,
            show_progress_bar=True,
        )

        texts = [c.get("embedding_text") or c.get("text", "") for c in all_chunks]
        all_embeddings = embedder.embed(texts)
        vector_dim = len(all_embeddings[0]) if all_embeddings else 0

        timer.end(
            "EMBEDDING",
            {
                "total": len(all_embeddings),
                "vector_dim": vector_dim,
                "model": _settings.embedding_model,
                "device": "cpu",
            },
        )
        logger.info(f"[EMBEDDING] {len(all_embeddings)} gömme üretildi, dim={vector_dim}")
    except Exception as exc:
        timer.fail("EMBEDDING", exc)
        errors.append(f"EMBEDDING: {exc}")
        raise

    # -----------------------------------------------------------------------
    # [EPSILLA_CONNECT] Bağlantı, DB ve tablo
    # -----------------------------------------------------------------------
    timer.begin("EPSILLA_CONNECT")
    _snap()
    epsilla_client: Any = None

    try:
        from app.retrieval.epsilla_tender_retriever import EpsillaClient

        epsilla_client = EpsillaClient(
            host=epsilla_host or os.environ.get("EPSILLA_HOST"),
            port=epsilla_port or (
                int(os.environ.get("EPSILLA_PORT", "8888"))
                if os.environ.get("EPSILLA_PORT") else None
            ),
        )
        logger.info(
            f"[EPSILLA_CONNECT] {epsilla_client.host}:{epsilla_client.port}"
        )
        epsilla_client.connect()
        epsilla_client.use_db("EkapTestDB")
        epsilla_client.create_table_if_not_exists("TenderChunks", vector_dim)

        timer.end(
            "EPSILLA_CONNECT",
            {
                "host": epsilla_client.host,
                "port": epsilla_client.port,
                "db": "EkapTestDB",
                "table": "TenderChunks",
                "vector_dim": vector_dim,
            },
        )
    except Exception as exc:
        timer.fail("EPSILLA_CONNECT", exc)
        errors.append(f"EPSILLA_CONNECT: {exc}")
        raise

    # -----------------------------------------------------------------------
    # [EPSILLA_INSERT] Chunk vektörlerini yaz
    # -----------------------------------------------------------------------
    timer.begin("EPSILLA_INSERT")
    _snap()
    total_inserted = 0

    try:
        records: list[dict[str, Any]] = []
        for chunk, emb in zip(all_chunks, all_embeddings):
            meta = chunk.get("metadata", {}) or {}
            okas_raw = chunk.get("okas_codes") or meta.get("okas_codes", [])
            okas_list = [str(x) for x in okas_raw if x] if isinstance(okas_raw, list) else []
            pc_raw = chunk.get("profile_codes") or meta.get("profile_codes", [])
            pc_list = [str(x) for x in pc_raw if x] if isinstance(pc_raw, list) else []

            records.append({
                "id":           str(chunk.get("chunk_id") or chunk.get("id") or ""),
                "tender_id":    str(chunk.get("tender_id") or ""),
                "ikn":          str(chunk.get("ikn") or ""),
                "title":        str(chunk.get("title") or "")[:500],
                "section":      str(chunk.get("section_type") or meta.get("section_type", ""))[:100],
                "chunk_id":     str(chunk.get("chunk_id") or ""),
                "section_id":   str(chunk.get("section_id") or ""),
                "text":         str(chunk.get("text") or "")[:4000],
                "authority":    str(chunk.get("authority_name") or "")[:300],
                "il":           str(chunk.get("il") or meta.get("il", ""))[:100],
                "ihale_tarihi": str(chunk.get("ihale_tarihi") or meta.get("ihale_tarihi", ""))[:50],
                "ihale_turu":   str(chunk.get("ihale_turu") or meta.get("ihale_turu", ""))[:100],
                "okas_codes":   json.dumps(okas_list, ensure_ascii=False),
                "profile_codes": json.dumps(pc_list, ensure_ascii=False),
                "embedding":    emb,
            })

        BATCH = 50
        for i in range(0, len(records), BATCH):
            batch = records[i : i + BATCH]
            epsilla_client.insert_records("TenderChunks", batch)
            total_inserted += len(batch)
            logger.info(f"[EPSILLA_INSERT] {total_inserted}/{len(records)} kayıt")

        timer.end("EPSILLA_INSERT", {"total_inserted": total_inserted})
    except Exception as exc:
        timer.fail("EPSILLA_INSERT", exc)
        errors.append(f"EPSILLA_INSERT: {exc}")
        raise

    # -----------------------------------------------------------------------
    # Yardımcı bileşenler — mevcut üretim kodu
    # -----------------------------------------------------------------------
    from app.retrieval.epsilla_tender_retriever import EpsillaTenderRetriever
    from app.matching.score_aggregator import ScoreAggregator
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
    from app.classification.isbak_tender_profile_classifier import IsbakTenderProfileClassifier
    from app.decision.ollama_decision_model import OllamaDecisionModel
    from app.decision.validator import IsbakDeterministicValidator
    from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
    from app.pipeline.isbak_tender_analysis_service import (
        format_company_context_compact,
        format_company_context,
    )

    scorer = ScoreAggregator()  # IsbakRagSettings'ten ağırlıkları okur
    epsilla_retriever = EpsillaTenderRetriever(
        epsilla_client=epsilla_client,
        embedder=embedder,
        scorer=scorer,
        top_k=40,
        max_chunks_per_tender=_rag_settings.faiss_max_chunks_per_tender,
    )
    profile_loader = IsbakProfileLoader(base_path=_PROJECT_ROOT / "config" / "isbak")
    profile_classifier = IsbakTenderProfileClassifier(profile_loader)
    decision_model = OllamaDecisionModel(
        name=_settings.qwen_model,
        host=_settings.ollama_base_url,
        prompt_version="isbak_qwen_decision_v4_compact",
    )
    validator = IsbakDeterministicValidator()
    decision_pipeline = IsbakDecisionPipeline(
        primary_model=decision_model,
        validator=validator,
    )

    # -----------------------------------------------------------------------
    # 10 ihale için karar döngüsü
    # -----------------------------------------------------------------------
    retrieval_log: list[dict[str, Any]] = []
    decisions_log: list[dict[str, Any]] = []
    dist_counts: dict[str, int] = {
        "uygun": 0, "uygun_degil": 0, "inceleme_gerekli": 0, "hata": 0
    }

    for idx, tender in enumerate(selected_tenders, 1):
        ikn = str(getattr(tender, "ikn"))
        tender_id = str(getattr(tender, "id"))
        title = str(getattr(tender, "adi", "") or ikn)
        logger.info(
            f"\n{'='*60}\n"
            f"İHALE {idx}/{len(selected_tenders)} | IKN={ikn}\n"
            f"  {title[:80]}\n"
            f"{'='*60}"
        )

        entry: dict[str, Any] = {
            "ikn": ikn,
            "tender_id": tender_id,
            "title": title,
            "idare_adi": str(getattr(tender, "idare_adi", "") or ""),
        }

        try:
            # ----------------------------------------------------------------
            # [PROFILE_MATCH] Profil belirleme
            # ----------------------------------------------------------------
            timer.begin(f"PROFILE_MATCH_{ikn}")
            _snap()

            okas_list_tender = getattr(tender, "okas_codes", []) or []
            okas_text = " ".join(
                str(getattr(c, "ad", "")) for c in okas_list_tender
                if getattr(c, "ad", "")
            )
            kapsam = str(getattr(tender, "kapsam", "") or "")
            query_text = f"{title} {kapsam} {okas_text}".strip()

            # Epsilla IsbakProfiles üzerinden anlamsal profil eşleştirme.
            # 20 profil / 80 profil parçası arasında yakınlık aranır.
            epsilla_profile_match = _epsilla_match_profile(
                epsilla_client=epsilla_client,
                embedder=embedder,
                tender=tender,
                top_k=20,
            )

            primary_profile = str(
                epsilla_profile_match["primary_profile_code"]
            ).strip()

            secondary_profiles = [
                str(code)
                for code in epsilla_profile_match.get(
                    "secondary_profile_codes", []
                )
                if str(code).strip()
                and str(code).strip() != primary_profile
            ]

            profile_score = float(
                epsilla_profile_match.get("primary_score", 0.0)
            )

            profile_ranking = epsilla_profile_match.get(
                "ranking", []
            )

            # Eski sınıflandırmanın sonucu yalnız karşılaştırma/telemetri
            # amacıyla tutulur. Qwen'e gidecek birincil profil Epsilla'dır.
            reference_classification = (
                selection_classifications.get(ikn)
                or profile_classifier.classify(tender)
            )

            reference_primary = (
                reference_classification.primary_profile_code
            )

            review_profiles = (
                reference_classification.review_profile_codes
            )

            evaluation_profiles = (
                reference_classification.evaluation_profile_codes
            )

            if not primary_profile:
                primary_profile = "BELIRLENEMEDI"

            timer.end(
                f"PROFILE_MATCH_{ikn}",
                {
                    "source": "epsilla",
                    "primary_profile": primary_profile,
                    "secondary_profiles": secondary_profiles,
                    "profile_score": profile_score,
                    "reference_primary": reference_primary,
                },
            )

            logger.info(
                f"[EPSILLA_PROFILE_MATCH] IKN={ikn} | "
                f"primary={primary_profile} | "
                f"secondary={secondary_profiles} | "
                f"score={profile_score:.6f} | "
                f"python_reference={reference_primary}"
            )

            logger.info(
                f"[EPSILLA_PROFILE_TOP] IKN={ikn} | "
                + " | ".join(
                    (
                        f"{item.get('profile_code')}="
                        f"{float(item.get('score', 0.0)):.6f}"
                        f"(max={float(item.get('max_score', 0.0)):.6f},"
                        f"n={int(item.get('support_count', 0))})"
                    )
                    for item in profile_ranking[:5]
                )
            )

            entry["primary_profile"] = primary_profile
            entry["secondary_profiles"] = secondary_profiles
            entry["epsilla_profile_score"] = profile_score
            entry["epsilla_profile_ranking"] = profile_ranking
            entry["python_reference_profile"] = reference_primary

            if primary_profile == "BELIRLENEMEDI":
                entry.update(final_decision="inceleme_gerekli", final_confidence=0.0,
                             reason="Profil belirlenemedi", evidence_chunk_ids=[])
                dist_counts["inceleme_gerekli"] += 1
                logger.info(f"[FINAL_DECISION] IKN={ikn} | inceleme_gerekli (profil yok)")
                decisions_log.append(entry)
                continue

            # Profil meta: strong_terms, negative_terms, okas_prefixes
            profile_meta = _load_profile_meta(profile_loader, primary_profile)

            # ----------------------------------------------------------------
            # [EPSILLA_RETRIEVAL] AYNI ihalenin chunk'larını Epsilla'dan al
            # ----------------------------------------------------------------
            timer.begin(f"EPSILLA_RETRIEVAL_{ikn}")
            _snap()

            tender_result = epsilla_retriever.retrieve_for_tender(
                query=query_text,
                tender_id=tender_id,
                ikn=ikn,
                profile_meta=profile_meta,
            )

            retrieval_elapsed = timer.end(
                f"EPSILLA_RETRIEVAL_{ikn}",
                {
                    "chunk_count": len(tender_result.evidence_chunks) if tender_result else 0,
                    "final_score": tender_result.scores.final if tender_result else 0.0,
                },
            )

            if tender_result is None:
                logger.warning(f"[EPSILLA_RETRIEVAL] IKN={ikn} için chunk bulunamadı!")
                chunk_count = 0
                evidence_chunk_ids: list[str] = []
                retrieval_score = 0.0
                tender_context = f"YENİ İHALE ŞARTLARI:\n[Epsilla'dan chunk getirilemedi: {ikn}]"
            else:
                chunk_count = len(tender_result.evidence_chunks)
                evidence_chunk_ids = [c.chunk_id for c in tender_result.evidence_chunks if c.chunk_id]
                retrieval_score = tender_result.scores.final
                logger.info(
                    f"[EPSILLA_RETRIEVAL] IKN={ikn} | {chunk_count} chunk | "
                    f"final_score={retrieval_score:.4f} | "
                    f"max_chunk={tender_result.scores.max_chunk:.4f} | "
                    f"neg_penalty={tender_result.scores.negative_penalty:.4f}"
                )

                # Karar bağlamını YALNIZCA Epsilla chunk metinlerinden oluştur
                tender_context_parts = [f"YENİ İHALE ŞARTLARI:\nIKN: {ikn} | {title}"]
                total_context_chars = len(tender_context_parts[0])
                max_chars = _settings.max_tender_context_chars

                for i, chunk_ev in enumerate(tender_result.evidence_chunks, 1):
                    header = (
                        f"\n[KAYNAK | chunk_id: {chunk_ev.chunk_id} | "
                        f"bölüm: {chunk_ev.section_id}]\n"
                    )
                    remaining = max_chars - total_context_chars - len(header) - 4
                    if remaining <= 50:
                        break
                    text_part = chunk_ev.text[:remaining]
                    entry_str = header + text_part
                    tender_context_parts.append(entry_str)
                    total_context_chars += len(entry_str)

                tender_context = "\n".join(tender_context_parts)[:max_chars]

            # Retrieval log
            retrieval_log.append({
                "ikn": ikn,
                "tender_id": tender_id,
                "query_preview": query_text[:200],
                "chunk_count": chunk_count,
                "retrieval_score": retrieval_score,
                "evidence_chunk_ids": evidence_chunk_ids,
                "score_breakdown": {
                    "max_chunk": tender_result.scores.max_chunk if tender_result else 0.0,
                    "top_chunks_mean": tender_result.scores.top_chunks_mean if tender_result else 0.0,
                    "section_diversity": tender_result.scores.section_diversity if tender_result else 0.0,
                    "okas_support": tender_result.scores.okas_support if tender_result else 0.0,
                    "title_support": tender_result.scores.title_support if tender_result else 0.0,
                    "negative_penalty": tender_result.scores.negative_penalty if tender_result else 0.0,
                    "final": retrieval_score,
                },
            })

            # ----------------------------------------------------------------
            # Şirket bağlamı — mevcut IsbakProfileLoader
            # ----------------------------------------------------------------
            context_dict = profile_loader.build_evaluation_context(
                primary_code=primary_profile,
                secondary_codes=secondary_profiles,
            )
            if _settings.qwen_compact_company_context:
                company_context, _ = format_company_context_compact(context_dict)
            else:
                company_context = format_company_context(context_dict)
            evaluation_rules = context_dict.get("degerlendirme_kurallari", {})

            # ----------------------------------------------------------------
            # [QWEN] Karar hattı
            # ----------------------------------------------------------------
            timer.begin(f"QWEN_{ikn}")
            _snap()

            logger.info(
                f"[QWEN] IKN={ikn} | model={_settings.qwen_model} | "
                f"num_ctx={_settings.qwen_decision_num_ctx}"
            )

            final_decision = decision_pipeline.run(
                tender_id=tender_id,
                ikn=ikn,
                tender_name=title,
                authority_name=str(getattr(tender, "idare_adi", "") or ""),
                category_code=str(getattr(tender, "ihale_turu", "GENEL") or "GENEL"),
                primary_profile_code=primary_profile,
                secondary_profile_codes=secondary_profiles,
                tender_context=tender_context,     # YALNIZCA Epsilla chunk metinleri
                company_context=company_context,
                evaluation_rules=evaluation_rules,
                evidence_count=chunk_count,
                valid_chunk_ids=evidence_chunk_ids,
            )

            qwen_elapsed = timer.end(
                f"QWEN_{ikn}",
                {
                    "qwen_decision": final_decision.final_decision,
                    "qwen_confidence": final_decision.final_confidence,
                },
            )

            # ----------------------------------------------------------------
            # [VALIDATOR] — pipeline içinde çalıştı; sonucu logla
            # ----------------------------------------------------------------
            val_passed = getattr(final_decision.validation, "passed", None)
            logger.info(
                f"[VALIDATOR] IKN={ikn} | passed={val_passed}"
            )

            # ----------------------------------------------------------------
            # [FINAL_DECISION]
            # ----------------------------------------------------------------
            final_dec = final_decision.final_decision
            final_conf = final_decision.final_confidence
            logger.info(
                f"[FINAL_DECISION] IKN={ikn} | "
                f"{final_dec} | güven={final_conf:.3f} | profil={primary_profile}"
            )
            dist_counts[final_dec] = dist_counts.get(final_dec, 0) + 1

            # Qwen diagnostics
            qwen_diag: dict[str, Any] = {}
            try:
                raw_resp = final_decision.primary_model.raw_response or {}
                meta_info = raw_resp.get("_metadata", {})
                qwen_diag = {
                    "load_duration_ns": raw_resp.get("load_duration"),
                    "prompt_eval_duration_ns": raw_resp.get("prompt_eval_duration"),
                    "eval_duration_ns": raw_resp.get("eval_duration"),
                    "total_duration_ns": raw_resp.get("total_duration"),
                    "prompt_eval_count": raw_resp.get("prompt_eval_count"),
                    "eval_count": raw_resp.get("eval_count"),
                    "done_reason": raw_resp.get("done_reason"),
                    "prompt_version": meta_info.get("prompt_version"),
                    "attempt_count": meta_info.get("attempt_count"),
                }
            except Exception:
                pass

            entry.update(
                final_decision=final_dec,
                final_confidence=final_conf,
                primary_decision=getattr(final_decision, "primary_decision", final_dec),
                primary_confidence=getattr(final_decision, "primary_confidence", final_conf),
                human_review_required=final_decision.human_review_required,
                activity_decision=getattr(final_decision, "activity_decision", ""),
                selected_profile=primary_profile,
                evidence_chunk_ids=evidence_chunk_ids,
                epsilla_chunk_count=chunk_count,
                epsilla_retrieval_score=retrieval_score,
                score_breakdown={
                    "max_chunk": tender_result.scores.max_chunk if tender_result else 0.0,
                    "okas_support": tender_result.scores.okas_support if tender_result else 0.0,
                    "negative_penalty": tender_result.scores.negative_penalty if tender_result else 0.0,
                    "final": retrieval_score,
                },
                qwen_elapsed_s=round(qwen_elapsed, 3),
                qwen_diagnostics=qwen_diag,
                validation_issues_count=len(
                    getattr(final_decision.validation, "issues", [])
                ),
            )

        except Exception as exc:
            tb = traceback.format_exc()
            logger.error(f"[ERROR] IKN={ikn}: {exc}\n{tb}")
            errors.append(f"IKN={ikn}: {exc}")
            dist_counts["hata"] += 1
            entry.update(final_decision="hata", error=str(exc))

        finally:
            _snap()
            decisions_log.append(entry)

    # -----------------------------------------------------------------------
    # Toplam
    # -----------------------------------------------------------------------
    total_elapsed = round(time.perf_counter() - test_start, 3)
    _snap()
    logger.info(
        f"\n{'='*60}\n"
        f"TEST TAMAMLANDI | {total_elapsed:.1f}s\n"
        f"Karar dağılımı: {dist_counts}\n"
        f"Hatalar: {len(errors)}\n"
        f"{'='*60}"
    )

    perf = timer.to_dict()
    perf["total_elapsed_s"] = total_elapsed

    resource_peak: dict[str, Any] = {}
    for key in ("python_rss_mb", "ram_used_mb", "swap_used_mb", "cpu_percent"):
        vals = [s.get(key, 0) for s in system_snapshots if s.get(key) is not None]
        if vals:
            resource_peak[f"peak_{key}"] = max(vals)

    summary = {
        "test_timestamp": test_ts,
        "report_dir": str(report_dir),
        "settings": {
            "db_host": _settings.database_host,
            "db_port": _settings.database_port,
            "db_name": _settings.database_name,
            "embedding_model": _settings.embedding_model,
            "embedding_device": "cpu",
            "qwen_model": _settings.qwen_model,
            "qwen_num_ctx": _settings.qwen_decision_num_ctx,
            "qwen_num_predict": _settings.qwen_decision_num_predict,
            "epsilla_host": epsilla_client.host if epsilla_client else "N/A",
            "epsilla_port": epsilla_client.port if epsilla_client else "N/A",
            "epsilla_db": "EkapTestDB",
            "epsilla_table": "TenderChunks",
            "distance_metric": "inner_product (= cosine similarity for normalized vectors)",
        },
        "counts": {
            "total_active_in_db": len(all_tenders),
            "selected_tenders": len(selected_tenders),
            "total_chunks": len(all_chunks),
            "total_embeddings": len(all_embeddings),
            "total_inserted_epsilla": total_inserted,
        },
        "decision_distribution": dist_counts,
        "total_elapsed_s": total_elapsed,
        "errors": errors,
        "resource_peak": resource_peak,
    }

    _write_json(report_dir / "epsilla_test_summary.json", summary)
    _write_jsonl(report_dir / "epsilla_tender_decisions.jsonl", decisions_log)
    _write_jsonl(report_dir / "epsilla_retrieval_results.jsonl", retrieval_log)
    _write_json(report_dir / "epsilla_performance_summary.json", perf)
    if system_snapshots:
        _write_csv(report_dir / "system_resources.csv", system_snapshots)
        _write_json(report_dir / "system_resources_summary.json", resource_peak)

    logger.info(f"Tüm raporlar: {report_dir}")
    return summary


# ===========================================================================
# CLI
# ===========================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Epsilla uçtan uca karar testi")
    parser.add_argument("--limit", type=int, default=10, help="Test edilecek ihale sayısı")
    parser.add_argument("--epsilla-host", default=None)
    parser.add_argument("--epsilla-port", type=int, default=None)
    parser.add_argument("--report-dir", default=None)
    args = parser.parse_args()

    try:
        run_epsilla_test(
            tender_limit=args.limit,
            epsilla_host=args.epsilla_host,
            epsilla_port=args.epsilla_port,
            report_dir=Path(args.report_dir) if args.report_dir else None,
        )
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Test başarısız: {e}", exc_info=True)
        sys.exit(1)
