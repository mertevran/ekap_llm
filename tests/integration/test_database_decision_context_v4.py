from __future__ import annotations

import pytest

from app.database import TenderRepository
from app.decision.tender_source_context import (
    TenderSourceContextBuilder,
    render_database_tender_context,
)

pytestmark = [pytest.mark.integration, pytest.mark.external]


def test_live_database_record_builds_decision_context(
    require_database: None,
) -> None:
    repository = TenderRepository()
    schema = repository.validate_required_schema()
    active = repository.get_active_tenders(limit=1)
    if not active:
        pytest.skip("Canlı veritabanında aktif ihale bulunamadı.")

    tender = repository.get_by_ikn(active[0].ikn)
    source = TenderSourceContextBuilder().build(tender, profile_signals={})
    rendered = render_database_tender_context(source, max_chars=30_000)

    assert schema["schema"] == "public"
    assert source.tender.ikn == active[0].ikn
    assert source.validation_context(
        profile_signals={}, retrieval_score=0.0
    ).source_origin == "postgresql"
    assert f"İKN: {tender.ikn}" in rendered
    assert "Gerçek ihale türü:" in rendered
    assert "[BÜTÜN OKAS KAYITLARI]" in rendered
    assert "[BÜTÜN TEKNİK ÖZELLİKLER]" in rendered
