"""
Canlı veritabanı keşfi — tarama stratejisini VERİYLE kurmak için. SADECE OKUMA.

    python evaluation/canli_kesif.py

=============================================================================
CEVAPLANACAK SORULAR
=============================================================================
1) `llm_rag.tender_index_state` ve `profile_index_state` neyi tutuyor?
   Kolonları, satır sayısı, örnek satırlar. İçerik özeti (hash) varsa
   "ilan değişti -> yeniden tara" mekanizması ZATEN kurulmuş olabilir.

2) Veritabanına günde kaç yeni ihale ve kaç yeni İLAN ekleniyor?
   Tam tarama hareketli bir hedefi tarıyor; ilk turdan sonraki günlük yükü
   bu belirler. 100-300/gün ise `--devam` yeter; çok fazlaysa paralellik
   ilk turdan ÖNCE gerekir.

3) Aktif ihalelerin ilan tipi dağılımı ne?
   Yerel kopyada 4.867 "Ön İlan" / 135 "İhale İlanı"ydı. Ön İlan kalem listesi
   vermez ("ayrıntı şartnamede"), İhale İlanı verir. `en_iyi_ilan()` İhale
   İlanı'nı tercih ettiği için ilan tipi değişince MODELE GİDEN METİN DEĞİŞİR
   ve karar da değişebilir.

4) Kaç aktif ihale birden fazla ilan almış?
   Bu, "bir kez taramak yetmez" iddiasının büyüklüğünü verir.

5) `tenders.takip_durumu` (yerel kopyada YOK olan yeni kolon) ne içeriyor?
   İnsan eliyle işaretlenmiş bir takip durumuysa ETİKETLİ VERİ olabilir —
   projenin en büyük eksiği tam bu.

6) `company_preferences` neyi tutuyor? 20 profilin kapasite alanları
   (`belgeler`, `tamamlanan_projeler`, `is_deneyim_belgeleri`) boş ve Aşama 2
   bu yüzden bloke. Aradığımız veri canlıda olabilir.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.domain.models import AKTIF_IHALE_DURUMU  # noqa: E402


def bolum(baslik: str) -> None:
    print(f"\n{'=' * 78}\n{baslik}\n{'=' * 78}")


def tablo_yapisi(cur, sema: str, tablo: str) -> list[str]:
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """, (sema, tablo))
    kolonlar = cur.fetchall()
    if not kolonlar:
        print(f"  {sema}.{tablo}: BULUNAMADI")
        return []
    print(f"  {sema}.{tablo}")
    for k in kolonlar:
        print(f"    {k['column_name']:26s} {k['data_type']}")
    try:
        cur.execute(f"SELECT COUNT(*) AS n FROM {sema}.{tablo}")
        print(f"    -> {cur.fetchone()['n']:,} satır")
    except Exception as e:  # noqa: BLE001
        print(f"    -> sayılamadı: {type(e).__name__}")
    return [k["column_name"] for k in kolonlar]


def ornek_satirlar(cur, sema: str, tablo: str, n: int = 3) -> None:
    try:
        cur.execute(f"SELECT * FROM {sema}.{tablo} LIMIT {n}")
        for i, r in enumerate(cur.fetchall(), 1):
            print(f"    örnek {i}:")
            for k, v in r.items():
                s = str(v)
                print(f"      {k:24s} = {s[:80]}{'…' if len(s) > 80 else ''}")
    except Exception as e:  # noqa: BLE001
        print(f"    örnek okunamadı: {type(e).__name__}: {e}")


