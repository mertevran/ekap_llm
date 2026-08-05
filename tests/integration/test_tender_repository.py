import pytest

from app.database import TenderRepository

TEST_IKN = "2026/1212847"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
]


def test_existing_tender_can_be_loaded(
    require_database: None,
) -> None:
    repository = TenderRepository()
    tender = repository.get_by_ikn(TEST_IKN)

    assert tender.id
    assert tender.ikn == TEST_IKN
    assert tender.adi


def test_child_records_belong_to_same_tender(
    require_database: None,
) -> None:
    repository = TenderRepository()
    tender = repository.get_by_ikn(TEST_IKN)

    announcement_ids_are_valid = all(
        item.tender_id == tender.id
        for item in tender.announcements
    )
    characteristic_ids_are_valid = all(
        item.tender_id == tender.id
        for item in tender.characteristics
    )
    okas_ids_are_valid = all(
        item.tender_id == tender.id
        for item in tender.okas_codes
    )

    assert announcement_ids_are_valid
    assert characteristic_ids_are_valid
    assert okas_ids_are_valid


def test_tender_contains_child_data(
    require_database: None,
) -> None:
    repository = TenderRepository()
    tender = repository.get_by_ikn(TEST_IKN)

    total_child_records = (
        len(tender.announcements)
        + len(tender.characteristics)
        + len(tender.okas_codes)
    )

    assert total_child_records > 0
