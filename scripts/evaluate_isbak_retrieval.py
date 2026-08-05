"""İSBAK bilgi getirme katmanı değerlendirme betiği.

Gerçek Qdrant verisi üzerinde arama kalitesini ölçer ve raporlar.
Qdrant'a veya PostgreSQL'e hiçbir yazma yapmaz.
Ham vektörler rapora yazılmaz.

Kullanım
--------
::

    PYTHONPATH=. python scripts/evaluate_isbak_retrieval.py \\
        --queries evaluation/isbak_retrieval_queries.json \\
        --limit 10 \\
        --candidate-limit 100 \\
        --device cpu \\
        --output reports/isbak_retrieval_evaluation.json

Ağırlık profili karşılaştırması
---------------------------------
::

    PYTHONPATH=. python scripts/evaluate_isbak_retrieval.py \\
        --queries evaluation/isbak_retrieval_queries.json \\
        --weight-profile all \\
        --output reports/isbak_retrieval_evaluation.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ağırlık profilleri
# ---------------------------------------------------------------------------

_WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "default": {
        "semantic_weight": 0.60,
        "lexical_weight": 0.20,
        "profile_weight": 0.15,
        "metadata_weight": 0.05,
    },
    "semantic-heavy": {
        "semantic_weight": 0.70,
        "lexical_weight": 0.15,
        "profile_weight": 0.10,
        "metadata_weight": 0.05,
    },
    "profile-heavy": {
        "semantic_weight": 0.55,
        "lexical_weight": 0.20,
        "profile_weight": 0.20,
        "metadata_weight": 0.05,
    },
}


# ---------------------------------------------------------------------------
# Argüman ayrıştırma
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="İSBAK bilgi getirme değerlendirme betiği",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--queries",
        required=True,
        metavar="PATH",
        help="Değerlendirme sorgu JSON dosyası",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Her sorgu için döndürülecek ihale sayısı (varsayılan: 10)",
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=100,
        metavar="N",
        help="Qdrant'tan çekilecek parça sayısı (varsayılan: 100)",
    )
    parser.add_argument(
        "--device",
        default=None,
        metavar="STR",
        help="BGE-M3 cihazı: cpu veya cuda (varsayılan: ayar dosyasından)",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        metavar="STR",
        help="Gömme modeli adı (varsayılan: BAAI/bge-m3)",
    )
    parser.add_argument(
        "--qdrant-path",
        default=None,
        metavar="PATH",
        help="Qdrant veri dizini (varsayılan: storage/qdrant_isbak)",
    )
    parser.add_argument(
        "--collection-name",
        default=None,
        metavar="STR",
        help="Qdrant koleksiyon adı",
    )
    parser.add_argument(
        "--weight-profile",
        choices=list(_WEIGHT_PROFILES.keys()) + ["all"],
        default="default",
        help="Ağırlık profili: default | semantic-heavy | profile-heavy | all",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="JSON rapor çıktı yolu",
    )
    parser.add_argument(
        "--k-values",
        default="5,10",
        metavar="LIST",
        help="Virgülle ayrılmış K değerleri (varsayılan: 5,10)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Terminal özet çıktısını bastır",
    )
    parser.add_argument(
        "--labels",
        default=None,
        metavar="PATH",
        help="Gerçek PR/Recall/nDCG metrikleri için etiket JSON dosyası",
    )
    parser.add_argument(
        "--split",
        choices=["all", "development", "holdout"],
        default="all",
        help="Yalnızca belirtilen veri kümesinde çalış (labels verildiğinde anlamlıdır)",
    )
    parser.add_argument(
        "--settings-override",
        default=None,
        metavar="PATH",
        help="Ağırlık/Eşik ayarlarını değiştirmek için JSON dosyası",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Sorgu dosyası yükleme
# ---------------------------------------------------------------------------


def _load_queries(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        print(f"HATA: Sorgu dosyası bulunamadı: {p}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"HATA: Sorgu dosyası geçersiz JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    queries = data.get("queries", [])
    if not queries:
        print("HATA: Sorgu dosyasında 'queries' listesi boş.", file=sys.stderr)
        sys.exit(1)

    return queries


# ---------------------------------------------------------------------------
# Retriever oluşturma
# ---------------------------------------------------------------------------


def _build_retriever(
    settings_kwargs: dict[str, Any],
    weight_kwargs: dict[str, float],
):
    from app.config.isbak_rag_settings import IsbakRagSettings
    from app.indexing.embedder import BgeM3Embedder
    from app.retrieval.isbak_tender_retriever import IsbakTenderRetriever
    from app.vector_store.qdrant_store import QdrantVectorStore

    all_kwargs = {**settings_kwargs, **weight_kwargs}
    settings = IsbakRagSettings(**all_kwargs) if all_kwargs else IsbakRagSettings()

    qdrant_path = settings.resolved_qdrant_path
    store = QdrantVectorStore(
        path=str(qdrant_path),
        collection_name=settings.collection_name,
    )

    if not store.collection_exists():
        store.close()
        print(
            f"HATA: '{settings.collection_name}' koleksiyonu bulunamadı ({qdrant_path}).",
            file=sys.stderr,
        )
        sys.exit(1)

    embedder = BgeM3Embedder(
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=False,
    )

    retriever = IsbakTenderRetriever(
        embedder=embedder,
        vector_store=store,
        settings=settings,
    )
    return retriever, store, settings


# ---------------------------------------------------------------------------
# Tek sorgu değerlendirme
# ---------------------------------------------------------------------------


def _evaluate_single_query(
    query_data: dict[str, Any],
    retriever,
    limit: int,
    k_values: tuple[int, ...],
) -> dict[str, Any]:
    """Tek bir sorgu için arama yap ve metrikleri hesapla."""
    from app.evaluation.retrieval_metrics import evaluate_query

    query_id = query_data.get("query_id", "?")
    query_text = query_data.get("query", "")
    expected_profiles = query_data.get("expected_profile_groups", [])
    relevant_ikns = set(query_data.get("relevant_ikns", []))
    graded_relevance: dict[str, int] = {
        str(k): int(v) for k, v in query_data.get("graded_relevance", {}).items()
    }

    t0 = time.perf_counter()
    error_msg: str | None = None
    results = []

    try:
        results = retriever.retrieve(query_text, limit=limit)
    except Exception as exc:
        error_msg = str(exc)

    elapsed = round(time.perf_counter() - t0, 3)

    retrieved_ikns = [r.ikn for r in results]
    retrieved_profile_codes = [r.profile_codes for r in results]

    if error_msg:
        return {
            "query_id": query_id,
            "query": query_text,
            "profile_group": query_data.get("profile_group", ""),
            "status": "error",
            "error": error_msg,
            "elapsed_seconds": elapsed,
            "result_count": 0,
            "retrieved_ikns": [],
            "metrics": {},
        }

    metrics = evaluate_query(
        retrieved_ikns=retrieved_ikns,
        retrieved_profile_codes=retrieved_profile_codes,
        relevant_ikns=relevant_ikns,
        graded_relevance=graded_relevance,
        expected_profile_groups=expected_profiles,
        k_values=k_values,
    )

    # Top results özeti (ham vektör dahil değil)
    top_results_summary = [
        {
            "rank": rank,
            "ikn": r.ikn,
            "tender_name": r.tender_name,
            "profile_codes": r.profile_codes,
            "final_score": r.scores.final,
            "semantic_score": r.scores.semantic,
            "profile_score": r.scores.profile,
        }
        for rank, r in enumerate(results[:10], start=1)
    ]

    return {
        "query_id": query_id,
        "query": query_text,
        "profile_group": query_data.get("profile_group", ""),
        "status": "success",
        "elapsed_seconds": elapsed,
        "result_count": len(results),
        "retrieved_ikns": retrieved_ikns,
        "metrics": {k: (None if math.isnan(v) else round(v, 4)) for k, v in metrics.items()},
        "top_results": top_results_summary,
        "notes": query_data.get("notes", ""),
    }


# ---------------------------------------------------------------------------
# Profil grubu bazında özet
# ---------------------------------------------------------------------------


def _group_by_profile(
    query_results: list[dict[str, Any]],
    k_values: tuple[int, ...],
) -> dict[str, dict[str, Any]]:
    from app.evaluation.retrieval_metrics import aggregate_metrics

    groups: dict[str, list[dict[str, float]]] = {}
    for qr in query_results:
        if qr["status"] != "success":
            continue
        profile = qr.get("profile_group", "UNKNOWN")
        metrics = {k: v for k, v in qr["metrics"].items() if v is not None}
        if profile not in groups:
            groups[profile] = []
        groups[profile].append(metrics)

    result: dict[str, dict[str, Any]] = {}
    for profile, metrics_list in groups.items():
        agg = aggregate_metrics(metrics_list, k_values=k_values)
        result[profile] = {k: (None if math.isnan(v) else round(v, 4)) for k, v in agg.items()}

    return result


# ---------------------------------------------------------------------------
# Terminal çıktısı
# ---------------------------------------------------------------------------


def _print_summary(
    profile_name: str,
    global_agg: dict[str, Any],
    per_group: dict[str, dict[str, Any]],
    query_results: list[dict[str, Any]],
    k_values: tuple[int, ...],
) -> None:
    print(f"\n{'=' * 70}")
    print(f"  Ağırlık Profili: {profile_name.upper()}")
    print(f"{'=' * 70}")
    print(f"  Toplam sorgu : {len(query_results)}")
    success = sum(1 for q in query_results if q["status"] == "success")
    failed = len(query_results) - success
    print(f"  Başarılı     : {success}")
    if failed:
        print(f"  Başarısız    : {failed}  *** UYARI ***")

    print(f"\n  {'Metrik':<30} {'Değer':>10}")
    print(f"  {'-' * 42}")

    def _fmt(v: Any) -> str:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "    N/A"
        return f"  {v:.4f}"

    for k in k_values:
        k_str = str(k)
        print(f"  Precision@{k_str:<20} {_fmt(global_agg.get(f'precision@{k_str}'))}")
        print(f"  Recall@{k_str:<23} {_fmt(global_agg.get(f'recall@{k_str}'))}")
        print(f"  nDCG@{k_str:<25} {_fmt(global_agg.get(f'ndcg@{k_str}'))}")
        print(
            f"  Profile Precision@{k_str:<12} {_fmt(global_agg.get(f'profile_precision@{k_str}'))}"
        )

    print(f"  {'MRR':<30} {_fmt(global_agg.get('mrr'))}")
    dup_avg = global_agg.get("duplicate_ikn_count")
    print(f"  {'Ort. Tekrarlı İKN':<30} {_fmt(dup_avg)}")

    if per_group:
        print("\n  Profil Grubu Bazında (Profile Precision@5):")
        print(f"  {'-' * 42}")
        for group, agg in sorted(per_group.items()):
            pp5 = agg.get("profile_precision@5")
            print(f"  {group:<30} {_fmt(pp5)}")


# ---------------------------------------------------------------------------
# Ağırlık profili karşılaştırma çıktısı
# ---------------------------------------------------------------------------


def _print_weight_comparison(
    profile_summaries: list[dict[str, Any]],
    k_values: tuple[int, ...],
) -> None:
    print(f"\n{'=' * 70}")
    print("  AĞIRLIK PROFİLİ KARŞILAŞTIRMASI")
    print(f"{'=' * 70}")

    header = f"  {'Profil':<20}"
    for k in k_values:
        header += f"  P@{k:<5}  MRR    PP@{k:<5}"
    print(header)
    print(f"  {'-' * 65}")

    best_profile = None
    best_mrr = -1.0
    best_pp5 = -1.0

    for ps in profile_summaries:
        name = ps["weight_profile"]
        agg = ps["global_metrics"]
        mrr = agg.get("mrr") or 0.0
        pp5 = agg.get("profile_precision@5") or 0.0

        row = f"  {name:<20}"
        for k in k_values:
            p_at_k = agg.get(f"precision@{k}")
            pp_at_k = agg.get(f"profile_precision@{k}")
            row += f"  {p_at_k:.3f}" if p_at_k is not None else "   N/A "
            row += f"  {mrr:.3f}" if mrr else "   N/A "
            row += f"  {pp_at_k:.3f}" if pp_at_k is not None else "   N/A "
        print(row)

        if mrr + pp5 > best_mrr + best_pp5:
            best_mrr = mrr
            best_pp5 = pp5
            best_profile = name

    if best_profile:
        print(f"\n  ★ En başarılı ağırlık profili: {best_profile}")
        print(f"    MRR={best_mrr:.4f} | Profile Precision@5={best_pp5:.4f}")
        print("  (Ayar dosyası otomatik olarak değiştirilmedi.)")


# ---------------------------------------------------------------------------
# Ana fonksiyon
# ---------------------------------------------------------------------------


def main() -> int:
    args = _parse_args()

    # K değerlerini ayrıştır
    try:
        k_values = tuple(int(k.strip()) for k in args.k_values.split(","))
    except ValueError:
        print(f"HATA: Geçersiz k değerleri: {args.k_values}", file=sys.stderr)
        return 1

    queries = _load_queries(args.queries)

    # ---------------------------------------------------------
    # LABEL (ETİKET) DESTEĞİ
    # ---------------------------------------------------------
    if args.labels:
        from app.evaluation.relevance_dataset import RelevanceDataset

        ds = RelevanceDataset(args.labels)
        splits = RelevanceDataset.compute_dataset_splits(queries)

        filtered_queries = []
        for q in queries:
            qid = q["query_id"]
            q_split = splits.get(qid, "development")
            q["dataset_split"] = q_split

            if args.split != "all" and q_split != args.split:
                continue

            labels_for_query = ds.get_labels_by_query(qid)
            # Yalnızca incelenmiş (reviewed) olanları dikkate al
            reviewed_labels = [
                L
                for L in labels_for_query
                if L.label_status == "reviewed" and L.relevance_grade is not None
            ]

            if not reviewed_labels:
                # Eğer bu sorgu için hiç etiket yoksa, metrik hesaplanmaması için listeye almayabiliriz
                # ya da boş bırakırız.
                pass

            relevant_ikns = []
            graded = {}
            for L in reviewed_labels:
                grade = L.relevance_grade
                graded[L.ikn] = grade
                if grade >= 2:
                    relevant_ikns.append(L.ikn)

            q["relevant_ikns"] = relevant_ikns
            q["graded_relevance"] = graded

            filtered_queries.append(q)

        queries = filtered_queries

    if not queries:
        print(
            "Çalıştırılacak sorgu bulunamadı (split filtrelemesi veya etiket eksikliği).",
            file=sys.stderr,
        )
        return 0

    if not args.quiet:
        print(f"Sorgu dosyası: {args.queries}")
        print(f"Sorgu sayısı : {len(queries)}")
        print(f"K değerleri  : {k_values}")
        print(f"Limit        : {args.limit}")
        print(f"Candidate    : {args.candidate_limit}")

    # Ağırlık profillerini belirle
    if args.weight_profile == "all":
        profiles_to_run = list(_WEIGHT_PROFILES.keys())
    else:
        profiles_to_run = [args.weight_profile]

    # Ayarlar için temel kwargs
    base_settings_kwargs: dict[str, Any] = {
        "candidate_limit": args.candidate_limit,
        "result_limit": args.limit,
    }
    if args.device:
        base_settings_kwargs["embedding_device"] = args.device
    if args.model_name:
        base_settings_kwargs["embedding_model"] = args.model_name
    if args.qdrant_path:
        base_settings_kwargs["qdrant_isbak_path"] = args.qdrant_path
    if args.collection_name:
        base_settings_kwargs["collection_name"] = args.collection_name

    all_profile_summaries: list[dict[str, Any]] = []

    for profile_name in profiles_to_run:
        weight_kwargs = _WEIGHT_PROFILES.get(profile_name, {})

        if args.settings_override:
            try:
                with open(args.settings_override, encoding="utf-8") as sf:
                    override_data = json.load(sf)
                    # Eğer doğrudan sözlük veya best_configuration vs varsa al
                    if "best_configuration" in override_data:
                        weight_kwargs = override_data["best_configuration"]
                    else:
                        weight_kwargs.update(override_data)
                profile_name = f"{profile_name} (overridden)"
            except Exception as e:
                print(f"UYARI: Ayar dosyası okunamadı: {e}", file=sys.stderr)

        if not args.quiet:
            print(f"\n{'─' * 60}")
            print(f"Ağırlık profili yükleniyor: {profile_name}")
            print("Gömme modeli yükleniyor...")

        retriever, store, settings = _build_retriever(
            {**base_settings_kwargs},
            weight_kwargs,
        )

        if not args.quiet:
            print(f"Qdrant    : {settings.resolved_qdrant_path}")
            print(f"Koleksiyon: {settings.collection_name}")
            print(f"Model     : {settings.embedding_model}")
            print(
                f"Ağırlıklar: sem={settings.semantic_weight} | "
                f"lex={settings.lexical_weight} | "
                f"pro={settings.profile_weight} | "
                f"meta={settings.metadata_weight}"
            )
            print(f"\nDeğerlendirme başlıyor ({len(queries)} sorgu)...")

        query_results: list[dict[str, Any]] = []
        t_total_start = time.perf_counter()

        for i, qdata in enumerate(queries, start=1):
            qid = qdata.get("query_id", str(i))
            if not args.quiet:
                print(f"  [{i:02d}/{len(queries)}] {qid} ...", end="", flush=True)

            qr = _evaluate_single_query(qdata, retriever, args.limit, k_values)
            query_results.append(qr)

            if not args.quiet:
                status = "✓" if qr["status"] == "success" else "✗"
                elapsed = qr["elapsed_seconds"]
                pp5 = qr["metrics"].get("profile_precision@5")
                pp5_str = f"PP@5={pp5:.2f}" if pp5 is not None else "PP@5=N/A"
                print(f" {status} {elapsed:.1f}s | {pp5_str}")

        store.close()

        total_elapsed = round(time.perf_counter() - t_total_start, 2)

        # Metrikleri topla
        from app.evaluation.retrieval_metrics import aggregate_metrics

        all_metrics = [
            {k: v for k, v in qr["metrics"].items() if v is not None}
            for qr in query_results
            if qr["status"] == "success"
        ]
        global_agg_raw = aggregate_metrics(all_metrics, k_values=k_values)
        global_agg = {
            k: (None if math.isnan(v) else round(v, 4)) for k, v in global_agg_raw.items()
        }

        per_group = _group_by_profile(query_results, k_values=k_values)

        if not args.quiet:
            _print_summary(profile_name, global_agg, per_group, query_results, k_values)

        all_profile_summaries.append(
            {
                "weight_profile": profile_name,
                "weights": weight_kwargs,
                "total_elapsed_seconds": total_elapsed,
                "query_count": len(queries),
                "success_count": sum(1 for q in query_results if q["status"] == "success"),
                "failed_count": sum(1 for q in query_results if q["status"] == "error"),
                "global_metrics": global_agg,
                "per_profile_group_metrics": per_group,
                "query_results": query_results,
                "failed_queries": [
                    {"query_id": q["query_id"], "error": q.get("error")}
                    for q in query_results
                    if q["status"] == "error"
                ],
            }
        )

    # Ağırlık profili karşılaştırması
    if len(all_profile_summaries) > 1 and not args.quiet:
        _print_weight_comparison(all_profile_summaries, k_values)

    # JSON raporu
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # En iyi profili bul
        best_profile_name = None
        best_score = -1.0
        for ps in all_profile_summaries:
            mrr = ps["global_metrics"].get("mrr") or 0.0
            pp5 = ps["global_metrics"].get("profile_precision@5") or 0.0
            score = mrr + pp5
            if score > best_score:
                best_score = score
                best_profile_name = ps["weight_profile"]

        report = {
            "evaluation_metadata": {
                "query_file": args.queries,
                "k_values": list(k_values),
                "limit": args.limit,
                "candidate_limit": args.candidate_limit,
                "weight_profiles_evaluated": profiles_to_run,
                "best_weight_profile": best_profile_name,
                "note": (
                    "Ham vektörler bu raporda yer almaz. "
                    "Yalnızca profil doğruluğu ölçüldü; "
                    "relevant_ikns boş bırakılan sorgularda "
                    "precision/recall/nDCG hesaplanmadı."
                ),
            },
            "profiles": all_profile_summaries,
        }

        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if not args.quiet:
            print(f"\nRapor kaydedildi: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
