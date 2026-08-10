import pytest

from app.database import get_connection

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
]


def test_database_connection_and_select_one(
    require_database: None,
) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()

    assert result is not None
    assert result[0] == 1


def test_expected_database_name(
    require_database: None,
) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            result = cursor.fetchone()

    assert result is not None
    assert result[0] == "ekap_db"
