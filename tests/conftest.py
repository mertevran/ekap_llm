from __future__ import annotations

import psycopg
import pytest

from app.database import get_connection


@pytest.fixture(scope="session")
def require_database() -> None:
    """
    Uzak PostgreSQL erişimini bir kez kontrol eder.

    Veritabanı erişilebilir değilse harici veritabanı testlerini
    başarısız saymak yerine kontrollü olarak atlar.
    """
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()

        if result is None or result[0] != 1:
            pytest.skip(
                "PostgreSQL bağlantı kontrolü beklenen sonucu vermedi."
            )

    except (
        psycopg.OperationalError,
        psycopg.errors.ConnectionTimeout,
        TimeoutError,
        OSError,
    ) as exc:
        pytest.skip(
            "Uzak PostgreSQL veritabanına ulaşılamadığı için "
            f"entegrasyon testi atlandı: {exc}"
        )
