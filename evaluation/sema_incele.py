"""
Canlı Postgres şema keşfi. SADECE OKUMA — sadece katalog sorguları + LIMIT 1 örnek.

    python evaluation/sema_incele.py

=============================================================================
NEDEN
=============================================================================
`denklik_testi.py` ilk denemede şu hatayla patladı:

    operator does not exist: timestamp without time zone ~ unknown
    LINE 4:  AND ihale_tarihi ~ '^[0-9]{2}\\.[0-9]{2}\\.[0-9]{4}...'

Yani canlıda `ihale_tarihi` bir TIMESTAMP kolonu; `postgres_depo.py`'deki
`_AKTIF_KOSUL` ise onu METİN varsayıp regex uyguluyor. O SQL, yerel SQLite
kopyasının şemasına göre yazılmış (SQLite'ta tarih `DD.MM.YYYY HH:MM` metni).

Hataları tek tek takip etmek yerine şemayı BİR KEZ okuyup tüm uyumsuzlukları
birlikte görmek daha güvenli. Bu script hiçbir şey değiştirmez; yalnızca
information_schema'yı ve tablo başına 1 örnek satırı okur.

BAKILAN ŞEYLER
--------------
1. Hangi tablolar var (kod `public.tenders`, `tender_announcements`,
   `tender_characteristics`, `tender_okas_codes` bekliyor)
2. Kolon adları ve TÜRLERİ — özellikle `ihale_tarihi`, `id`, `tender_id`
3. `icerik_temiz` kolonu canlıda VAR MI? (olmaması gerekiyor; varsa BİLEREK
   kullanılmayacak — plan Bölüm 2 kararı)
4. `ihale_durumu` değerleri — kod tam olarak
   "İhale İlanı Yayımlanmış, Katılıma Açık" metnini arıyor. Canlıda birebir
   aynı yazılmamışsa aktif filtre 0 döndürür ve bu SESSİZ bir hatadır.
5. Satır sayıları
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.domain.models import AKTIF_IHALE_DURUMU  # noqa: E402

BEKLENEN_TABLOLAR = ("tenders", "tender_announcements", "tender_characteristics", "tender_okas_codes")


def main() -> int:
    ayarlar = ayarlari_al()
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as e:
        print(f'HATA: psycopg kurulu değil ({e}).\n  pip install "psycopg[binary]"')
        return 1

    dizge = ayarlar.postgres_baglanti_dizesi()
    gorunur = dizge.replace(ayarlar.database_password, "***") if ayarlar.database_password else dizge
    print("=" * 78)
    print("CANLI POSTGRES ŞEMA KEŞFİ — sadece okuma")
    print("=" * 78)
    print(f"  {gorunur}\n")

    with psycopg.connect(dizge) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # ---- 1) Tablolar ----
            cur.execute("""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name
            """)
            tablolar = cur.fetchall()
            print(f"--- TABLOLAR ({len(tablolar)}) ---")
            for t in tablolar:
                print(f"  {t['table_schema']}.{t['table_name']}")
            adlar = {t["table_name"] for t in tablolar}
            eksik = [t for t in BEKLENEN_TABLOLAR if t not in adlar]
            if eksik:
                print(f"\n  !! KODUN BEKLEDİĞİ AMA BULUNAMAYAN: {', '.join(eksik)}")

            # ---- 2) Kolonlar ----
            for tablo in BEKLENEN_TABLOLAR:
                if tablo not in adlar:
                    continue
                cur.execute("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = %s
                    ORDER BY ordinal_position
                """, (tablo,))
                kolonlar = cur.fetchall()
                print(f"\n--- {tablo} ({len(kolonlar)} kolon) ---")
                for k in kolonlar:
                    isaret = ""
                    if k["column_name"] in ("ihale_tarihi", "ilan_tarihi"):
                        isaret = "   <-- KODUN KRİTİK VARSAYIMI"
                    if k["column_name"] == "icerik_temiz":
                        isaret = "   <-- CANLIDA VAR! (bilerek kullanılmayacak)"
                    bos = "" if k["is_nullable"] == "YES" else " NOT NULL"
                    print(f"  {k['column_name']:22s} {k['data_type']}{bos}{isaret}")

                try:
                    cur.execute(f"SELECT COUNT(*) AS n FROM public.{tablo}")
                    print(f"  satır sayısı: {cur.fetchone()['n']:,}")
                except Exception as e:  # noqa: BLE001
                    print(f"  satır sayısı okunamadı: {type(e).__name__}: {e}")

            # ---- 3) ihale_durumu değerleri ----
            print("\n--- ihale_durumu DEĞERLERİ (en sık 15) ---")
            cur.execute("""
                SELECT ihale_durumu, COUNT(*) AS n
                FROM public.tenders
                GROUP BY ihale_durumu
                ORDER BY n DESC
                LIMIT 15
            """)
            bulundu = False
            for r in cur.fetchall():
                d = r["ihale_durumu"]
                esit = d == AKTIF_IHALE_DURUMU
                bulundu = bulundu or esit
                print(f"  {r['n']:>8,}  {d!r}{'   <-- KODUN ARADIĞI' if esit else ''}")
            print()
            if bulundu:
                print(f"  OK: kodun aradığı durum metni canlıda AYNEN var.")
            else:
                print(f"  !! KODUN ARADIĞI METİN BULUNAMADI: {AKTIF_IHALE_DURUMU!r}")
                print("     Aktif filtre 0 döndürür — SESSİZ hata. Metin düzeltilmeli.")

            # ---- 4) ihale_tarihi örnekleri ----
            print("\n--- ihale_tarihi ÖRNEKLERİ ---")
            cur.execute("""
                SELECT ihale_tarihi, pg_typeof(ihale_tarihi) AS tur
                FROM public.tenders
                WHERE ihale_tarihi IS NOT NULL
                LIMIT 5
            """)
            for r in cur.fetchall():
                print(f"  {r['tur']}  ->  {r['ihale_tarihi']!r}")

            # ---- 5) Bir örnek ihale + ilan ----
            print("\n--- ÖRNEK KAYIT ---")
            cur.execute("""
                SELECT id, ikn, adi, ihale_durumu, ihale_tarihi
                FROM public.tenders
                WHERE ihale_durumu = %s
                LIMIT 1
            """, (AKTIF_IHALE_DURUMU,))
            ih = cur.fetchone()
            if not ih:
                print("  Aktif durumda kayıt bulunamadı.")
            else:
                for k, v in ih.items():
                    print(f"  {k:16s}: {str(v)[:70]}")
                cur.execute("""
                    SELECT ilan_tipi, ilan_tarihi, length(icerik) AS ham_uzunluk
                    FROM public.tender_announcements
                    WHERE tender_id::text = %s
                """, (str(ih["id"]),))
                ilanlar = cur.fetchall()
                print(f"  ilan sayısı     : {len(ilanlar)}")
                for il in ilanlar:
                    print(f"    {il['ilan_tipi']} | {il['ilan_tarihi']} | {il['ham_uzunluk']} kr")

    print("\n" + "=" * 78)
    print("SIRADAKİ: bu çıktıya göre postgres_depo.py'deki _AKTIF_KOSUL düzeltilecek,")
    print("sonra denklik_testi.py tekrar koşulacak.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
