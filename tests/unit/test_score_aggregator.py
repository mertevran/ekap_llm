"""Unit testler — ScoreAggregator."""

from __future__ import annotations


def _make_settings(**overrides):
    """Test için minimal settings nesnesi."""
    from app.config.isbak_rag_settings import IsbakRagSettings
    return IsbakRagSettings(**overrides)


def _aggregator(settings=None):
    from app.matching.score_aggregator import ScoreAggregator
    return ScoreAggregator(settings)


def test_default_weights_sum_to_one():
    s = _make_settings()
    total = (
        s.weight_max_chunk
        + s.weight_top_chunks
        + s.weight_section_diversity
        + s.weight_okas
        + s.weight_title
    )
    assert abs(total - 1.0) < 1e-6


def test_score_range_0_to_1():
    agg = _aggregator()
    result = agg.compute(
        raw_scores=[0.9, 0.8, 0.7],
        section_types=["qualification", "technical_requirements", "announcement"],
        okas_codes=["724100"],
        query_terms=("yazılım", "geliştirme"),
        tender_name="Yazılım Geliştirme İhalesi",
    )
    assert 0.0 <= result.final_score <= 1.0


def test_empty_scores_returns_zero():
    agg = _aggregator()
    result = agg.compute(
        raw_scores=[],
        section_types=[],
        okas_codes=[],
        query_terms=(),
    )
    assert result.final_score == 0.0


def test_negative_penalty_does_not_go_below_zero():
    agg = _aggregator()
    result = agg.compute(
        raw_scores=[0.01],  # çok düşük temel skor
        section_types=["okas"],
        okas_codes=[],
        query_terms=("hedef",),
        negative_terms=["hedef", "hedef2", "hedef3", "hedef4", "hedef5"],
    )
    assert result.final_score >= 0.0


def test_section_diversity_uses_known_section_types():
    from app.vector_store.faiss_vector_reader import KNOWN_SECTION_TYPES
    agg = _aggregator()
    # Bilinmeyen bölüm türleri çeşitlilik katkısı sağlamamalı
    result_unknown = agg.compute(
        raw_scores=[0.8],
        section_types=["unknown_section", "another_unknown"],
        okas_codes=[],
        query_terms=(),
    )
    result_known = agg.compute(
        raw_scores=[0.8],
        section_types=list(KNOWN_SECTION_TYPES)[:3],
        okas_codes=[],
        query_terms=(),
    )
    assert result_known.section_diversity >= result_unknown.section_diversity


def test_okas_support_prefix_match():
    agg = _aggregator()
    result = agg.compute(
        raw_scores=[0.5],
        section_types=["okas"],
        okas_codes=["724100", "486100"],
        query_terms=("yazılım",),
        profile_okas_prefixes=["72"],  # 724100 ile eşleşmeli
    )
    assert result.okas_support == 1.0


def test_okas_support_no_match():
    agg = _aggregator()
    result = agg.compute(
        raw_scores=[0.5],
        section_types=["okas"],
        okas_codes=["486100"],
        query_terms=("yazılım",),
        profile_okas_prefixes=["72"],  # eşleşme yok
    )
    assert result.okas_support == 0.0


def test_title_support():
    agg = _aggregator()
    result = agg.compute(
        raw_scores=[0.5],
        section_types=[],
        okas_codes=[],
        query_terms=("yazılım", "geliştirme"),
        tender_name="Yazılım Geliştirme Hizmet Alımı",
    )
    assert result.title_support > 0.0


def test_section_diversity_max_at_three_or_more():
    agg = _aggregator()
    result = agg.compute(
        raw_scores=[0.8],
        section_types=["qualification", "technical_requirements", "announcement", "okas"],
        okas_codes=[],
        query_terms=(),
    )
    assert result.section_diversity == 1.0


def test_section_diversity_one_section():
    agg = _aggregator()
    result = agg.compute(
        raw_scores=[0.8],
        section_types=["qualification"],
        okas_codes=[],
        query_terms=(),
    )
    # 1/3 ≈ 0.333
    assert abs(result.section_diversity - 1 / 3) < 0.01