def main() -> int:
    ayarlar = ayarlari_al()
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as e:
        print(f'HATA: psycopg kurulu değil ({e}).')
        return 1

    with psycopg.connect(ayarlar.postgres_baglanti_dizesi()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:

            # ---------------------------------------------------------- 1
            bolum("1) llm_rag ŞEMASI — kim ne tutuyor")
            for tablo in ("tender_index_state", "profile_index_state"):
                kolonlar = tablo_yapisi(cur, "llm_rag", tablo)
                if kolonlar:
                    ornek_satirlar(cur, "llm_rag", tablo)
                print()

            # tender_index_state gerçekten sadece aktifleri mi tutuyor?
            try:
                cur.execute("""
                    SELECT t.ihale_durumu, COUNT(*) AS n
                    FROM llm_rag.tender_index_state s
                    JOIN public.tenders t ON t.id::text = s.tender_id::text
                    GROUP BY t.ihale_durumu ORDER BY n DESC LIMIT 8
                """)
                print("  index_state'teki ihalelerin DURUM dağılımı:")
                for r in cur.fetchall():
                    isaret = "  <-- aktif" if r["ihale_durumu"] == AKTIF_IHALE_DURUMU else ""
                    print(f"    {r['n']:>8,}  {r['ihale_durumu']}{isaret}")
            except Exception as e:  # noqa: BLE001
                print(f"  durum dağılımı alınamadı: {type(e).__name__}: {e}")
                print("  (kolon adı 'tender_id' olmayabilir — yukarıdaki yapıya bakın)")

            # ---------------------------------------------------------- 2
            bolum("2) GÜNLÜK ARTIŞ — tam tarama hareketli hedefi tarıyor")
            for tablo, etiket in (("tenders", "yeni İHALE"), ("tender_announcements", "yeni İLAN")):
                cur.execute(f"""
                    SELECT created_at::date AS gun, COUNT(*) AS n
                    FROM public.{tablo}
                    WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
                    GROUP BY 1 ORDER BY 1 DESC LIMIT 14
                """)
                satirlar = cur.fetchall()
                print(f"\n  {etiket} / gün (son 14 gün):")
                if not satirlar:
                    print("    kayıt yok — created_at toplu yükleme tarihi olabilir")
                for r in satirlar:
                    print(f"    {r['gun']}  {r['n']:>6,}")
                if satirlar:
                    ort = sum(r["n"] for r in satirlar) / len(satirlar)
                    print(f"    ortalama: {ort:,.0f}/gün")

            # ---------------------------------------------------------- 3
            bolum("3) AKTİF İHALELERİN İLAN TİPİ DAĞILIMI")
            cur.execute("""
                WITH aktif AS (
                    SELECT id FROM public.tenders
                    WHERE ihale_durumu = %s
                      AND ihale_tarihi IS NOT NULL
                      AND ihale_tarihi >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')
                )
                SELECT a.ilan_tipi, COUNT(*) AS n
                FROM public.tender_announcements a
                JOIN aktif ON aktif.id::text = a.tender_id::text
                GROUP BY 1 ORDER BY n DESC
            """, (AKTIF_IHALE_DURUMU,))
            for r in cur.fetchall():
                print(f"  {r['n']:>8,}  {r['ilan_tipi']}")

            # ---------------------------------------------------------- 4
            bolum("4) KAÇ AKTİF İHALE BİRDEN FAZLA İLAN ALMIŞ")
            cur.execute("""
                WITH aktif AS (
                    SELECT id FROM public.tenders
                    WHERE ihale_durumu = %s
                      AND ihale_tarihi IS NOT NULL
                      AND ihale_tarihi >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Istanbul')
                ), sayim AS (
                    SELECT aktif.id, COUNT(a.id) AS ilan_sayisi
                    FROM aktif LEFT JOIN public.tender_announcements a
                      ON aktif.id::text = a.tender_id::text
                    GROUP BY aktif.id
                )
                SELECT ilan_sayisi, COUNT(*) AS ihale FROM sayim
                GROUP BY 1 ORDER BY 1
            """, (AKTIF_IHALE_DURUMU,))
            toplam = 0
            coklu = 0
            for r in cur.fetchall():
                toplam += r["ihale"]
                if r["ilan_sayisi"] > 1:
                    coklu += r["ihale"]
                print(f"  {r['ilan_sayisi']} ilan  ->  {r['ihale']:>6,} ihale")
            if toplam:
                print(f"\n  Birden fazla ilanı olan: {coklu:,}/{toplam:,}  (%{100*coklu/toplam:.0f})")
                print("  Bu oran, 'bir kez taramak yetmez' iddiasının büyüklüğü.")

            # ---------------------------------------------------------- 5
            bolum("5) takip_durumu — İNSAN ETİKETİ OLABİLİR")
            cur.execute("""
                SELECT takip_durumu, COUNT(*) AS n
                FROM public.tenders GROUP BY 1 ORDER BY n DESC LIMIT 12
            """)
            for r in cur.fetchall():
                print(f"  {r['n']:>8,}  {r['takip_durumu']!r}")

            # ---------------------------------------------------------- 6
            bolum("6) company_preferences — AŞAMA 2'NİN ARADIĞI VERİ Mİ")
            if tablo_yapisi(cur, "public", "company_preferences"):
                ornek_satirlar(cur, "public", "company_preferences", 5)

            bolum("7) okas_catalog")
            if tablo_yapisi(cur, "public", "okas_catalog"):
                ornek_satirlar(cur, "public", "okas_catalog", 3)

    print("\n" + "=" * 78)
    print("Bu çıktıya göre: tarama stratejisi + sonuç tablosu şeması + yeniden")
    print("tarama tetikleyicisi kararlaştırılacak.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
