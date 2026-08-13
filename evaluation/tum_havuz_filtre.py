"""
Tüm ihale havuzunda alan filtresi — 50 binlik tabloda, SADECE OKUMA.

    python evaluation/tum_havuz_filtre.py                      # durum dağılımı
    python evaluation/tum_havuz_filtre.py --durum aktif        # sadece aktif
    python evaluation/tum_havuz_filtre.py --durum tamamlanmis --disa-aktar korpus.csv
    python evaluation/tum_havuz_filtre.py --yil 2026 --ornek 20

=============================================================================
NEDEN AYRI SCRIPT (sql_filtre_analizi.py neden yetmiyor)
=============================================================================
`sql_filtre_analizi.py` tek bir soruyu cevaplar: "filtre pozitif kaybediyor mu?"
Kapsamı AKTİF havuzdur, çünkü tarama oradan yapılır.

Bu script farklı bir iş yapar: TÜM tabloyu (aktif + geçmiş + iptal + sonuçlanmış)
filtreden geçirir. İki kullanımı var:

  1. FİLTRE DAVRANIŞINI GÖRMEK. Filtre duruma göre yanlıysa (ör. sadece aktifleri
     tutuyorsa) bu tabloda görünür. Yerel kopyada ölçüldü: altı durumun hepsinde
     geçme oranı ~%12,4 — yanlılık yok.

  2. GEÇMİŞ İHALE KORPUSU ÇIKARMAK. Raporun 9.4'ünde eksik olarak sayılıyor:
     "Geçmiş ihale korpusu indekslenmemiştir." Yerel kopyada 30.789 sonuçlanmış
     ihalenin 3.719'u filtreden geçiyor — yani İSBAK'ın alanında, tamamlanmış,
     gerçek iş. Elde bugün 41 etiketli örnek var; bu havuz 90 katı.

     Kullanım alanları: kontrastif örnek havuzunu büyütmek, gömme tabanlı
     sınıflandırıcıya eğitim verisi, Aşama 2 için kanıt havuzu.

=============================================================================
UYARI — BU BİR ETİKET DEĞİLDİR
=============================================================================
Filtreden geçmek "İSBAK'a uygun" DEMEK DEĞİLDİR. Filtre kelime ve OKAS
eşleşmesidir; raporun 2. bölümündeki iki yönlü hataya açıktır ("Sondaj ve
Workover Kuleleri Kamera Sistemi" bu filtreyi rahatça geçer).

Çıkan liste bir ADAY HAVUZUDUR. Etiket olması için insan kararı ya da en azından
LLM değerlendirmesi gerekir. Kontrastif koleksiyona doğrudan beslenirse sızıntı
üretir — `scripts/index_profiles.py` final satırlarını bu yüzden dışlıyor.

=============================================================================
NE YAZAR
=============================================================================
Hiçbir şey. Sadece SELECT. `--disa-aktar` verilirse yerel bir CSV üretir.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.decision.sql_filtre import eslesme_var_mi, profillerden_kur  # noqa: E402
from app.profiles.loader import profilleri_yukle  # noqa: E402

# `--durum` kısayolları -> `tenders.ihale_durumu` değerleri
DURUM_KISAYOLLARI = {
    "aktif": "İhale İlanı Yayımlanmış, Katılıma Açık",
    "tamamlanmis": "Sonuç İlanı Yayımlanmış",
    "iptal": "İhale İptal Edilmiş",
    "degerlendirme": "Teklif Değerlendirme Tamamlanmış",
    "kapali": "İhale Tekliflere Kapalı, Teklifler Değerlendiriliyor",
    "sozlesme": "Sözleşme İmzalanmış",
}


def _yil(ihale_tarihi) -> str:
    """`DD.MM.YYYY HH:MM` metni ya da `datetime` -> `YYYY`. Tanınmazsa '?'.

    İKİ TÜRÜ DE KABUL ETMEK ZORUNDA: canlıda `ihale_tarihi` gerçek bir
    `timestamp` kolonu, yerel SQLite kopyasında ise METİN. `postgres_depo`
    bunu `to_char()` ile çözüyor (gerekçesi o dosyanın başlığında), ama bu
    script kendi ham SELECT'ini attığı için dönüşümden geçmiyor.
    """
    if hasattr(ihale_tarihi, "year"):          # datetime / date
        return str(ihale_tarihi.year)
    s = str(ihale_tarihi or "").strip()
    return s[6:10] if len(s) >= 10 and s[6:10].isdigit() else "?"


def _bas(baslik: str) -> None:
    print(f"\n{'=' * 76}\n{baslik}\n{'=' * 76}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--durum", choices=sorted(DURUM_KISAYOLLARI), default=None,
                    help="Tek bir duruma daralt. Boş = tüm tablo.")
    ap.add_argument("--yil", default=None, help="İhale yılına göre daralt (ör. 2026)")
    ap.add_argument("--genislik", choices=("dar", "genis", "odakli"), default="genis")
    ap.add_argument("--ornek", type=int, default=10, help="Kaç örnek başlık basılsın")
    ap.add_argument("--disa-aktar", type=Path, default=None,
                    help="Eşleşenleri CSV'ye yaz (yerel dosya, DB'ye yazmaz)")
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    tanim = profillerden_kur(profilleri_yukle(), genislik=a.genislik)

    _bas("FİLTRE")
    print(f"  kaynak      : {tanim.kaynak}")
    print(f"  terim       : {len(tanim.terimler)}")
    print(f"  OKAS ön eki : {len(tanim.okas_on_ekleri)}")

    # Tüm tabloyu okumak için repository arayüzü yetmiyor (o AKTİF'e kilitli);
    # doğrudan hafif bir SELECT atıyoruz. SADECE OKUMA.
    satirlar: list[dict] = []
    okaslar: dict[str, list[str]] = defaultdict(list)

    if ayarlar.data_backend == "postgres":
        import psycopg

        with psycopg.connect(ayarlar.postgres_baglanti_dizesi()) as conn:
            with conn.cursor() as cur:
                # to_char: tarih iki arka uçta da AYNI metin biçiminde gelsin
                # (bkz. postgres_depo.py başlığı, _IHALE_TARIHI_BICIMI).
                cur.execute("SELECT id, ikn, adi, idare_adi, il, ihale_turu, ihale_durumu, "
                            "to_char(ihale_tarihi, 'DD.MM.YYYY HH24:MI') AS ihale_tarihi "
                            "FROM public.tenders")
                kolonlar = [d[0] for d in cur.description]
                satirlar = [dict(zip(kolonlar, r)) for r in cur.fetchall()]
                cur.execute("SELECT tender_id, kod FROM public.tender_okas_codes")
                for tid, kod in cur.fetchall():
                    okaslar[str(tid)].append(str(kod))
    else:
        import sqlite3

        conn = sqlite3.connect(ayarlar.sqlite_yolu)
        conn.row_factory = sqlite3.Row
        try:
            satirlar = [dict(r) for r in conn.execute(
                "SELECT id, ikn, adi, idare_adi, il, ihale_turu, ihale_durumu, "
                "ihale_tarihi FROM tenders")]
            for r in conn.execute("SELECT tender_id, kod FROM tender_okas_codes"):
                okaslar[str(r["tender_id"])].append(str(r["kod"]))
        finally:
            conn.close()

    if a.durum:
        hedef = DURUM_KISAYOLLARI[a.durum]
        satirlar = [s for s in satirlar if s["ihale_durumu"] == hedef]
    if a.yil:
        satirlar = [s for s in satirlar if _yil(s["ihale_tarihi"]) == a.yil]

    if not satirlar:
        print("\n  Bu daraltmayla hiç satır yok.")
        return 1

    eslesen = [s for s in satirlar
               if eslesme_var_mi(tanim, s["adi"], okaslar.get(str(s["id"]), []))]

    # ------------------------------------------------------------ DAĞILIM
    _bas("DURUM DAĞILIMI")
    top, gec = Counter(), Counter()
    for s in satirlar:
        top[s["ihale_durumu"]] += 1
    for s in eslesen:
        gec[s["ihale_durumu"]] += 1
    print(f"  {'durum':<52} {'toplam':>8} {'geçen':>7} {'oran':>6}")
    for d, n in top.most_common():
        g = gec.get(d, 0)
        print(f"  {str(d)[:52]:<52} {n:8,} {g:7,} {100*g/n:5.1f}%")
    print(f"\n  TOPLAM {len(satirlar):,}  →  filtreyi geçen {len(eslesen):,} "
          f"(%{100*len(eslesen)/len(satirlar):.1f})")

    # Oran durumlar arasında çok oynuyorsa filtre yanlı demektir.
    oranlar = [gec.get(d, 0) / n for d, n in top.items() if n >= 100]
    if len(oranlar) > 1 and (max(oranlar) - min(oranlar)) > 0.10:
        print("\n  ! Geçme oranı durumlar arasında %10'dan fazla oynuyor —")
        print("    filtre duruma göre yanlı olabilir, incelenmeli.")

    # --------------------------------------------------------------- YIL
    _bas("YIL DAĞILIMI (filtreyi geçenler)")
    yillar = Counter(_yil(s["ihale_tarihi"]) for s in eslesen)
    for y, n in sorted(yillar.items()):
        print(f"  {y}: {n:,}")

    # ------------------------------------------------------------ ÖRNEKLER
    if a.ornek:
        _bas(f"ÖRNEK BAŞLIKLAR (ilk {a.ornek})")
        for s in eslesen[: a.ornek]:
            kod = ", ".join(okaslar.get(str(s["id"]), [])[:2]) or "-"
            print(f"  {s['ikn']}  {(s['adi'] or '')[:58]}")
            print(f"      {str(s['idare_adi'] or '')[:56]}   OKAS: {kod}")

    # ------------------------------------------------------------- ÇIKTI
    if a.disa_aktar:
        a.disa_aktar.parent.mkdir(parents=True, exist_ok=True)
        alanlar = ["ikn", "adi", "idare_adi", "il", "ihale_turu",
                   "ihale_durumu", "ihale_tarihi", "okas"]
        with open(a.disa_aktar, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=alanlar, delimiter=";", extrasaction="ignore")
            w.writeheader()
            for s in eslesen:
                w.writerow({**s, "okas": "|".join(okaslar.get(str(s["id"]), []))})
        print(f"\n  CSV: {a.disa_aktar}  ({len(eslesen):,} satır)")

    _bas("HATIRLATMA")
    print("  Bu liste bir ETİKET DEĞİL, aday havuzudur. Filtre kelime + OKAS")
    print("  eşleşmesidir; 'Sondaj ve Workover Kuleleri Kamera Sistemi' de geçer.")
    print("  Kontrastif koleksiyona doğrudan beslenirse SIZINTI üretir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
