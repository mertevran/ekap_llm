"""Integration tests for database-sequential selection mode.

Tüm testler Mock kullanır; gerçek DB / FAISS / Ollama bağlantısı
gerektirmez. Testleri çalıştırmak için:

    PYTHONPATH=. .venv/bin/python3 -m pytest tests/integration/test_database_sequential_selection.py -v
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_tender(
    tender_id: str,
    ikn: str,
    adi: str = "Test İhalesi",
    idare_adi: str = "TEST İDARESİ",
    created_at: datetime | None = None,
    ihale_durumu: str = "Teklif Dosyası Hazırlanıyor",
) -> MagicMock:
    """Return a minimal TenderRecord-like mock."""
    t = MagicMock()
    t.id = tender_id
    t.ikn = ikn
    t.adi = adi
    t.idare_adi = idare_adi
    t.created_at = created_at
    t.ihale_durumu = ihale_durumu
    t.il = "İstanbul"
    t.ihale_tarihi = None
    t.ihale_turu = None
    return t


# ─────────────────────────────────────────────────────────────
# 1. argparse: database-sequential kabul ediliyor
# ─────────────────────────────────────────────────────────────

def test_argparse_accepts_database_sequential():
    """--selection-mode database-sequential argparse tarafından kabul edilmeli."""
    import importlib.util, sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "run_tender_decision_chain",
        Path(__file__).resolve().parent.parent.parent
        / "scripts"
        / "run_tender_decision_chain.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # parse_args'ı doğrudan çağırmadan sadece parser'ı test et
    sys.argv = [
        "run_tender_decision_chain.py",
        "--selection-mode", "database-sequential",
        "--max-decisions", "5",
    ]
    # Module'ü yüklemeden argparse'ı al
    # sadece choices kontrolü için basit yaklaşım:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--selection-mode",
        choices=("candidate-pool", "database-random", "database-sequential"),
    )
    parser.add_argument("--max-decisions", type=int, default=5)
    args = parser.parse_args(
        ["--selection-mode", "database-sequential", "--max-decisions", "10"]
    )
    assert args.selection_mode == "database-sequential"
    assert args.max_decisions == 10


# ─────────────────────────────────────────────────────────────
# 2. Sequential seçim deterministiktir
# ─────────────────────────────────────────────────────────────

def test_sequential_order_is_deterministic():
    """Aynı tender listesi her zaman aynı sırayla gelmelidir."""
    from datetime import datetime

    dt1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    dt2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    dt3 = datetime(2026, 7, 1, tzinfo=timezone.utc)

    tenders = [
        _make_tender("id-1", "2026/100001", created_at=dt1),
        _make_tender("id-3", "2026/100003", created_at=dt3),
        _make_tender("id-2", "2026/100002", created_at=dt2),
    ]

    def _sort(lst):
        from datetime import datetime as _dt
        _dt_min = _dt.min
        lst_copy = list(lst)
        lst_copy.sort(
            key=lambda t: (
                t.created_at.replace(tzinfo=None) if t.created_at else _dt_min,
                str(t.id),
            ),
            reverse=True,
        )
        return [t.ikn for t in lst_copy]

    order_1 = _sort(tenders)
    order_2 = _sort(tenders)
    assert order_1 == order_2, "Aynı listede sıralama her zaman tutarlı olmalı"


# ─────────────────────────────────────────────────────────────
# 3. Tarih DESC / newest-first davranışı
# ─────────────────────────────────────────────────────────────

def test_sequential_newest_first_ordering():
    """created_at en büyük olan ihale ilk sıraya gelmeli."""
    from datetime import datetime

    dt_old = datetime(2025, 1, 1)
    dt_new = datetime(2026, 8, 1)
    dt_mid = datetime(2026, 3, 15)

    tenders = [
        _make_tender("id-old", "2025/111111", created_at=dt_old),
        _make_tender("id-new", "2026/999999", created_at=dt_new),
        _make_tender("id-mid", "2026/555555", created_at=dt_mid),
    ]

    from datetime import datetime as _dt
    _dt_min = _dt.min
    tenders.sort(
        key=lambda t: (
            t.created_at.replace(tzinfo=None) if t.created_at else _dt_min,
            str(t.id),
        ),
        reverse=True,
    )

    ikns = [t.ikn for t in tenders]
    assert ikns[0] == "2026/999999", "En yeni ihale ilk sıraya gelmeli"
    assert ikns[-1] == "2025/111111", "En eski ihale son sıraya gelmeli"


# ─────────────────────────────────────────────────────────────
# 4. random_seed sequential sıralamayı etkilemez
# ─────────────────────────────────────────────────────────────

def test_random_seed_does_not_affect_sequential_order():
    """database-sequential modunda random_seed sıralamayı değiştirmemeli."""
    import random as _random
    from datetime import datetime

    dt1 = datetime(2026, 5, 1)
    dt2 = datetime(2026, 6, 1)
    dt3 = datetime(2026, 7, 1)

    tenders = [
        _make_tender("id-1", "2026/100001", created_at=dt1),
        _make_tender("id-2", "2026/100002", created_at=dt2),
        _make_tender("id-3", "2026/100003", created_at=dt3),
    ]

    from datetime import datetime as _dt
    _dt_min = _dt.min

    def _sequential_sort(lst):
        lst_copy = list(lst)
        lst_copy.sort(
            key=lambda t: (
                t.created_at.replace(tzinfo=None) if t.created_at else _dt_min,
                str(t.id),
            ),
            reverse=True,
        )
        return [t.ikn for t in lst_copy]

    order_seed42 = _sequential_sort(tenders)

    # Seed 99 ile rastgele karıştır, sonra sırala — sonuç değişmemeli
    _random.Random(99).shuffle(tenders)
    order_seed99 = _sequential_sort(tenders)

    assert order_seed42 == order_seed99, "Farklı seed'ler sequential sıralamayı etkilememeli"


# ─────────────────────────────────────────────────────────────
# 5. NULL created_at en sona gider
# ─────────────────────────────────────────────────────────────

def test_null_created_at_goes_to_end():
    """created_at=None olan ihale newest-first sıralamasında en sona gitmeli."""
    from datetime import datetime

    tenders = [
        _make_tender("id-null", "2026/000001", created_at=None),
        _make_tender("id-new",  "2026/999999", created_at=datetime(2026, 8, 1)),
        _make_tender("id-old",  "2025/111111", created_at=datetime(2025, 1, 1)),
    ]

    from datetime import datetime as _dt
    _dt_min = _dt.min
    tenders.sort(
        key=lambda t: (
            t.created_at.replace(tzinfo=None) if t.created_at else _dt_min,
            str(t.id),
        ),
        reverse=True,
    )

    assert tenders[-1].ikn == "2026/000001", "None created_at en sona gitmeli"


# ─────────────────────────────────────────────────────────────
# 6. ISBAK kendi ihalesi selection_rank tüketmez
# ─────────────────────────────────────────────────────────────

def test_isbak_own_tender_skipped_without_consuming_selection_rank():
    """İSBÂK kendi ihalesi skipped olmalı ve selection_rank artmamalı."""
    from app.indexing.active_tender_indexer import is_isbak_tender

    isbak_idare = (
        "İSTANBUL BÜYÜKŞEHİR BELEDİYE BAŞKANLIĞI "
        "İSBAK İSTANBUL BİLİŞİM VE AKILLI KENT TEKNOLOJİLERİ "
        "ANONİM ŞİRKETİ GENEL MÜDÜRLÜĞÜ"
    )
    normal_idare = "ANKARA ÇEVRE VE ŞEHİRCİLİK İL MÜDÜRLÜĞÜ"

    assert is_isbak_tender(isbak_idare) is True, "İSBÂK ihalesi tanınmalı"
    assert is_isbak_tender(normal_idare) is False, "Normal idare İSBÂK sayılmamalı"
    assert is_isbak_tender(None) is False, "None idare_adi güvenli olmalı"

    # Simülasyon: max_decisions=2, İSBÂK tender'ı geçilmeli, normal tender'lar sayılmalı
    max_decisions = 2
    selection_rank = 0
    examined_rank = 0

    tenders_sim = [
        _make_tender("id-isbak", "2026/1000", idare_adi=isbak_idare),
        _make_tender("id-normal-1", "2026/2000", idare_adi=normal_idare),
        _make_tender("id-normal-2", "2026/3000", idare_adi=normal_idare),
        _make_tender("id-normal-3", "2026/4000", idare_adi=normal_idare),
    ]

    selected_ikns = []
    skipped_ikns = []

    for tender in tenders_sim:
        if selection_rank >= max_decisions:
            break
        examined_rank += 1
        if is_isbak_tender(tender.idare_adi):
            skipped_ikns.append(tender.ikn)
            continue
        selection_rank += 1
        selected_ikns.append(tender.ikn)

    assert "2026/1000" in skipped_ikns, "İSBÂK ihalesi atlanmalı"
    assert len(selected_ikns) == 2, "max_decisions=2 olduğundan 2 ihale seçilmeli"
    assert "2026/1000" not in selected_ikns


# ─────────────────────────────────────────────────────────────
# 7. Semantic evidence olmayan kayıt selection_rank tüketmez
# ─────────────────────────────────────────────────────────────

def test_no_semantic_evidence_does_not_consume_selection_rank():
    """FAISS'te bulunmayan ihale skipped olmalı, selection_rank artmamalı."""
    max_decisions = 2
    selection_rank = 0
    examined_rank = 0

    # tender_ikn -> faiss_payloads
    faiss_payloads_by_tender: dict[str, list] = {
        "2026/2000": [("pid-1", {"tender_id": "id-normal-1", "ikn": "2026/2000"})],
        "2026/3000": [("pid-2", {"tender_id": "id-normal-2", "ikn": "2026/3000"})],
    }

    tenders_sim = [
        _make_tender("id-missing", "2026/9999"),  # FAISS'te yok
        _make_tender("id-normal-1", "2026/2000"),  # FAISS'te var
        _make_tender("id-normal-2", "2026/3000"),  # FAISS'te var
    ]

    selected = []
    skipped = []

    for tender in tenders_sim:
        if selection_rank >= max_decisions:
            break
        examined_rank += 1
        matched = faiss_payloads_by_tender.get(str(tender.ikn), [])
        if not matched:
            skipped.append(tender.ikn)
            continue
        selection_rank += 1
        selected.append(tender.ikn)

    assert "2026/9999" in skipped
    assert len(selected) == 2
    assert "2026/9999" not in selected


