from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.indexing.embedder import BgeM3Embedder
from app.retrieval.epsilla_tender_retriever import EpsillaClient


# ============================================================
# AYARLAR
# ============================================================

EPSILLA_HOST = "localhost"
EPSILLA_PORT = 8888
EPSILLA_DB = "EkapTestDB"
EPSILLA_TABLE = "IsbakProfiles"

TOP_K = 10

REPORT_DIR = Path("experiments/epsilla/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
report_path = REPORT_DIR / f"epsilla_benchmark_{timestamp}.json"


# ============================================================
# KONTROLLÜ TEST SORGULARI
# expected = beklenen profil
# ============================================================

TEST_QUERIES = [
    {
        "expected": "ENT-03",
        "query": "Araç takip sistemi filo yönetimi GPS konum takibi telemetri",
    },
    {
        "expected": "AUS-02",
        "query": "Elektronik denetleme sistemi plaka tanıma PTS radar kamera trafik ihlal tespiti",
    },
    {
        "expected": "AUS-03",
        "query": "Trafik yoğunluğu ölçüm sistemi araç sayım sistemi otopark yönlendirme bilgilendirme ekranı",
    },
    {
        "expected": "AUS-04",
        "query": "Toplu taşıma yönetim sistemi yolcu bilgilendirme raylı sistem araç konum rota izleme",
    },
    {
        "expected": "ENT-02",
        "query": "Kamera güvenlik sistemi video analiz görüntü işleme akıllı video izleme",
    },
    {
        "expected": "ENT-04",
        "query": "Personel devam kontrol sistemi kartlı geçiş erişim kontrol turnike",
    },
    {
        "expected": "ENT-05",
        "query": "Akıllı aydınlatma enerji yönetimi uzaktan kontrol sokak aydınlatması",
    },
    {
        "expected": "TEK-01",
        "query": "Yazılım geliştirme veri entegrasyonu API uygulama entegrasyonu veri platformu",
    },
    {
        "expected": "TEK-03",
        "query": "Ağ altyapısı haberleşme sistemi network fiber optik veri iletişimi",
    },
    {
        "expected": "OPS-02",
        "query": "Bakım onarım teknik destek periyodik bakım sistem destek hizmeti",
    },
]


def print_header(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def get_container_stats() -> dict:
    """
    Epsilla Docker kapsayıcısının anlık CPU/RAM bilgisini almaya çalışır.
    Başarısız olursa test durmaz.
    """
    try:
        output = subprocess.check_output(
            [
                "docker",
                "stats",
                "epsilla-test",
                "--no-stream",
                "--format",
                "{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}",
            ],
            text=True,
            timeout=10,
        ).strip()

        cpu, mem_usage, mem_percent = output.split("|", 2)

        return {
            "cpu_percent_raw": cpu,
            "memory_usage_raw": mem_usage,
            "memory_percent_raw": mem_percent,
        }
    except Exception as exc:
        return {
            "error": str(exc),
        }


def get_process_rss_mb() -> float | None:
    try:
        import psutil

        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def rank_profile_codes(rows: list[dict]) -> list[str]:
    """
    Epsilla satırlarını döndüğü sıraya göre profil bazında tekilleştirir.

    Burada herhangi bir yeni skor formülü YOKTUR.
    0.70/0.30 veya 1/(1+d) gibi dönüşümler kullanılmaz.

    Amaç doğrudan Epsilla'nın doğal sıralamasını gözlemlemektir.
    """
    result: list[str] = []
    seen: set[str] = set()

    for row in rows:
        code = str(row.get("profile_code") or "").strip()

        if not code or code in seen:
            continue

        seen.add(code)
        result.append(code)

    return result


def main() -> None:
    settings = get_settings()

    print_header("EPSILLA BAĞIMSIZ BENCHMARK (BAŞARIM ÖLÇÜMÜ)")

    print(f"Epsilla     : {EPSILLA_HOST}:{EPSILLA_PORT}")
    print(f"DB          : {EPSILLA_DB}")
    print(f"Tablo       : {EPSILLA_TABLE}")
    print(f"Embedding   : {settings.embedding_model}")
    print("Cihaz       : CPU")
    print(f"Test sorgusu: {len(TEST_QUERIES)}")
    print(f"Top-K       : {TOP_K}")

    report = {
        "timestamp": timestamp,
        "epsilla": {
            "host": EPSILLA_HOST,
            "port": EPSILLA_PORT,
            "db": EPSILLA_DB,
            "table": EPSILLA_TABLE,
        },
        "embedding": {
            "model": settings.embedding_model,
            "device": "cpu",
        },
        "tests": [],
    }

    # --------------------------------------------------------
    # 1. EMBEDDER
    # --------------------------------------------------------

    print_header("1. BGE-M3 EMBEDDING (GÖMME) YÜKLENİYOR")

    t0 = time.perf_counter()

    embedder = BgeM3Embedder(
        model_name=settings.embedding_model,
        device="cpu",
        cache_folder=settings.model_cache_path,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=False,
    )

    embedder_init_s = time.perf_counter() - t0

    print(f"Embedder yükleme süresi: {embedder_init_s:.3f} s")

    report["embedding"]["init_seconds"] = embedder_init_s

    # --------------------------------------------------------
    # 2. VEKTÖR NORM TESTİ
    # --------------------------------------------------------

    print_header("2. VEKTÖR NORM TESTİ")

    norm_vector = np.asarray(
        embedder.embed(["Araç takip sistemi GPS telemetri"])[0],
        dtype=np.float32,
    )

    norm = float(np.linalg.norm(norm_vector))

    print(f"Vektör boyutu : {len(norm_vector)}")
    print(f"L2 norm       : {norm:.8f}")

    if abs(norm - 1.0) <= 0.01:
        print("SONUÇ         : BGE-M3 çıktısı yaklaşık L2-normalize.")
        normalized = True
    else:
        print("SONUÇ         : BGE-M3 çıktısı L2-normalize görünmüyor.")
        normalized = False

    report["embedding"]["vector_dim"] = len(norm_vector)
    report["embedding"]["sample_l2_norm"] = norm
    report["embedding"]["appears_l2_normalized"] = normalized

    # --------------------------------------------------------
    # 3. EPSILLA BAĞLANTISI
    # --------------------------------------------------------

    print_header("3. EPSILLA BAĞLANTISI")

    t0 = time.perf_counter()

    client = EpsillaClient(
        host=EPSILLA_HOST,
        port=EPSILLA_PORT,
    )

    client.connect()
    client.use_db(EPSILLA_DB)

    connection_s = time.perf_counter() - t0

    print(f"Bağlantı başarılı: {connection_s:.3f} s")

    report["epsilla"]["connection_seconds"] = connection_s

    before_stats = get_container_stats()
    report["container_before"] = before_stats

    # --------------------------------------------------------
    # 4. QUERY TESTLERİ
    # --------------------------------------------------------

    print_header("4. EPSILLA TOP-K RETRIEVAL (GETİRİM) TESTLERİ")

    latencies = []
    top1_hits = 0
    top3_hits = 0

    for number, test in enumerate(TEST_QUERIES, 1):
        expected = test["expected"]
        query_text = test["query"]

        embed_start = time.perf_counter()
        vector = embedder.embed([query_text])[0]
        query_embedding_s = time.perf_counter() - embed_start

        vector_norm = float(
            np.linalg.norm(np.asarray(vector, dtype=np.float32))
        )

        query_start = time.perf_counter()

        rows = client.query(
            table_name=EPSILLA_TABLE,
            query_field="embedding",
            query_vector=vector,
            limit=TOP_K,
            response_fields=[
                "profile_code",
                "profile_name",
                "section",
                "text",
            ],
        )

        query_s = time.perf_counter() - query_start
        latencies.append(query_s)

        ranking = rank_profile_codes(rows)

        top1 = ranking[0] if ranking else None
        top3 = ranking[:3]

        top1_ok = top1 == expected
        top3_ok = expected in top3

        if top1_ok:
            top1_hits += 1

        if top3_ok:
            top3_hits += 1

        print()
        print("-" * 80)
        print(f"TEST {number}/{len(TEST_QUERIES)}")
        print(f"Beklenen profil : {expected}")
        print(f"Sorgu           : {query_text}")
        print(f"Embedding süresi: {query_embedding_s:.4f} s")
        print(f"Epsilla sorgusu : {query_s:.4f} s")
        print(f"Vektör normu    : {vector_norm:.8f}")
        print(f"Top-1           : {top1}")
        print(f"Top-3           : {top3}")
        print(f"Top-1 doğru     : {top1_ok}")
        print(f"Top-3 doğru     : {top3_ok}")

        print()
        print("HAM EPSILLA İLK 5:")

        for rank, row in enumerate(rows[:5], 1):
            print(
                f"{rank:>2}. "
                f"profile={row.get('profile_code')} | "
                f"section={row.get('section')} | "
                f"@distance={row.get('@distance')}"
            )

        report["tests"].append(
            {
                "number": number,
                "expected_profile": expected,
                "query": query_text,
                "embedding_seconds": query_embedding_s,
                "query_seconds": query_s,
                "vector_l2_norm": vector_norm,
                "top1": top1,
                "top3": top3,
                "top1_correct": top1_ok,
                "top3_correct": top3_ok,
                "raw_top5": [
                    {
                        "profile_code": row.get("profile_code"),
                        "profile_name": row.get("profile_name"),
                        "section": row.get("section"),
                        "distance": row.get("@distance"),
                    }
                    for row in rows[:5]
                ],
            }
        )

    # --------------------------------------------------------
    # 5. ÖZET
    # --------------------------------------------------------

    after_stats = get_container_stats()
    report["container_after"] = after_stats

    python_rss_mb = get_process_rss_mb()

    top1_accuracy = top1_hits / len(TEST_QUERIES)
    top3_accuracy = top3_hits / len(TEST_QUERIES)

    latency_mean = statistics.mean(latencies)
    latency_median = statistics.median(latencies)
    latency_min = min(latencies)
    latency_max = max(latencies)

    summary = {
        "total_queries": len(TEST_QUERIES),
        "top1_hits": top1_hits,
        "top3_hits": top3_hits,
        "top1_accuracy": top1_accuracy,
        "top3_accuracy": top3_accuracy,
        "query_latency_mean_s": latency_mean,
        "query_latency_median_s": latency_median,
        "query_latency_min_s": latency_min,
        "query_latency_max_s": latency_max,
        "python_rss_mb": python_rss_mb,
    }

    report["summary"] = summary

    print_header("5. SONUÇ")

    print(f"Top-1 doğruluk : {top1_hits}/{len(TEST_QUERIES)} = %{top1_accuracy * 100:.1f}")
    print(f"Top-3 doğruluk : {top3_hits}/{len(TEST_QUERIES)} = %{top3_accuracy * 100:.1f}")
    print()
    print(f"Ort. sorgu     : {latency_mean:.4f} s")
    print(f"Medyan sorgu   : {latency_median:.4f} s")
    print(f"En hızlı       : {latency_min:.4f} s")
    print(f"En yavaş       : {latency_max:.4f} s")

    if python_rss_mb is not None:
        print(f"Python RSS     : {python_rss_mb:.2f} MB")

    print()
    print(f"Epsilla önce   : {before_stats}")
    print(f"Epsilla sonra  : {after_stats}")

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Rapor           : {report_path}")
    print()
    print("TEST TAMAMLANDI.")


if __name__ == "__main__":
    main()
