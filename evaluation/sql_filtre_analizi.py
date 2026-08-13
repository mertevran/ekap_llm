"""
SQL ön filtresi analizi — LLM ÇAĞIRMAZ, SADECE OKUMA.

    python evaluation/sql_filtre_analizi.py
    python evaluation/sql_filtre_analizi.py --genislik dar
    python evaluation/sql_filtre_analizi.py --rastgele 800
    python evaluation/sql_filtre_analizi.py --ek-terim "elektronik denetleme" "akıllı ulaşım"

=============================================================================
TEK BİR SORUYU CEVAPLAR
=============================================================================
"SQL ile havuzu daraltırsak POZİTİF KAYBEDİYOR MUYUZ?"

İki sayı üretir ve ikisi birlikte okunmalıdır:

    GERİ ÇAĞIRMA  : etiketli pozitiflerin yüzde kaçı filtreyi geçiyor
    DARALMA       : rastgele aktif havuzun yüzde kaçı eleniyor

Daralma tek başına ANLAMSIZDIR. Her şeyi eleyen bir filtre %100 daraltır ve
sistemi yok eder. Kabul kriteri ÖNCE geri çağırmadır.

KABUL EŞİĞİ: geri çağırma **41/41 (%100)**. Gerekçe: `sert_on_filtre_skoru=0.44`
kabul edilirken ölçülen pozitif kaybı SIFIRDI (`Sonuclar/esik_analizi.json`).
Aynı çıtayı burada da uyguluyoruz — daha düşüğü kaçırma kapısı üretir ve
kaçırma bu projenin en pahalı hatasıdır.

=============================================================================
İLK ÖLÇÜM (06.08.2026, genislik=guclu+destekleyici+genel)
=============================================================================
    GERİ ÇAĞIRMA : 35/41  (%85)   <-- KABUL EDİLEMEZ
    DARALMA      : %88

Kaçan 6 ihale İSBAK'ın çekirdek işiydi (EDS, İstanbul Geneli Sinyalizasyon
Bakım-Onarım, Akıllı Ulaşım Sistemleri...). Bu script terim listeleri
genişletildikçe tekrar koşulmak için var: `--ek-terim` ile aday terim dene,
geri çağırma 41/41 olduğunda daralmanın ne kadar kaldığına bak.

Daralma %50'nin altına inerse filtre zahmete değmez; o noktada gömme tabanlı
sınıflandırıcı (aynı vektör, kelime eşleşmesi yok) daha doğru yatırımdır.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.database.base import depo_olustur  # noqa: E402
from app.decision.sql_filtre import eslesme_var_mi, profillerden_kur  # noqa: E402
from app.profiles.loader import profilleri_yukle  # noqa: E402

KOK = Path(__file__).resolve().parents[1]
KACIRMA_SETI = KOK / "evaluation" / "kacirma-seti-v1.csv"
KARAR_SETI = KOK / "evaluation" / "karar-seti-v1.csv"


def _bas(baslik: str) -> None:
    print(f"\n{'=' * 76}\n{baslik}\n{'=' * 76}")


def pozitif_iknleri() -> list[str]:
    """Etiketli 'uygun' ihalelerin İKN'leri (kaçırma seti + karar setinin uygunları)."""
    iknler: list[str] = []
    if KACIRMA_SETI.exists():
        with open(KACIRMA_SETI, encoding="utf-8-sig") as f:
            iknler += [s["ikn"] for s in csv.DictReader(f, delimiter=";")]
    if KARAR_SETI.exists():
        with open(KARAR_SETI, encoding="utf-8-sig") as f:
            iknler += [s["ikn"] for s in csv.DictReader(f, delimiter=";")
                       if (s.get("karar") or "").strip() == "uygun"]
    return list(dict.fromkeys(iknler))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--genislik", choices=("dar", "genis", "odakli"), default="genis",
                    help="dar = sadece guclu_terimler, genis = anahtar_kelimeler")
    ap.add_argument("--rastgele", type=int, default=400,
                    help="Daralma ölçümü için kaç rastgele aktif ihale")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ek-terim", nargs="*", default=None,
                    help="Denenecek ek terimler (geri çağırmayı yükseltmek için)")
    ap.add_argument("--ek-okas", nargs="*", default=None,
                    help="Denenecek ek OKAS ön ekleri (ör. 45316 50232 63712)")
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    depo = depo_olustur(ayarlar)
    tanim = profillerden_kur(profilleri_yukle(), genislik=a.genislik, ek_terimler=a.ek_terim)
    if a.ek_okas:
        from dataclasses import replace
        tanim = replace(tanim, okas_on_ekleri=tuple(sorted(
            set(tanim.okas_on_ekleri) | {str(p).strip() for p in a.ek_okas if str(p).strip()})))

    _bas("FİLTRE TANIMI")
    print(f"  kaynak         : {tanim.kaynak}")
    print(f"  terim sayısı   : {len(tanim.terimler)}")
    print(f"  OKAS ön eki    : {len(tanim.okas_on_ekleri)}")
    if a.ek_terim:
        print(f"  eklenen terim  : {', '.join(a.ek_terim)}")

    # ---------------------------------------------------------- GERİ ÇAĞIRMA
    _bas("1) POZİTİF GERİ ÇAĞIRMA  —  ASIL KABUL KRİTERİ")
    iknler = pozitif_iknleri()
    if not iknler:
        print("  Etiketli pozitif bulunamadı; ölçüm anlamsız.")
        return 1

    ihaleler = depo.coklu_getir(iknler, alan="ikn")
    kacan = []
    for ih in ihaleler:
        if not eslesme_var_mi(tanim, ih.adi, [o.kod for o in ih.okas_kodlari]):
            kacan.append(ih)

    n = len(ihaleler)
    gecen = n - len(kacan)
    print(f"  etiketli pozitif : {n}")
    print(f"  filtreyi geçen   : {gecen}   (%{100*gecen/max(n,1):.0f})")
    print(f"  KAÇAN            : {len(kacan)}")
    if kacan:
        print("\n  Kaçan pozitifler — bu isimlerden terim türetilmeli:")
        for ih in kacan:
            print(f"    {ih.ikn}  {ih.adi[:64]}")
            kodlar = ", ".join(o.kod for o in ih.okas_kodlari) or "-"
            print(f"       OKAS: {kodlar}")

    # --------------------------------------------------------------- DARALMA
    _bas("2) HAVUZ DARALMASI  —  ancak geri çağırma tamsa anlamlı")
    havuz = depo.aktif_ihale_iknleri()
    rastgele = random.Random(a.seed).sample(havuz, min(a.rastgele, len(havuz)))
    ornek = depo.coklu_getir(rastgele, alan="ikn")
    kalan = sum(1 for ih in ornek
                if eslesme_var_mi(tanim, ih.adi, [o.kod for o in ih.okas_kodlari]))
    m = len(ornek)
    print(f"  aktif havuz      : {len(havuz):,}")
    print(f"  örneklem         : {m}  (seed={a.seed})")
    print(f"  filtreyi geçen   : {kalan}  (%{100*kalan/max(m,1):.0f})")
    print(f"  ELENEN           : {m-kalan}  (%{100*(m-kalan)/max(m,1):.0f})")
    if m:
        tahmini = int(len(havuz) * kalan / m)
        print(f"\n  Tüm havuza ölçeklenirse taranacak ihale: ~{tahmini:,} "
              f"(şu an {len(havuz):,})")

    # ----------------------------------------------------------------- HÜKÜM
    _bas("HÜKÜM")
    if not kacan:
        print("  GERİ ÇAĞIRMA TAM (%100). Filtre ELEME modunda kullanılabilir.")
        print("  Yine de: etiketli set 41 örnek. 'Hiç bulunmamış pozitifler'")
        print("  bu sette YOK — ölçüm bu sınırla okunmalı.")
    else:
        print(f"  GERİ ÇAĞIRMA EKSİK: {len(kacan)} pozitif kaybediliyor.")
        print("  ELEME MODUNU AÇMA — bu bir kaçırma kapısıdır.")
        print("  Yapılacak: yukarıdaki kaçan başlıklardan terim türet, --ek-terim")
        print("  ile dene, 41/41 olana kadar tekrarla. O zamana kadar SIRALAMA")
        print("  modu kullan (toplu_tarama.py --siralama): eleme yok, sadece sıra.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