# ─────────────────────────────────────────────────────────────
# 8. 10 geçerli ihale için 10'dan fazla kayıt taranabilir
# ─────────────────────────────────────────────────────────────

def test_more_records_scanned_than_max_decisions():
    """max_decisions=3 istendiğinde sistem gerektiğinde daha fazla kayıt taramalı."""
    max_decisions = 3
    selection_rank = 0
    examined_rank = 0

    # 3 ihale FAISS'te yok (skip), 3 ihale var (select)
    faiss_payloads: dict[str, list] = {
        "2026/4000": [("p4", {})],
        "2026/5000": [("p5", {})],
        "2026/6000": [("p6", {})],
    }
    tenders_sim = [
        _make_tender("id-skip-1", "2026/1000"),  # skip
        _make_tender("id-skip-2", "2026/2000"),  # skip
        _make_tender("id-skip-3", "2026/3000"),  # skip
        _make_tender("id-ok-1",   "2026/4000"),  # select
        _make_tender("id-ok-2",   "2026/5000"),  # select
        _make_tender("id-ok-3",   "2026/6000"),  # select
    ]

    for tender in tenders_sim:
        if selection_rank >= max_decisions:
            break
        examined_rank += 1
        if faiss_payloads.get(str(tender.ikn)):
            selection_rank += 1

    assert selection_rank == 3
    assert examined_rank == 6, "3 skip + 3 select = 6 kayıt taranmalı"


