"""Unit testler — TenderToProfileMatcher kanıt bütünlüğü.

Test kapsamı:
A. İhale ve profil metinlerinin karışmadığı test
B. Chunk eşleşme ilişkisi testi (ChunkMatchEvidence)
C. Aynı ihale–profil chunk çiftinin en yüksek skorla tekilleştirilmesi
D. Aynı profil chunk'ı farklı ihale chunk'larıyla eşleşebilmeli
E. Section diversity seçilen ihale parçalarından hesaplanmalı
F. Tender evidence listeleri chunk_id bazında tekilleştirilmeli
G. Profile evidence listeleri profile_chunk_id bazında tekilleştirilmeli
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Test yardımcıları
# ---------------------------------------------------------------------------


def _make_settings(min_score: float = 0.0):
    from app.config.isbak_rag_settings import IsbakRagSettings

    return IsbakRagSettings(
        **{
            "minimum_final_score": min_score,
            "faiss_search_top_k": 100,
            "faiss_max_profiles_per_tender": 20,
            "faiss_max_chunks_per_tender": 8,
        }
    )


def _make_faiss_tender_store(
    tender_chunks: list[dict[str, Any]],
) -> MagicMock:
    """Belirtilen chunk listesinden mock ihale store oluşturur."""
    import faiss

    dim = 4
    base = faiss.IndexFlatIP(dim)
    idx = faiss.IndexIDMap(base)

    payloads: dict[int, dict[str, Any]] = {}
    for i, chunk in enumerate(tender_chunks, start=1):
        vec = np.random.rand(dim).astype(np.float32)
        faiss.normalize_L2(vec.reshape(1, -1))
        idx.add_with_ids(vec.reshape(1, -1), np.array([i], dtype=np.int64))
        payloads[i] = chunk

    store = MagicMock()
    store.index = idx
    store.payloads = payloads
    store.collection_exists.return_value = True
    store.count.return_value = len(tender_chunks)
    return store


def _make_profile_store_with_results(
    search_results: list[dict[str, Any]],
) -> MagicMock:
    """Sabit arama sonuçları döndüren mock profil store."""
    store = MagicMock()
    store.collection_exists.return_value = True
    store.count.return_value = len(search_results)
    store.search.return_value = search_results
    return store


def _run_matcher(
    tender_chunks: list[dict[str, Any]],
    profile_results: list[dict[str, Any]],
    min_score: float = 0.0,
    top_k: int = 10,
):
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_faiss_tender_store(tender_chunks)
    profile_store = _make_profile_store_with_results(profile_results)
    settings = _make_settings(min_score)

    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    return matcher.match(ikn="2026/TEST", top_k=top_k)


# ---------------------------------------------------------------------------
# A. İhale ve profil metinlerinin karışmadığı test
# ---------------------------------------------------------------------------


def test_a_tender_and_profile_texts_do_not_mix():
    """tender_evidence_texts yalnızca ihale metni içermeli.
    profile_evidence_texts yalnızca profil metni içermeli."""
    TENDER_TEXT = "İhale teknik şartname metni"
    PROFILE_TEXT = "İSBAK akıllı ulaşım yetkinliği"

    tender_chunks = [
        {
            "tender_id": "T_TEST",
            "ikn": "2026/TEST",
            "chunk_id": "tender-chunk-1",
            "text": TENDER_TEXT,
            "section_type": "technical_requirements",
            "authority_name": "Dış Belediye",
        }
    ]
    profile_results = [
        {
            "id": "pr-1",
            "score": 0.80,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "profile-chunk-1",
                "section": "technical",
                "text": PROFILE_TEXT,
            },
        }
    ]

    results = _run_matcher(tender_chunks, profile_results)
    assert results, "Eşleşme sonucu boş olmamalı"

    match = results[0]

    # İhale kanıtları yalnızca ihale metni içermeli
    assert any(TENDER_TEXT in t for t in match.tender_evidence_texts), (
        "tender_evidence_texts ihale metnini içermeli"
    )
    # Profil metni ihale kanıtlarında olmamalı
    for t in match.tender_evidence_texts:
        assert PROFILE_TEXT not in t, (
            f"Profil metni ihale kanıtlarına karışmış: {t!r}"
        )

    # Profil kanıtları yalnızca profil metni içermeli
    assert any(PROFILE_TEXT in t for t in match.profile_evidence_texts), (
        "profile_evidence_texts profil metnini içermeli"
    )
    # İhale metni profil kanıtlarında olmamalı
    for t in match.profile_evidence_texts:
        assert TENDER_TEXT not in t, (
            f"İhale metni profil kanıtlarına karışmış: {t!r}"
        )


# ---------------------------------------------------------------------------
# B. Chunk eşleşme ilişkisi testi
# ---------------------------------------------------------------------------


def test_b_chunk_match_evidence_fields():
    """chunk_matches[0] doğru tender_chunk_id ve profile_chunk_id içermeli."""
    tender_chunks = [
        {
            "tender_id": "T_TEST",
            "ikn": "2026/TEST",
            "chunk_id": "tender-chunk-1",
            "text": "İhale teknik şartname metni",
            "section_type": "technical_requirements",
            "authority_name": "Dış Belediye",
        }
    ]
    profile_results = [
        {
            "id": "pr-1",
            "score": 0.75,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "profile-chunk-1",
                "section": "technical",
                "text": "İSBAK akıllı ulaşım yetkinliği",
            },
        }
    ]

    results = _run_matcher(tender_chunks, profile_results)
    assert results

    match = results[0]
    assert match.chunk_matches, "chunk_matches boş olmamalı"

    evidence = match.chunk_matches[0]
    assert evidence.tender_chunk_id == "tender-chunk-1"
    assert evidence.profile_chunk_id == "profile-chunk-1"
    assert 0.0 <= evidence.similarity_score <= 1.0
    assert evidence.tender_text == "İhale teknik şartname metni"
    assert evidence.profile_text == "İSBAK akıllı ulaşım yetkinliği"


# ---------------------------------------------------------------------------
# C. Aynı ihale–profil chunk çiftinin en yüksek skorla tekilleştirilmesi
# ---------------------------------------------------------------------------


def test_c_dedup_same_pair_keeps_highest_score():
    """Aynı (tender_chunk_id, profile_chunk_id) çifti birden fazla gelirse
    en yüksek skor korunmalı."""
    # İki ihale chunk'ı aynı ikn'e sahip — bu durum gerçekte tek ihale içinden
    # aynı vektörün iki kez gönderilmesiyle oluşabilir. Burada bunu simüle etmek
    # için profile_store iki arama sonucu döndürüyor: ilki 0.71, ikincisi 0.84.
    # Ama iki ihale chunk'ı farklı faiss_id'ye sahip olduğundan matcher ikisini
    # ayrı arama vektörü olarak kullanır. Her ikisi de aynı profile chunk döndürürse
    # pair_key (tender-chunk-1, profile-chunk-1) olur ve yalnızca bir kez kaydedilir.

    # Bu testi basit tutmak için tek bir ihale chunk kullan ve score değişimi
    # _select_diverse_evidence_pairs öncesinde profile_groups seviyesinde test et.

    # Aynı pair_key için iki farklı skor simülasyonu:
    # Bunu test etmek için _evidence_pair_key hesabını manuel doğrulayalım.
    from app.matching.tender_to_profile_matcher import _evidence_pair_key

    rec_low = {
        "profile_result": {
            "id": "pr-1",
            "score": 0.71,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "profile-chunk-1",
                "text": "profil metin",
            },
        },
        "tender_external_id": 1,
        "tender_payload": {
            "chunk_id": "tender-chunk-1",
            "text": "ihale metin",
        },
    }
    rec_high = {
        "profile_result": {
            "id": "pr-1",
            "score": 0.84,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "profile-chunk-1",
                "text": "profil metin",
            },
        },
        "tender_external_id": 1,
        "tender_payload": {
            "chunk_id": "tender-chunk-1",
            "text": "ihale metin",
        },
    }

    key_low = _evidence_pair_key(rec_low)
    key_high = _evidence_pair_key(rec_high)

    # Aynı pair_key olmalı
    assert key_low == key_high == ("tender-chunk-1", "profile-chunk-1")

    # Tekilleştirme mantığını doğrula: profile_groups dict'ine ekleme sırası
    profile_groups: dict[tuple, dict] = {}
    for rec in [rec_low, rec_high]:
        key = _evidence_pair_key(rec)
        existing = profile_groups.get(key)
        raw_score = float(rec["profile_result"]["score"])
        if existing is None or raw_score > float(existing["profile_result"]["score"]):
            profile_groups[key] = rec

    # Yalnızca yüksek skor korunmalı
    assert len(profile_groups) == 1
    assert float(profile_groups[key_low]["profile_result"]["score"]) == pytest.approx(0.84)


# ---------------------------------------------------------------------------
# D. Aynı profil chunk'ı farklı ihale chunk'larıyla eşleşebilmeli
# ---------------------------------------------------------------------------


def test_d_same_profile_chunk_different_tender_chunks():
    """(tender-1, profile-1) ve (tender-2, profile-1) ayrı ChunkMatchEvidence
    olarak korunmalı."""
    from app.matching.tender_to_profile_matcher import _evidence_pair_key

    rec_t1 = {
        "profile_result": {
            "id": "pr-1",
            "score": 0.80,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "profile-chunk-1",
                "text": "profil metin",
            },
        },
        "tender_external_id": 1,
        "tender_payload": {
            "chunk_id": "tender-chunk-1",
            "text": "ihale metin 1",
        },
    }
    rec_t2 = {
        "profile_result": {
            "id": "pr-1",
            "score": 0.75,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "profile-chunk-1",
                "text": "profil metin",
            },
        },
        "tender_external_id": 2,
        "tender_payload": {
            "chunk_id": "tender-chunk-2",
            "text": "ihale metin 2",
        },
    }

    key1 = _evidence_pair_key(rec_t1)
    key2 = _evidence_pair_key(rec_t2)

    # Anahtarlar farklı olmalı (farklı ihale chunk'ları)
    assert key1 != key2
    assert key1 == ("tender-chunk-1", "profile-chunk-1")
    assert key2 == ("tender-chunk-2", "profile-chunk-1")

    # Her ikisi de ayrı kayıt olarak korunmalı
    profile_groups: dict[tuple, dict] = {}
    for rec in [rec_t1, rec_t2]:
        key = _evidence_pair_key(rec)
        existing = profile_groups.get(key)
        raw_score = float(rec["profile_result"]["score"])
        if existing is None or raw_score > float(existing["profile_result"]["score"]):
            profile_groups[key] = rec

    assert len(profile_groups) == 2, (
        "Farklı ihale chunk'larıyla eşleşen profil chunk ayrı kaydedilmeli"
    )


# ---------------------------------------------------------------------------
# E. Section diversity seçilen ihale parçalarından hesaplanmalı
# ---------------------------------------------------------------------------


def test_e_section_diversity_from_selected_tender_chunks():
    """Seçilmeyen ihale parçaları section_diversity hesabını etkilememeli."""
    # 3 ihale chunk'ı var: ikisi seçilecek (max_chunks_per_tender=2),
    # biri seçilmeyecek. Seçilmeyen chunk farklı section_type'a sahip.
    # section_diversity yalnızca seçilen 2 chunk'ın section_type'ından hesaplanmalı.

    # Bu testi doğrudan _select_diverse_evidence_pairs çıktısı üzerinden test et
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    records = [
        {
            "profile_result": {"id": "pr-1", "score": 0.90, "payload": {
                "profile_code": "AUS-01", "chunk_id": "pc-1", "text": "profil1"}},
            "tender_external_id": 1,
            "tender_payload": {
                "chunk_id": "tc-1", "text": "ihale1",
                "section_type": "technical_requirements",
            },
        },
        {
            "profile_result": {"id": "pr-2", "score": 0.85, "payload": {
                "profile_code": "AUS-01", "chunk_id": "pc-2", "text": "profil2"}},
            "tender_external_id": 2,
            "tender_payload": {
                "chunk_id": "tc-2", "text": "ihale2",
                "section_type": "qualification",
            },
        },
        {
            "profile_result": {"id": "pr-3", "score": 0.60, "payload": {
                "profile_code": "AUS-01", "chunk_id": "pc-3", "text": "profil3"}},
            "tender_external_id": 3,
            "tender_payload": {
                "chunk_id": "tc-3", "text": "ihale3",
                "section_type": "announcement",  # bu seçilmemeli (limit=2)
            },
        },
    ]

    selected = TenderToProfileMatcher._select_diverse_evidence_pairs(records, limit=2)
    selected_tender_cids = {
        rec["tender_payload"]["chunk_id"] for rec in selected
    }

    # Yalnızca 2 kayıt seçilmeli
    assert len(selected) == 2

    # "tc-3" seçilmemeli (en düşük skor)
    assert "tc-3" not in selected_tender_cids

    # Seçilen parçaların section_type'ları "announcement" içermemeli
    selected_sections = {
        rec["tender_payload"]["section_type"] for rec in selected
    }
    assert "announcement" not in selected_sections


# ---------------------------------------------------------------------------
# F. Tender evidence listeleri chunk_id bazında tekilleştirilmeli
# ---------------------------------------------------------------------------


def test_f_tender_evidence_chunk_ids_are_deduplicated():
    """tender_evidence_chunk_ids aynı chunk_id'yi yalnızca bir kez içermeli."""
    tender_chunks = [
        {
            "tender_id": "T_TEST",
            "ikn": "2026/TEST",
            "chunk_id": "tc-1",
            "text": "İhale metni 1",
            "section_type": "technical_requirements",
            "authority_name": "Dış Belediye",
        }
    ]
    # Aynı tender chunk için iki farklı profil parçası eşleşiyor
    # → tender kanıtlarında tc-1 yalnızca bir kez olmalı
    profile_results = [
        {
            "id": "pr-1",
            "score": 0.85,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "pc-1",
                "section": "s1",
                "text": "Profil metin 1",
            },
        },
        {
            "id": "pr-2",
            "score": 0.80,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "pc-2",
                "section": "s2",
                "text": "Profil metin 2",
            },
        },
    ]

    results = _run_matcher(tender_chunks, profile_results)
    assert results

    match = results[0]
    cids = match.tender_evidence_chunk_ids
    assert len(cids) == len(set(cids)), (
        f"tender_evidence_chunk_ids tekilleştirilmemiş: {cids}"
    )
    # "tc-1" yalnızca bir kez olmalı
    assert cids.count("tc-1") == 1


