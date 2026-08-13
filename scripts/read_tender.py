"""
Bir ihaleyi veri kaynağından okuyup ham/temiz metni yan yana gösterir.
LLM ve Qdrant GEREKTİRMEZ — sadece veri katmanını ve temizleyiciyi test eder.

    python scripts/read_tender.py 2025/2196999
    python scripts/read_tender.py --aktif 5              # 5 aktif ihaleye göz at
    python scripts/read_tender.py --aktif 30 --ozet      # ilan tipi dağılımı

`--ozet` kipi, temizleyicinin kaç ilanda güvenli üst sınıra (3000 karakter) takıldığını
ve ortalama ne kadar metin attığını raporlar. Ön İlan şablonunu doğrulamak için bu.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.base import depo_olustur  # noqa: E402
from app.domain.models import en_iyi_ilan, kapsam_metni  # noqa: E402
from app.text.ilan_temizleyici import GUVENLI_UST_SINIR  # noqa: E402


def tek_goster(ihale, tam: bool) -> None:
    ilan = en_iyi_ilan(ihale)
    temiz = kapsam_metni(ihale)
    print("=" * 78)
    print(f"İKN     : {ihale.ikn}")
    print(f"Adı     : {ihale.adi}")
    print(f"İdare   : {ihale.idare_adi}")
    print(f"İl/Tür  : {ihale.il} / {ihale.ihale_turu} / {ihale.ihale_usulu}")
    print(f"Durum   : {ihale.ihale_durumu}")
    print(f"Tarih   : {ihale.ihale_tarihi}")
    print(f"OKAS    : {', '.join(ihale.okas_metni()) or '(yok)'}")
    print(f"Özellik : {len(ihale.ozellikler)} kayıt")
    print(f"İlanlar : {[(i.ilan_tipi, len(i.icerik or ''), len(i.icerik_temiz)) for i in ihale.ilanlar]}")
    if ilan:
        ham = len(ilan.icerik or "")
        print(f"Seçilen : {ilan.ilan_tipi}  |  ham {ham} -> temiz {len(temiz)} karakter "
              f"(%{100*(1-len(temiz)/max(ham,1)):.0f} atıldı)")
    print("-" * 78)
    print(temiz if tam else temiz[:900] + ("\n[...]" if len(temiz) > 900 else ""))
    print()


def ozet_raporu(ihaleler) -> None:
    import statistics

    satirlar = []
    for i in ihaleler:
        ilan = en_iyi_ilan(i)
        if not ilan or not ilan.icerik:
            continue
        temiz = kapsam_metni(i)
        satirlar.append((ilan.ilan_tipi, len(ilan.icerik), len(temiz), len(temiz) >= GUVENLI_UST_SINIR))

    if not satirlar:
        print("İncelenecek ilan bulunamadı.")
        return

    print(f"\n=== TEMİZLEYİCİ ÖZETİ ({len(satirlar)} ilan) ===")
    tipler = {}
    for tip, ham, temiz, tavan in satirlar:
        tipler.setdefault(tip, []).append((ham, temiz, tavan))
    for tip, kayitlar in sorted(tipler.items()):
        hamlar = [k[0] for k in kayitlar]
        temizler = [k[1] for k in kayitlar]
        tavana_carpan = sum(1 for k in kayitlar if k[2])
        print(
            f"  {tip:12s} n={len(kayitlar):4d}  "
            f"ham ort={statistics.mean(hamlar):7.0f}  temiz ort={statistics.mean(temizler):6.0f}  "
            f"temiz max={max(temizler):5d}  "
            f"güvenli üst sınıra ({GUVENLI_UST_SINIR}) takılan: {tavana_carpan}"
        )
    bos = sum(1 for _, _, t, _ in satirlar if t == 0)
    print(f"\n  Temiz metni BOŞ çıkan: {bos}  <-- 0 olmalı; değilse temizleyici o şablonu tanımıyor")
    print("  (Aktif ihalelerin ~%99'u 'Ön İlan' tipindedir — asıl bakılması gereken satır odur.)\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ikn", nargs="*", help="Bir veya daha fazla İKN")
    ap.add_argument("--aktif", type=int, metavar="N", help="İlk N aktif ihaleyi getir")
    ap.add_argument("--ozet", action="store_true", help="Tek tek basmak yerine istatistik çıkar")
    ap.add_argument("--tam", action="store_true", help="Temiz metni kırpmadan bas")
    a = ap.parse_args()

    depo = depo_olustur()
    print(f"veri kaynağı: {type(depo).__name__}")

    if a.aktif:
        ihaleler = depo.aktif_ihaleler(limit=a.aktif)
    elif a.ikn:
        ihaleler = depo.coklu_getir(a.ikn, alan="ikn")
        if not ihaleler:
            print(f"Bulunamadı: {a.ikn}")
            return 1
    else:
        ap.error("İKN verin ya da --aktif N kullanın")

    if a.ozet:
        ozet_raporu(ihaleler)
    else:
        for i in ihaleler:
            tek_goster(i, a.tam)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
