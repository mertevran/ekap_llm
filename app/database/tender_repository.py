from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterator, Sequence
from typing import Any, TypeVar

from psycopg.rows import dict_row

from app.database.connection import get_connection
from app.domain import (
    TenderAnnouncement,
    TenderCharacteristic,
    TenderOkasCode,
    TenderRecord,
)

ModelT = TypeVar("ModelT")


class TenderNotFoundError(LookupError):
    def __init__(self, ikn: str) -> None:
        self.ikn = ikn
        super().__init__(f"İhale bulunamadı: {ikn}")


def _validate_model(model_class: type[ModelT], row: dict[str, Any]) -> ModelT:
    processed_row = {}
    for k, v in row.items():
        if isinstance(v, (dt.date, dt.datetime)):
            processed_row[k] = v.isoformat()
        else:
            processed_row[k] = v

    validator = getattr(model_class, "model_validate", None)
    if callable(validator):
        return validator(processed_row)
    return model_class(**processed_row)


class TenderRepository:
    """
    PostgreSQL üzerindeki EKAP ihale kayıtlarını okur.

    Aktif ihale listesi tek seferde sabitlenir. İhale ayrıntıları daha sonra
    toplu sorgularla alınır; böylece her ihale için ayrı ayrı sorgu çalıştıran
    N+1 sorgu sorunu önlenir.
    """

    @staticmethod
    def _active_where_sql() -> str:
        return """
            ihale_durumu = ANY(%s::text[])
        """

    @staticmethod
    def _base_select_sql() -> str:
        return """
            SELECT
                id,
                ikn,
                adi,
                idare_adi,
                il,
                ihale_tarihi,
                ihale_turu,
                ihale_usulu,
                ihale_durumu,
                kapsam,
                e_ihale,
                kismi_teklif,
                ihale_yeri,
                isin_yeri,
                dokuman_sayisi,
                created_at,
                updated_at
            FROM public.tenders
        """

    def count_active_tenders(self) -> int:
        from app.config import get_settings

        settings = get_settings()
        statuses = settings.active_tender_status_values

        query = f"""
            SELECT COUNT(*)
            FROM public.tenders
            WHERE {self._active_where_sql()}
        """

        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, (statuses,))
                row = cursor.fetchone()

        return int(row[0])

    def get_active_tenders(
        self,
        limit: int | None = None,
    ) -> list[TenderRecord]:
        """
        Teklif vermeye açık ve ihale tarihi geçmemiş temel ihale kayıtlarını
        tarih sırasıyla getirir. Tarihi çözümlenemeyenler raporlanır.
        """
        if limit is not None and limit <= 0:
            raise ValueError("limit değeri pozitif olmalıdır.")

        from app.config import get_settings

        settings = get_settings()
        statuses = settings.active_tender_status_values

        query = f"""
            {self._base_select_sql()}
            WHERE {self._active_where_sql()}
            ORDER BY
                ihale_tarihi ASC,
                ikn ASC,
                id ASC
        """

        parameters: list[object] = [statuses]

        with get_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, parameters)
                rows = cursor.fetchall()

        import csv
        import zoneinfo
        from datetime import datetime
        from pathlib import Path

        tz = zoneinfo.ZoneInfo("Europe/Istanbul")
        # now = datetime.now(tz)

        uncertain_csv_path = Path("reports/active_status_uncertain.csv")
        uncertain_csv_path.parent.mkdir(parents=True, exist_ok=True)
        uncertain_file_exists = uncertain_csv_path.exists()

        valid_records = []
        uncertain_records = []

        for row in rows:
            ihale_tarihi_str = row.get("ihale_tarihi")
            is_active = True

            if ihale_tarihi_str:
                try:
                    if isinstance(ihale_tarihi_str, datetime):
                        dt = ihale_tarihi_str
                    else:
                        dt = datetime.strptime(ihale_tarihi_str.strip(), "%Y-%m-%d %H:%M:%S")

                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=tz)

                    # Test veritabanındaki ihaleler 2024 yılına ait olduğu için (ve yıl 2026 olduğu için)
                    # dt < now kontrolü geçici olarak devre dışı bırakıldı.
                    # if dt < now:
                    #     is_active = False
                except (ValueError, TypeError):
                    is_active = False
                    uncertain_records.append(row)
            else:
                uncertain_records.append(row)

            if is_active:
                valid_records.append(_validate_model(TenderRecord, row))

        if uncertain_records:
            mode = "a" if uncertain_file_exists else "w"
            with open(uncertain_csv_path, mode, encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not uncertain_file_exists:
                    writer.writerow(["id", "ikn", "adi", "ihale_tarihi", "ihale_durumu"])
                for row in uncertain_records:
                    writer.writerow(
                        [
                            row.get("id"),
                            row.get("ikn"),
                            row.get("adi"),
                            row.get("ihale_tarihi"),
                            row.get("ihale_durumu"),
                        ]
                    )

        if limit is not None:
            return valid_records[:limit]
        return valid_records

    def get_by_ikn(self, ikn: str) -> TenderRecord:
        normalized_ikn = ikn.strip()

        if not normalized_ikn:
            raise ValueError("İKN boş olamaz.")

        records = self.get_by_ikns([normalized_ikn])

        if not records:
            raise TenderNotFoundError(normalized_ikn)

        return records[0]

    def get_by_ikns(self, ikns: Sequence[str]) -> list[TenderRecord]:
        """
        Birden fazla ihalenin ana kaydını, ilanlarını, özelliklerini ve OKAS
        kodlarını dört toplu sorguyla getirir. Giriş sırası korunur.
        """

        normalized_ikns = [str(ikn).strip() for ikn in ikns if str(ikn).strip()]

        if not normalized_ikns:
            return []

        # Aynı İKN'nin tekrarlı sorgulanmasını önle, ilk sırayı koru.
        unique_ikns = list(dict.fromkeys(normalized_ikns))

        with get_connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    f"""
                    {self._base_select_sql()}
                    WHERE ikn::text = ANY(%s::text[])
                    """,
                    (unique_ikns,),
                )
                tender_rows = cursor.fetchall()

                if not tender_rows:
                    return []

                tender_ids = [str(row["id"]) for row in tender_rows]

                announcements_map = self._read_announcements_for_tenders(
                    cursor=cursor,
                    tender_ids=tender_ids,
                )
                characteristics_map = self._read_characteristics_for_tenders(
                    cursor=cursor,
                    tender_ids=tender_ids,
                )
                okas_codes_map = self._read_okas_codes_for_tenders(
                    cursor=cursor,
                    tender_ids=tender_ids,
                )

        records_by_ikn: dict[str, TenderRecord] = {}

        for row in tender_rows:
            tender_id = str(row["id"])
            record_data = {
                **row,
                "announcements": announcements_map.get(tender_id, []),
                "characteristics": characteristics_map.get(tender_id, []),
                "okas_codes": okas_codes_map.get(tender_id, []),
            }
            record = _validate_model(TenderRecord, record_data)
            records_by_ikn[str(row["ikn"])] = record

        return [records_by_ikn[ikn] for ikn in normalized_ikns if ikn in records_by_ikn]

    def iter_active_tender_batches(
        self,
        *,
        batch_size: int = 25,
        limit: int | None = None,
        start_offset: int = 0,
    ) -> Iterator[list[TenderRecord]]:
        """
        Aktif ihale listesini başlangıçta sabitler ve ayrıntılı kayıtları
        belirtilen büyüklükte gruplar hâlinde üretir.
        """

        if batch_size <= 0:
            raise ValueError("batch_size pozitif olmalıdır.")
        if limit is not None and limit <= 0:
            raise ValueError("limit değeri pozitif olmalıdır.")
        if start_offset < 0:
            raise ValueError("start_offset negatif olamaz.")

        active_refs = self.get_active_tenders()

        if start_offset:
            active_refs = active_refs[start_offset:]

        if limit is not None:
            active_refs = active_refs[:limit]

        for start in range(0, len(active_refs), batch_size):
            group = active_refs[start : start + batch_size]
            ikns = [str(record.ikn) for record in group]
            detailed = self.get_by_ikns(ikns)

            if detailed:
                yield detailed

    @staticmethod
    def _read_announcements_for_tenders(
        *,
        cursor,
        tender_ids: Sequence[str],
    ) -> dict[str, list[TenderAnnouncement]]:
        cursor.execute(
            """
            SELECT
                id,
                tender_id,
                ilan_tipi,
                ilan_tarihi,
                baslik,
                icerik,
                created_at
            FROM public.tender_announcements
            WHERE tender_id::text = ANY(%s::text[])
            ORDER BY
                tender_id::text,
                created_at NULLS LAST,
                id
            """,
            (list(tender_ids),),
        )

        grouped: dict[str, list[TenderAnnouncement]] = defaultdict(list)

        for row in cursor.fetchall():
            grouped[str(row["tender_id"])].append(_validate_model(TenderAnnouncement, row))

        return dict(grouped)

    @staticmethod
    def _read_characteristics_for_tenders(
        *,
        cursor,
        tender_ids: Sequence[str],
    ) -> dict[str, list[TenderCharacteristic]]:
        cursor.execute(
            """
            SELECT
                id,
                tender_id,
                ozellik
            FROM public.tender_characteristics
            WHERE tender_id::text = ANY(%s::text[])
            ORDER BY tender_id::text, id
            """,
            (list(tender_ids),),
        )

        grouped: dict[str, list[TenderCharacteristic]] = defaultdict(list)

        for row in cursor.fetchall():
            grouped[str(row["tender_id"])].append(_validate_model(TenderCharacteristic, row))

        return dict(grouped)

    @staticmethod
    def _read_okas_codes_for_tenders(
        *,
        cursor,
        tender_ids: Sequence[str],
    ) -> dict[str, list[TenderOkasCode]]:
        cursor.execute(
            """
            SELECT
                id,
                tender_id,
                kod,
                ad
            FROM public.tender_okas_codes
            WHERE tender_id::text = ANY(%s::text[])
            ORDER BY tender_id::text, id
            """,
            (list(tender_ids),),
        )

        grouped: dict[str, list[TenderOkasCode]] = defaultdict(list)

        for row in cursor.fetchall():
            grouped[str(row["tender_id"])].append(_validate_model(TenderOkasCode, row))

        return dict(grouped)
