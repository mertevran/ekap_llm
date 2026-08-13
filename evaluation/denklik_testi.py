"""
SQLite ↔ Postgres denklik testi. SADECE OKUMA — hiçbir yazma yapmaz.

    python evaluation/denklik_testi.py                  # 50 örnek
    python evaluation/denklik_testi.py --ornek 200
    python evaluation/denklik_testi.py --ikn 2026/948244

=============================================================================
NEDEN BU TEST VAR — ATLANMAMASI GEREKEN KAPI
=============================================================================
Postgres arka ucunun kodu yazılı ama BUGÜNE KADAR TEK SATIRI GERÇEK BAĞLANTIDA
KOŞMADI (devir dokümanı, Bölüm 7 madde 8). Doğrudan tam taramaya geçmek, 8.000
ihalelik bir koşuyu doğrulanmamış bir veri yolu üzerinde başlatmak olur.

EN KRİTİK RİSK — SESSİZ METİN FARKI:
`icerik_temiz` alanı canlı Postgres'te YOKTUR. Her iki arka uç da ham `icerik`i
okuyup `_ilani_kur()` ile OKUMA ANINDA temizler. Bu yolun yerel kopyada kayıtlı
kolonla birebir aynı sonucu verdiği 96.218 satırda doğrulandı — AMA YEREL KOPYADA.

Canlıdaki ham `icerik` alanı farklı biçimde geliyorsa (farklı HTML dönüşümü,
farklı satır sonu, kırpılmış içerik) temizleyici sessizce BAŞKA bir metin üretir.
O metin modele giden tek girdi olduğu için bugüne kadarki tüm ölçümler
(profil daraltma, eşikler, 30/30 kaçırma sonucu) canlıda geçersiz olur.
Ve bu hata hiçbir yerde patlamaz — sadece kararlar sessizce değişir.

BEKLENEN FARK (hata değil): `aktif_ihaleler()` sorgusunda Postgres, "ihale_tarihi
geçmemiş" koşulunu da SQL'e koyuyor; SQLite'ta tarih metin olduğu için o kontrol
Python tarafında (on_filtre.py) yapılıyor. Ayrıca canlı veri günceldir, yerel
kopya 22.07 anlık görüntüsüdür. Sayıların farklı olması NORMALDİR — bu test
sayıları değil, AYNI İHALENİN AYNI OKUNUP OKUNMADIĞINI karşılaştırır.
=============================================================================
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.database.base import IhaleBulunamadi  # noqa: E402
from app.domain.models import kapsam_metni  # noqa: E402

# Alan alan karşılaştırılacak skaler alanlar
ALANLAR = (
    "id", "adi", "idare_adi", "il", "ihale_tarihi", "ihale_turu",
    "ihale_usulu", "ihale_durumu", "kapsam", "ihale_yeri", "isin_yeri",
)


def _kisa(v, n: int = 60) -> str:
    s = "" if v is None else str(v)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def karsilastir(a, b) -> list[tuple[str, str, str]]:
    """(alan, sqlite_degeri, postgres_degeri) farklarını döndürür."""
    farklar: list[tuple[str, str, str]] = []

    for alan in ALANLAR:
        av, bv = getattr(a, alan, None), getattr(b, alan, None)
        if (av or "") != (bv or ""):
            farklar.append((alan, _kisa(av), _kisa(bv)))

    ak = {k.kod for k in a.okas_kodlari}
    bk = {k.kod for k in b.okas_kodlari}
    if ak != bk:
        farklar.append(("okas_kodlari", _kisa(sorted(ak)), _kisa(sorted(bk))))

    if len(a.ilanlar) != len(b.ilanlar):
        farklar.append(("ilan_sayisi", str(len(a.ilanlar)), str(len(b.ilanlar))))

    at = sorted((i.ilan_tipi or "") for i in a.ilanlar)
    bt = sorted((i.ilan_tipi or "") for i in b.ilanlar)
    if at != bt:
        farklar.append(("ilan_tipleri", _kisa(at), _kisa(bt)))

    # --- EN KRİTİK: modele giden metin ---
    am, bm = kapsam_metni(a) or "", kapsam_metni(b) or ""
    if am != bm:
        # Farkın nerede başladığını göster — teşhis için tek satır yeter
        i = next((j for j in range(min(len(am), len(bm))) if am[j] != bm[j]), min(len(am), len(bm)))
        farklar.append((
            "!! KAPSAM_METNI",
            f"{len(am)} kr, {i}. karakterden itibaren: {_kisa(am[i:i+40], 42)}",
            f"{len(bm)} kr, {i}. karakterden itibaren: {_kisa(bm[i:i+40], 42)}",
        ))

    # Ham içerik uzunlukları — temizleyici farkının kaynağını ayırt etmek için
    ah = sum(len(i.icerik or "") for i in a.ilanlar)
    bh = sum(len(i.icerik or "") for i in b.ilanlar)
    if ah != bh:
        farklar.append(("ham_icerik_uzunlugu", str(ah), str(bh)))

    return farklar


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ornek", type=int, default=50, help="Kaç İKN karşılaştırılacak")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ikn", action="append", help="Belirli İKN(ler) — tekrarlanabilir")
    a = ap.parse_args()

    ayarlar = ayarlari_al()

    from app.database.sqlite_depo import SqliteIhaleDeposu

    try:
        from app.database.postgres_depo import PostgresIhaleDeposu
    except ImportError as e:
        print(f"HATA: psycopg kurulu değil ({e}).\n  pip install \"psycopg[binary]\"")
        return 1

    print("\n" + "=" * 76)
    print("SQLITE ↔ POSTGRES DENKLİK TESTİ — sadece okuma")
    print("=" * 76)

    sq = SqliteIhaleDeposu(ayarlar.sqlite_yolu)
    print(f"  sqlite   : {ayarlar.sqlite_yolu}")

    dizge = ayarlar.postgres_baglanti_dizesi()
    # Şifreyi ASLA basma
    gorunur = dizge
    for gizli in (ayarlar.database_password,):
        if gizli:
            gorunur = gorunur.replace(gizli, "***")
    print(f"  postgres : {gorunur}")

    try:
        pg = PostgresIhaleDeposu(dizge)
        pg_sayi = pg.aktif_ihale_sayisi()
    except Exception as e:  # noqa: BLE001
        print(f"\n  !! POSTGRES BAĞLANTISI KURULAMADI: {type(e).__name__}: {e}")
        print("     .env'deki DATABASE_* alanlarını ve ağ erişimini (VPN?) kontrol edin.")
        return 1

    sq_sayi = sq.aktif_ihale_sayisi()
    print(f"\nAktif ihale sayısı:  sqlite {sq_sayi}   postgres {pg_sayi}")
    print("  (Farklı olması NORMAL: canlı veri günceldir + Postgres tarih filtresini")
    print("   SQL'e koyuyor. Bu test sayıları değil, AYNI İHALEYİ karşılaştırır.)")

    if a.ikn:
        iknler = a.ikn
    else:
        havuz = sq.aktif_ihale_iknleri()
        iknler = random.Random(a.seed).sample(havuz, min(a.ornek, len(havuz)))
    print(f"\n{len(iknler)} İKN karşılaştırılıyor...\n")

    ayni = 0
    bulunamadi: list[str] = []
    hatali: list[tuple[str, str]] = []
    farkli: list[tuple[str, list]] = []
    metin_farki = 0

    for i, ikn in enumerate(iknler, 1):
        try:
            s = sq.ikn_ile_getir(ikn)
        except IhaleBulunamadi:
            continue
        try:
            p = pg.ikn_ile_getir(ikn)
        except IhaleBulunamadi:
            bulunamadi.append(ikn)
            continue
        except Exception as e:  # noqa: BLE001
            hatali.append((ikn, f"{type(e).__name__}: {e}"))
            continue

        f = karsilastir(s, p)
        if not f:
            ayni += 1
        else:
            farkli.append((ikn, f))
            if any(alan.startswith("!!") for alan, _, _ in f):
                metin_farki += 1
        if i % 10 == 0:
            print(f"  {i}/{len(iknler)}", flush=True)

    # ---------------------------------------------------------------- RAPOR
    print("\n" + "=" * 76)
    print("SONUÇ")
    print("=" * 76)
    toplam = ayni + len(farkli)
    print(f"  birebir aynı        : {ayni}/{toplam}")
    print(f"  farklı              : {len(farkli)}/{toplam}")
    print(f"  KAPSAM METNİ farklı : {metin_farki}   <-- SIFIR OLMALI")
    if bulunamadi:
        print(f"  canlıda bulunamadı  : {len(bulunamadi)}  (ör. {', '.join(bulunamadi[:3])})")
        print("    Not: yerel kopya 22.07 anlık görüntüsü; silinmiş/arşivlenmiş kayıt olabilir.")
    if hatali:
        print(f"  okuma hatası        : {len(hatali)}")
        for ikn, m in hatali[:5]:
            print(f"    {ikn}: {m}")

    if farkli:
        print(f"\n{'-' * 76}\nFARKLAR (ilk 12)\n{'-' * 76}")
        for ikn, f in farkli[:12]:
            print(f"\n{ikn}")
            for alan, av, bv in f:
                print(f"  {alan}")
                print(f"    sqlite   : {av}")
                print(f"    postgres : {bv}")

    print(f"\n{'=' * 76}\nYORUM\n{'=' * 76}")
    if metin_farki:
        print("  !! KAPSAM METNİ FARKLI ÇIKTI — TAM TARAMAYA GEÇMEYİN.")
        print("  Modele giden tek girdi bu metin. Farklıysa bugüne kadarki tüm ölçümler")
        print("  (eşikler, profil daraltma, 30/30 kaçırma sonucu) canlıda geçersizdir.")
        print("  Önce ham `icerik` alanının iki kaynakta neden farklı olduğu bulunmalı.")
        return 1
    if not toplam:
        print("  Karşılaştırılacak kayıt bulunamadı — örneklem veya bağlantıyı kontrol edin.")
        return 1
    if farkli:
        print("  Kapsam metni AYNI — modele giden girdi iki kaynakta birebir eşleşiyor.")
        print("  Kalan farklar metadata düzeyinde (durum/tarih güncellenmiş olabilir);")
        print("  yukarıdaki listeyi gözden geçirip beklenen olup olmadığına karar verin.")
    else:
        print("  TAM DENKLİK. İki arka uç aynı ihaleyi birebir aynı okuyor.")
        print("  DATA_BACKEND=postgres'e geçiş güvenli.")
    print("\n  Sonraki adım:  python scripts/read_tender.py --aktif 300 --ozet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