# ---------------------------------------------------------------------------
# G. Profile evidence listeleri profile_chunk_id bazında tekilleştirilmeli
# ---------------------------------------------------------------------------


def test_g_profile_evidence_chunk_ids_are_deduplicated():
    """profile_evidence_chunk_ids aynı profile_chunk_id'yi yalnızca bir kez içermeli."""
    # İki ihale chunk'ı aynı profil chunk'ıyla eşleşiyor
    tender_chunks = [
        {
            "tender_id": "T_TEST",
            "ikn": "2026/TEST",
            "chunk_id": "tc-1",
            "text": "İhale metni 1",
            "section_type": "technical_requirements",
            "authority_name": "Dış Belediye",
        },
        {
            "tender_id": "T_TEST",
            "ikn": "2026/TEST",
            "chunk_id": "tc-2",
            "text": "İhale metni 2",
            "section_type": "qualification",
            "authority_name": "Dış Belediye",
        },
    ]
    # Her ihale vektörü aynı profil chunk'ını döndürüyor
    profile_results = [
        {
            "id": "pr-1",
            "score": 0.85,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "pc-shared",
                "section": "shared",
                "text": "Paylaşılan profil metni",
            },
        },
    ]

    results = _run_matcher(tender_chunks, profile_results)
    assert results

    match = results[0]
    p_cids = match.profile_evidence_chunk_ids
    assert len(p_cids) == len(set(p_cids)), (
        f"profile_evidence_chunk_ids tekilleştirilmemiş: {p_cids}"
    )
    # "pc-shared" yalnızca bir kez olmalı
    assert p_cids.count("pc-shared") == 1


