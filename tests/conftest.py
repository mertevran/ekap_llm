"""Ortak test fikstürleri."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK))


@pytest.fixture(scope="session")
def ekap_db() -> Path:
    yol = KOK.parent / "VerilerEtiketliveri" / "ekap.db"
    if not yol.exists():
        pytest.skip(f"Yerel veri yok: {yol}")
    return yol


@pytest.fixture(scope="session")
def depo(ekap_db):
    from app.database.sqlite_depo import SqliteIhaleDeposu

    return SqliteIhaleDeposu(ekap_db)


@pytest.fixture(scope="session")
def karar_seti() -> Path:
    yol = KOK / "evaluation" / "karar-seti-v1.csv"
    if not yol.exists():
        pytest.skip(f"Karar seti yok: {yol}")
    return yol
