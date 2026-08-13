"""
Aktif ihale sayısı teşhisi — CANLI Postgres üzerinde, SADECE OKUMA.

    python evaluation/aktif_teshis.py

=============================================================================
NEDEN
=============================================================================
Kod canlıda ~4.518 aktif ihale sayarken, kurumdan gelen bilgiye göre gerçek sayı
8 binin üzerinde. Canlı veritabanına her gün yeni ihale ekleniyor, yani havuz
büyüyor — ama kodun saydığı sayı büyümüyor. Bu script farkın nereden geldiğini
adım adım gösterir.

Aktiflik üç koşula bağlı (app/database/postgres_depo.py::_AKTIF_KOSUL):

    ihale_durumu = 'İhale İlanı Yayımlanmış, Katılıma Açık'   <- TEK sabit dize
    AND ihale_tarihi IS NOT NULL
    AND ihale_tarihi >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')

Üçü de satır kaybedebilir ve üçü FARKLI şekilde düzeltilir. Bu script hangisinin
kaybettirdiğini gösterir, tahmin ettirmez.

  H1  Kodun bilmediği bir açık durum var (ör. 'Ön İlan Yayımlanmış').
      -> AKTIF_IHALE_DURUMU tek dize olmaktan çıkıp KÜMEYE dönmeli.

  H2  Dize eşleşmesi kırık: sonda boşluk, farklı Unicode normalizasyonu ya da
      Türkçe 'İ' varyantı. Bu projede AYNI SINIF HATA DAHA ÖNCE YAŞANDI —
      `on_filtre.turkce_ascii_kucuk` tam olarak "İSBAK".lower()'ın 'i'+U+0307
      üretmesi yüzünden yazıldı. Tam eşleşme aranan her Türkçe dize risk taşır.
      -> SQL'de normalizasyon/trim.

  H3  Tarih koşulu eliyor. `ihale_tarihi IS NOT NULL` şartı, RAPOR 3.4'teki
      "Tarih biçimi tanınmıyorsa ihale ELENMEZ" ilkesiyle ÇELİŞİYOR; ortak ön
      filtre (`on_filtre.ihale_tarihi_gecmis_mi`) tarih yoksa elemiyor, SQL
      eliyor. Tarihsiz satır varsa bu bir kaçırma kapısıdır.
      -> IS NOT NULL kaldırılıp NULL'lar aktif sayılmalı.

  H4  Kayıp yok, tanım farklı: 8 bin rakamı teklif süresi dolmuşları da
      sayıyordur. O durumda kodda hata yok.

=============================================================================
NE YAZAR
=============================================================================
Hiçbir şey. Sadece SELECT. `public.tenders` okunur, tek satır bile
değiştirilmez.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.domain.models import AKTIF_IHALE_DURUMU  # noqa: E402

ZAMAN_DILIMI = "Europe/Istanbul"
SIMDI = f"(CURRENT_TIMESTAMP AT TIME ZONE '{ZAMAN_DILIMI}')"


def _bas(baslik: str) -> None:
    print(f"\n{'=' * 74}\n{baslik}\n{'=' * 74}")


def main() -> int:
    ayarlar = ayarlari_al()
    if ayarlar.data_backend != "postgres":
        print(f"! DATA_BACKEND={ayarlar.data_backend}. Bu teşhis CANLI için yazıldı.")
        print("  .env'de DATA_BACKEND=postgres yapın.")
        return 1

    try:
        import psycopg
    except ImportError:
        print("psycopg kurulu değil:  pip install 'psycopg[binary]'")
        return 1

    print(f"Bağlanılıyor: {ayarlar.database_host}:{ayarlar.database_port}"
          f"/{ayarlar.database_name}")
    with psycopg.connect(ayarlar.postgres_baglanti_dizesi()) as conn:
        with conn.cursor() as cur:

            # ---------------------------------------------------------- H1 + H2
            _bas("1) ihale_durumu DAĞILIMI  (H1: bilinmeyen durum, H2: dize varyantı)")
            cur.execute("""
                SELECT ihale_durumu,
                       length(ihale_durumu)       AS karakter,
                       octet_length(ihale_durumu) AS bayt,
                       count(*)                   AS adet
                FROM public.tenders
                GROUP BY 1, 2, 3
                ORDER BY adet DESC
            """)
            satirlar = cur.fetchall()
            print(f"  {'adet':>8}  {'krkt':>4} {'bayt':>4}  durum")
            for durum, krkt, bayt, adet in satirlar:
                isaret = " <-- KODUN ARADIĞI" if durum == AKTIF_IHALE_DURUMU else ""
                print(f"  {adet:8,}  {krkt:4} {bayt:4}  {durum!r}{isaret}")

            # Aynı metnin birden fazla varyantı var mı?
            benzer = [s for s in satirlar if s[0] and "Katılıma" in s[0]]
            if len(benzer) > 1:
                print("\n  !! H2 ADAYI: 'Katılıma' geçen BİRDEN FAZLA varyant var.")
                print("     Uzunlukları karşılaştırın — sonda boşluk ya da farklı")
                print("     Unicode normalizasyonu tam eşleşmeyi sessizce kırar.")
            if not any(s[0] == AKTIF_IHALE_DURUMU for s in satirlar):
                print(f"\n  !! KODUN ARADIĞI DİZE HİÇ YOK: {AKTIF_IHALE_DURUMU!r}")
                print("     H2 kesinleşti — eşleşme dize düzeyinde kırık.")

            # ------------------------------------------------------------- H3
            _bas("2) FİLTRE HUNİSİ  (H3: satırlar nerede eleniyor)")
            cur.execute(f"""
                WITH d AS (
                    SELECT ihale_tarihi FROM public.tenders WHERE ihale_durumu = %s
                )
                SELECT
                    (SELECT count(*) FROM d),
                    (SELECT count(*) FROM d WHERE ihale_tarihi IS NULL),
                    (SELECT count(*) FROM d WHERE ihale_tarihi IS NOT NULL
                                             AND ihale_tarihi <  {SIMDI}),
                    (SELECT count(*) FROM d WHERE ihale_tarihi IS NOT NULL
                                             AND ihale_tarihi >= {SIMDI})
            """, (AKTIF_IHALE_DURUMU,))
            durum_eslesen, tarihi_null, tarihi_gecmis, kodun_saydigi = cur.fetchone()

            print(f"  durum eşleşen                     : {durum_eslesen:8,}")
            print(f"    ├─ tarihi NULL       (eleniyor) : {tarihi_null:8,}")
            print(f"    ├─ tarihi geçmiş     (eleniyor) : {tarihi_gecmis:8,}")
            print(f"    └─ KODUN AKTİF SAYDIĞI          : {kodun_saydigi:8,}")

            if tarihi_null:
                print("\n  !! H3: tarihsiz satır VAR ve SQL bunları eliyor.")
                print("     Rapor 3.4 ilkesi: 'Tarih biçimi tanınmıyorsa ihale ELENMEZ'.")
                print("     on_filtre.ihale_tarihi_gecmis_mi tarih yoksa ELEMİYOR —")
                print("     SQL ile ÇELİŞİYOR. Bu bir kaçırma kapısı, düzeltilmeli.")

            # -------------------------------------------------- gevşek eşleşme
            _bas("3) GEVŞEK EŞLEŞME KIYASI  (H1/H2 doğrulaması)")
            cur.execute("""
                SELECT
                    count(*) FILTER (WHERE ihale_durumu ILIKE '%%Katılıma%%Açık%%'),
                    count(*) FILTER (WHERE ihale_durumu ILIKE '%%Ön İlan%%'),
                    count(*) FILTER (WHERE btrim(ihale_durumu) = btrim(%s)),
                    count(*)
                FROM public.tenders
            """, (AKTIF_IHALE_DURUMU,))
            gevsek, on_ilan, trimli, toplam = cur.fetchone()

            print(f"  ILIKE '%Katılıma%Açık%'           : {gevsek:8,}")
            print(f"  ILIKE '%Ön İlan%'                 : {on_ilan:8,}")
            print(f"  btrim ile eşleşen                 : {trimli:8,}")
            print(f"  tablodaki TOPLAM ihale            : {toplam:8,}")

            if gevsek > durum_eslesen:
                print(f"\n  !! H2 KESİN: gevşek eşleşme {gevsek - durum_eslesen:,} satır")
                print("     fazla buluyor. Tam eşleşme dize farkı yüzünden kaçırıyor.")
            if on_ilan:
                print(f"\n  !! H1 ADAYI: 'Ön İlan' durumunda {on_ilan:,} satır var.")
                print("     Bunlar teklif verilebilir mi? Evetse AKTIF_IHALE_DURUMU")
                print("     tek dize olmaktan çıkıp KÜMEYE dönmeli.")

            # ------------------------------------------------------------ özet
            _bas("ÖZET")
            print(f"  Kodun bugün saydığı aktif ihale   : {kodun_saydigi:,}")
            kayip = durum_eslesen - kodun_saydigi
            if kayip:
                print(f"  Durum eşleşip tarihten elenen     : {kayip:,}")
            print()
            print("  Beklediğin sayı bunun ÇOK üstündeyse ve yukarıda H1/H2/H3")
            print("  uyarısı ÇIKMADIYSA: kayıp yok, tanım farklı (H4). Karşılaştırdığın")
            print("  kaynak teklif süresi dolmuş ihaleleri de sayıyor olabilir.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