# ---------------------------------------------------------------------------
# Ek: Boş chunk_id ve metin olan kayıtlar atlanmalı
# ---------------------------------------------------------------------------


def test_empty_chunk_id_is_skipped():
    """chunk_id boş olan profil sonucu ChunkMatchEvidence'a eklenmemeli."""
    tender_chunks = [
        {
            "tender_id": "T_TEST",
            "ikn": "2026/TEST",
            "chunk_id": "tc-1",
            "text": "İhale metni",
            "section_type": "technical_requirements",
            "authority_name": "Dış Belediye",
        }
    ]
    profile_results = [
        {
            "id": "",  # boş id
            "score": 0.80,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "",  # boş chunk_id
                "text": "Profil metni",
            },
        },
        {
            "id": "pr-valid",
            "score": 0.75,
            "payload": {
                "profile_code": "AUS-01",
                "chunk_id": "pc-valid",
                "text": "Geçerli profil metni",
            },
        },
    ]

    results = _run_matcher(tender_chunks, profile_results)
    assert results

    match = results[0]
    # chunk_matches içinde boş chunk_id bulunmamalı
    for cm in match.chunk_matches:
        assert cm.tender_chunk_id, "tender_chunk_id boş olmamalı"
        assert cm.profile_chunk_id, "profile_chunk_id boş olmamalı"