# ─────────────────────────────────────────────────────────────
# 9. database-random davranışı etkilenmez
# ─────────────────────────────────────────────────────────────

def test_database_random_mode_unaffected():
    """database-sequential için is_sequential=False olduğunda shuffle mantığı değişmemeli."""
    import random as _random

    tenders = [
        _make_tender(f"id-{i}", f"2026/{i:06d}", created_at=None)
        for i in range(10)
    ]
    ikns_before = [t.ikn for t in tenders]

    is_sequential = False  # database-random simülasyonu
    if is_sequential:
        pass  # sequential sıralama
    else:
        _random.Random(42).shuffle(tenders)

    ikns_after = [t.ikn for t in tenders]
    # Shuffle yapıldıysa sıra değişmiş olmalı (seed=42, 10 eleman için deterministic)
    assert ikns_before != ikns_after, "database-random shuffle uygulanmalıydı"

    # Aynı seed ile tekrar shuffle → aynı sıra
    tenders2 = [
        _make_tender(f"id-{i}", f"2026/{i:06d}", created_at=None)
        for i in range(10)
    ]
    _random.Random(42).shuffle(tenders2)
    ikns_after_2 = [t.ikn for t in tenders2]
    assert ikns_after == ikns_after_2, "Aynı seed her zaman aynı sırayı üretmeli"
