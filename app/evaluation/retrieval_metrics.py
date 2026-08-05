"""Bilgi getirme katmanı için saf ölçüm fonksiyonları.

Tüm fonksiyonlar:
- bağımsız ve test edilebilir
- boş giriş için güvenli
- sıfıra bölme korumalı
- tür ipuçları içeriyor (Python 3.12 uyumlu)

Kullanım
--------
::

    from app.evaluation.retrieval_metrics import (
        precision_at_k,
        recall_at_k,
        ndcg_at_k,
        mean_reciprocal_rank,
        profile_precision_at_k,
        duplicate_ikn_count,
    )
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Precision @ K
# ---------------------------------------------------------------------------


def precision_at_k(
    retrieved: Sequence[str],
    relevant: set[str],
    k: int,
) -> float:
    """İlk K sonuç içindeki kesinlik oranı.

    Precision@K = |retrieved[:K] ∩ relevant| / K

    Args:
        retrieved: Alınan sonuçların İKN listesi (sıralı).
        relevant:  İlgili İKN kümesi.
        k:         İlk kaç sonucun değerlendirileceği.

    Returns:
        0.0–1.0 arasında kesinlik oranı.

    Raises:
        ValueError: k ≤ 0 ise.
    """
    if k <= 0:
        raise ValueError(f"k pozitif olmalıdır, alınan: {k}")
    if not retrieved or not relevant:
        return 0.0

    top_k = [str(ikn) for ikn in retrieved[:k]]
    hits = sum(1 for ikn in top_k if ikn in relevant)
    return hits / k


# ---------------------------------------------------------------------------
# Recall @ K
# ---------------------------------------------------------------------------


def recall_at_k(
    retrieved: Sequence[str],
    relevant: set[str],
    k: int,
) -> float:
    """İlk K sonuç içindeki geri çağırma oranı.

    Recall@K = |retrieved[:K] ∩ relevant| / |relevant|

    Args:
        retrieved: Alınan sonuçların İKN listesi (sıralı).
        relevant:  İlgili İKN kümesi.
        k:         İlk kaç sonucun değerlendirileceği.

    Returns:
        0.0–1.0 arasında geri çağırma oranı.

    Raises:
        ValueError: k ≤ 0 ise.
    """
    if k <= 0:
        raise ValueError(f"k pozitif olmalıdır, alınan: {k}")
    if not relevant:
        return 0.0
    if not retrieved:
        return 0.0

    top_k = [str(ikn) for ikn in retrieved[:k]]
    hits = sum(1 for ikn in top_k if ikn in relevant)
    return hits / len(relevant)


# ---------------------------------------------------------------------------
# Reciprocal Rank
# ---------------------------------------------------------------------------


def reciprocal_rank(
    retrieved: Sequence[str],
    relevant: set[str],
) -> float:
    """İlk doğru sonucun sıra numarasının tersi.

    RR = 1 / rank(first_relevant)

    Args:
        retrieved: Alınan sonuçların İKN listesi (sıralı).
        relevant:  İlgili İKN kümesi.

    Returns:
        0.0–1.0 arasında RR değeri. Hiç doğru sonuç yoksa 0.0.
    """
    if not retrieved or not relevant:
        return 0.0

    relevant_str = {str(r) for r in relevant}
    for rank, ikn in enumerate(retrieved, start=1):
        if str(ikn) in relevant_str:
            return 1.0 / rank

    return 0.0


# ---------------------------------------------------------------------------
# Mean Reciprocal Rank
# ---------------------------------------------------------------------------


def mean_reciprocal_rank(
    queries_results: Sequence[tuple[Sequence[str], set[str]]],
) -> float:
    """Tüm sorgular için ortalama RR.

    MRR = (1/|Q|) * Σ RR(q)

    Args:
        queries_results: (retrieved_list, relevant_set) çiftlerinin listesi.

    Returns:
        0.0–1.0 arasında MRR değeri. Boş liste için 0.0.
    """
    if not queries_results:
        return 0.0

    rrs = [reciprocal_rank(retrieved, relevant) for retrieved, relevant in queries_results]
    return sum(rrs) / len(rrs)


# ---------------------------------------------------------------------------
# DCG @ K
# ---------------------------------------------------------------------------


def dcg_at_k(
    retrieved: Sequence[str],
    graded_relevance: dict[str, int],
    k: int,
) -> float:
    """İlk K sonuç için Discounted Cumulative Gain.

    DCG@K = Σ (2^rel_i - 1) / log2(i + 1)  for i in 1..K

    Args:
        retrieved:         Alınan sonuçların İKN listesi (sıralı).
        graded_relevance:  İKN → derece (tam sayı, genellikle 0–3).
        k:                 İlk kaç sonucun değerlendirileceği.

    Returns:
        DCG değeri (≥ 0.0).

    Raises:
        ValueError: k ≤ 0 ise.
    """
    if k <= 0:
        raise ValueError(f"k pozitif olmalıdır, alınan: {k}")
    if not retrieved:
        return 0.0

    dcg = 0.0
    for i, ikn in enumerate(retrieved[:k], start=1):
        rel = graded_relevance.get(str(ikn), 0)
        if rel > 0:
            dcg += (2**rel - 1) / math.log2(i + 1)
    return dcg


# ---------------------------------------------------------------------------
# nDCG @ K
# ---------------------------------------------------------------------------


def ndcg_at_k(
    retrieved: Sequence[str],
    graded_relevance: dict[str, int],
    k: int,
) -> float:
    """İlk K sonuç için Normalized Discounted Cumulative Gain.

    nDCG@K = DCG@K / IDCG@K

    İdeal DCG; mevcut ilgili belgelerin en iyi sıralamada elde
    edilebilecek maksimum DCG'sidir.

    Args:
        retrieved:         Alınan sonuçların İKN listesi (sıralı).
        graded_relevance:  İKN → derece (tam sayı, genellikle 0–3).
        k:                 İlk kaç sonucun değerlendirileceği.

    Returns:
        0.0–1.0 arasında nDCG değeri.

    Raises:
        ValueError: k ≤ 0 ise.
    """
    if k <= 0:
        raise ValueError(f"k pozitif olmalıdır, alınan: {k}")

    actual_dcg = dcg_at_k(retrieved, graded_relevance, k)
    if actual_dcg == 0.0:
        # Hiçbir ilgili sonuç alınmadı → ideal de boşsa 0, değilse 0
        # Gerçek DCG sıfır ise zaten nDCG sıfırdır
        return 0.0

    # İdeal sıralama: ilgililiklere göre azalan
    ideal_order = sorted(
        graded_relevance.keys(),
        key=lambda x: graded_relevance[x],
        reverse=True,
    )
    ideal_dcg = dcg_at_k(ideal_order, graded_relevance, k)

    if ideal_dcg == 0.0:
        return 0.0

    return min(actual_dcg / ideal_dcg, 1.0)


# ---------------------------------------------------------------------------
# Profil kesinliği @ K
# ---------------------------------------------------------------------------


def profile_precision_at_k(
    retrieved_profile_codes: Sequence[Sequence[str]],
    expected_groups: Sequence[str],
    k: int,
) -> float:
    """İlk K sonuçta beklenen profil grubuna uyum oranı.

    Her sonuç için profil kodlarının üç harflik ön eki alınır (AUS-01 → AUS)
    ve beklenen gruplarla kesişimi kontrol edilir.

    Args:
        retrieved_profile_codes: Her sonuç için profil kodları listesi.
        expected_groups:         Beklenen İSBAK profil grupları (örn. ["AUS"]).
        k:                       İlk kaç sonucun değerlendirileceği.

    Returns:
        0.0–1.0 arasında profil kesinliği.

    Raises:
        ValueError: k ≤ 0 ise.
    """
    if k <= 0:
        raise ValueError(f"k pozitif olmalıdır, alınan: {k}")
    if not retrieved_profile_codes or not expected_groups:
        return 0.0

    expected_set: set[str] = {str(g).strip()[:3].upper() for g in expected_groups}
    top_k = list(retrieved_profile_codes[:k])

    if not top_k:
        return 0.0

    hits = 0
    for codes in top_k:
        result_groups: set[str] = {str(c).strip()[:3].upper() for c in codes if c}
        if result_groups & expected_set:
            hits += 1

    return hits / len(top_k)


# ---------------------------------------------------------------------------
# Tekrarlı İKN sayısı
# ---------------------------------------------------------------------------


def duplicate_ikn_count(retrieved: Sequence[str]) -> int:
    """Sonuç listesindeki tekrarlı İKN sayısını döndürür.

    Aynı İKN birden fazla kez varsa her fazlası bir tekrar sayılır.
    İdeal sonuçta bu değer 0 olmalıdır.

    Args:
        retrieved: Alınan sonuçların İKN listesi.

    Returns:
        Tekrarlı giriş sayısı (≥ 0).
    """
    seen: set[str] = set()
    duplicates = 0
    for ikn in retrieved:
        ikn_str = str(ikn)
        if ikn_str in seen:
            duplicates += 1
        else:
            seen.add(ikn_str)
    return duplicates


# ---------------------------------------------------------------------------
# Sorgu bazlı özet
# ---------------------------------------------------------------------------


def evaluate_query(
    retrieved_ikns: list[str],
    retrieved_profile_codes: list[list[str]],
    relevant_ikns: set[str],
    graded_relevance: dict[str, int],
    expected_profile_groups: list[str],
    k_values: tuple[int, ...] = (5, 10),
) -> dict[str, float]:
    """Tek bir sorgu için tüm metrikleri hesaplar.

    Args:
        retrieved_ikns:         Geri dönen İKN'ler (sıralı).
        retrieved_profile_codes: Her sonucun profil kodları.
        relevant_ikns:          İlgili İKN kümesi (boş olabilir).
        graded_relevance:       İKN → derece (boş olabilir).
        expected_profile_groups: Beklenen profil grupları.
        k_values:               Hesaplanacak K değerleri.

    Returns:
        Metrik adı → değer sözlüğü.
    """
    metrics: dict[str, float] = {}
    has_relevance = bool(relevant_ikns)

    for k in k_values:
        k_str = str(k)
        if has_relevance:
            metrics[f"precision@{k_str}"] = precision_at_k(retrieved_ikns, relevant_ikns, k)
            metrics[f"recall@{k_str}"] = recall_at_k(retrieved_ikns, relevant_ikns, k)
            metrics[f"ndcg@{k_str}"] = ndcg_at_k(retrieved_ikns, graded_relevance, k)
        else:
            metrics[f"precision@{k_str}"] = float("nan")
            metrics[f"recall@{k_str}"] = float("nan")
            metrics[f"ndcg@{k_str}"] = float("nan")

        metrics[f"profile_precision@{k_str}"] = profile_precision_at_k(
            retrieved_profile_codes, expected_profile_groups, k
        )

    if has_relevance:
        metrics["rr"] = reciprocal_rank(retrieved_ikns, relevant_ikns)
    else:
        metrics["rr"] = float("nan")

    metrics["duplicate_ikn_count"] = float(duplicate_ikn_count(retrieved_ikns))
    return metrics


def aggregate_metrics(
    per_query_metrics: list[dict[str, float]],
    k_values: tuple[int, ...] = (5, 10),
) -> dict[str, float]:
    """Sorgu başına metrikleri ortalayarak genel sonuçları hesaplar.

    ``nan`` değerler ortalamadan hariç tutulur.
    """
    if not per_query_metrics:
        return {}

    agg: dict[str, float] = {}
    all_keys: set[str] = set()
    for m in per_query_metrics:
        all_keys.update(m.keys())

    for key in sorted(all_keys):
        values = [m[key] for m in per_query_metrics if key in m and not math.isnan(m[key])]
        agg[key] = sum(values) / len(values) if values else float("nan")

    # MRR: rr'nin ortalaması
    rrs = [m["rr"] for m in per_query_metrics if "rr" in m and not math.isnan(m["rr"])]
    agg["mrr"] = sum(rrs) / len(rrs) if rrs else float("nan")

    return agg


__all__ = [
    "aggregate_metrics",
    "dcg_at_k",
    "duplicate_ikn_count",
    "evaluate_query",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "precision_at_k",
    "profile_precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]
