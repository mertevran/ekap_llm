from __future__ import annotations

from app.database import get_connection


def main() -> int:
    print("EKAP PostgreSQL Bağlantı Kontrolü")
    print("-" * 45)

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        current_database(),
                        current_user,
                        version()
                    """
                )

                database_name, current_user, version = cursor.fetchone()

                cursor.execute("SELECT 1")
                health_value = cursor.fetchone()[0]

        print(f"Veritabanı : {database_name}")
        print(f"Kullanıcı  : {current_user}")
        print(f"Sunucu     : {version}")
        print(f"SELECT 1   : {health_value}")
        print()
        print("PostgreSQL bağlantısı başarılı.")

        return 0

    except Exception as exc:
        print()
        print(f"[HATA] PostgreSQL bağlantısı kurulamadı: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