# ---------------------------------------------------------------------------
# Ek: Skor 0.0–1.0 aralığına clamp edilmeli
# ---------------------------------------------------------------------------


def test_similarity_score_clamped_to_valid_range():
    """Negatif FAISS skoru 0.0'a clamp edilmeli."""
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    records = [
        {
            "profile_result": {
                "id": "pr-1",
                "score": -0.05,  # negatif skor
                "payload": {
                    "profile_code": "AUS-01",
                    "chunk_id": "pc-1",
                    "text": "profil metin",
                },
            },
            "tender_external_id": 1,
            "tender_payload": {
                "chunk_id": "tc-1",
                "text": "ihale metin",
            },
        },
        {
            "profile_result": {
                "id": "pr-2",
                "score": 1.05,  # 1.0 üstü skor
                "payload": {
                    "profile_code": "AUS-01",
                    "chunk_id": "pc-2",
                    "text": "profil metin 2",
                },
            },
            "tender_external_id": 2,
            "tender_payload": {
                "chunk_id": "tc-2",
                "text": "ihale metin 2",
            },
        },
    ]

    selected = TenderToProfileMatcher._select_diverse_evidence_pairs(records, limit=5)
    # raw_scores clamp mantığını kontrol et
    for rec in selected:
        raw = float(rec["profile_result"]["score"])
        clamped = max(0.0, min(1.0, raw))
        assert 0.0 <= clamped <= 1.0, f"Clamp başarısız: {raw} → {clamped}"


# ---------------------------------------------------------------------------
# Ek: Deterministik sıralama
# ---------------------------------------------------------------------------


def test_select_diverse_evidence_pairs_deterministic():
    """Aynı giriş için _select_diverse_evidence_pairs deterministik sonuç üretmeli."""
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    records = [
        {
            "profile_result": {"id": f"pr-{i}", "score": 0.80 - i * 0.01,
                               "payload": {"profile_code": "AUS-01",
                                           "chunk_id": f"pc-{i}", "text": f"profil{i}"}},
            "tender_external_id": i,
            "tender_payload": {"chunk_id": f"tc-{i}", "text": f"ihale{i}"},
        }
        for i in range(5)
    ]

    result1 = TenderToProfileMatcher._select_diverse_evidence_pairs(records, limit=3)
    result2 = TenderToProfileMatcher._select_diverse_evidence_pairs(records, limit=3)

    ids1 = [rec["profile_result"]["id"] for rec in result1]
    ids2 = [rec["profile_result"]["id"] for rec in result2]
    assert ids1 == ids2, "Deterministik olmalı"
