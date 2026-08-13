"""
Qdrant koleksiyonlarını kurar/doldurur.

    python scripts/index_profiles.py                      # sadece 20 profil
    python scripts/index_profiles.py --sifirla             # sıfırdan kur
    python scripts/index_profiles.py --ornek-seti evaluation/karar-seti-v1.csv

`--ornek-seti` verilirse kontrastif örnek koleksiyonları da doldurulur:
  uygun / belirsiz olanlar  -> isbak_ornek_uygun
  uygun_degil olanlar       -> isbak_ornek_red

=============================== SIZINTI GÜVENLİĞİ ===============================
Karar setinin `final` (held-out) kısmındaki hiçbir ihale örnek koleksiyonlarına
GİREMEZ. Girerse model, değerlendirileceği örneği kanıt olarak kendi bağlamında görür
ve ölçüm anlamsızlaşır. Bu kural burada kodla uygulanıyor ve `--izin-ver-final` gibi
bir kaçış kapısı BİLEREK YOK. Ayar seti (`set=ayar`) serbestçe kullanılabilir.
=================================================================================
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.database.base import depo_olustur  # noqa: E402
from app.domain.models import kapsam_metni  # noqa: E402
from app.embedding.embedders import embedder_olustur  # noqa: E402
from app.retrieval.profil_retriever import ornekleri_indeksle, profilleri_indeksle  # noqa: E402
from app.retrieval.qdrant_deposu import (  # noqa: E402
    KOLEKSIYON_ORNEK_BELIRSIZ,
    KOLEKSIYON_ORNEK_RED,
    KOLEKSIYON_ORNEK_UYGUN,
    KOLEKSIYON_PROFIL,
    ORNEK_KOLEKSIYONLARI,
    QdrantDeposu,
)


def ornekleri_oku(csv_yolu: Path, depo) -> tuple[dict[str, list[dict]], int]:
    """Karar setinden kontrastif örnekleri ÜÇ AYRI GRUBA ayırır. `final` satırları ATILIR.

    ÖNEMLİ (28.07.2026 düzeltmesi): ilk sürümde `belirsiz` örnekler `uygun` grubuna
    konuyordu ve modele "onaylanmış uygun örnek" diye gösteriliyordu. v1 koşusundaki
    üç hatanın da sebebi buydu — model gerekçelerinde bu örnekleri "geçmişte uygun
    bulunmuştu" diye alıntıladı. Artık her etiket kendi grubunda.
    """
    with open(csv_yolu, encoding="utf-8-sig") as f:
        satirlar = list(csv.DictReader(f, delimiter=";"))

    ayar = [s for s in satirlar if (s.get("set") or "").strip() == "ayar"]
    atilan = len(satirlar) - len(ayar)

    ihaleler = {i.id: i for i in depo.coklu_getir([s["tender_id"] for s in ayar], alan="id")}

    gruplar: dict[str, list[dict]] = {"uygun": [], "belirsiz": [], "uygun_degil": []}
    for s in ayar:
        ihale = ihaleler.get(s["tender_id"])
        karar = (s.get("karar") or "").strip()
        if ihale is None or karar not in gruplar:
            continue
        gruplar[karar].append(
            {"id": ihale.id, "baslik": ihale.adi or "", "metin": kapsam_metni(ihale)[:2000]}
        )

    return gruplar, atilan


_KOLEKSIYON_ESLEMESI = {
    "uygun": KOLEKSIYON_ORNEK_UYGUN,
    "belirsiz": KOLEKSIYON_ORNEK_BELIRSIZ,
    "uygun_degil": KOLEKSIYON_ORNEK_RED,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sifirla", action="store_true", help="Koleksiyonları silip yeniden kur")
    ap.add_argument("--ornek-seti", type=Path, help="Kontrastif örnekler için karar seti CSV'si")
    ap.add_argument(
        "--ornekleri-temizle",
        action="store_true",
        help=(
            "Üç örnek koleksiyonunu da BOŞALT (kontrolsüz taban ölçümü için). "
            "Sadece 20 profille, kontrastif kanıt olmadan koşmak istediğinizde kullanın."
        ),
    )
    ap.add_argument(
        "--belirsiz-yok",
        action="store_true",
        help=(
            "`belirsiz` örnek koleksiyonunu BOŞ bırak; sadece uygun + uygun_degil "
            "gösterilsin. GEREKÇE: ayar setinde en kalabalık grup `belirsiz` (6 vs 5 vs 4) "
            "ve o 6'nın 3'ü tartışmalı etiket — yani modele en az güvendiğimiz sınıftan "
            "en çok örnek veriyoruz. Model de zaten `belirsiz`e sığınıyor (60 etiketli "
            "ihalede gerçek `uygun_degil`lerin %%59'u `belirsiz`e düşüyor)."
        ),
    )
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    embedder = embedder_olustur(ayarlar)
    print(f"embedder: {embedder.imza} (boyut {embedder.boyut})")
    print(f"qdrant  : {ayarlar.qdrant_yolu}\n")

    with QdrantDeposu(ayarlar.qdrant_yolu, KOLEKSIYON_PROFIL) as d:
        n = profilleri_indeksle(d, embedder, sifirla=a.sifirla)
        print(f"  {KOLEKSIYON_PROFIL:24s} +{n:4d} kayıt (toplam {d.sayi()})")

    # KOLEKSİYON BAŞINA RAPOR — yazılan sayı DEĞİL, koleksiyonda GERÇEKTEN kalan sayı.
    #
    # 30.07.2026: bu script "isbak_ornek_uygun +5 kayıt" yazarken koleksiyonda 11 kayıt
    # duruyordu (Windows'ta delete_collection sessizce başarısız oluyor — bkz.
    # QdrantDeposu._kalintiyi_temizle). Sadece "+n" basıldığı için fark edilmedi ve
    # dört ölçüm çelişkili RAG ile koştu. Artık gerçek toplam basılıyor ve
    # beklenenden farklıysa satır [UYUŞMAZLIK] ile işaretleniyor.
    sorunlu = False

    def _rapor(ad: str, d: QdrantDeposu, yazilan: int, beklenen: int, aciklama: str) -> bool:
        gercek = d.sayi()
        kurtarilan = f"  [{d.son_kalinti} kalıntı silindi]" if d.son_kalinti else ""
        if gercek == beklenen:
            print(f"  {ad:24s} +{yazilan:4d} yazıldı, toplam {gercek:3d}  {aciklama}{kurtarilan}")
            return True
        print(
            f"  {ad:24s} +{yazilan:4d} yazıldı, TOPLAM {gercek:3d} — BEKLENEN {beklenen}"
            f"  [UYUŞMAZLIK]{kurtarilan}"
        )
        return False

    if a.ornekleri_temizle:
        print("\n  TABAN ÖLÇÜMÜ: üç örnek koleksiyonu da boşaltılıyor.")
        for ad in ORNEK_KOLEKSIYONLARI:
            with QdrantDeposu(ayarlar.qdrant_yolu, ad) as d:
                n = ornekleri_indeksle(d, embedder, [], sifirla=True)
                sorunlu |= not _rapor(ad, d, n, 0, "boşaltıldı")
    elif a.ornek_seti:
        depo = depo_olustur(ayarlar)
        gruplar, atilan = ornekleri_oku(a.ornek_seti, depo)
        print(f"\n  SIZINTI GÜVENLİĞİ: {atilan} 'final' satırı atlandı (asla indekslenmez).")
        if a.belirsiz_yok:
            gruplar["belirsiz"] = []
            print("  --belirsiz-yok: 'belirsiz' koleksiyonu BOŞ bırakılıyor.")
        print("  Her etiket KENDİ koleksiyonuna gidiyor (v1'deki karışma düzeltildi):")
        for etiket, ad in _KOLEKSIYON_ESLEMESI.items():
            beklenen = len(gruplar[etiket])
            with QdrantDeposu(ayarlar.qdrant_yolu, ad) as d:
                n = ornekleri_indeksle(d, embedder, gruplar[etiket], sifirla=True)
                bos = " (bilerek boş)" if etiket == "belirsiz" and a.belirsiz_yok else ""
                sorunlu |= not _rapor(ad, d, n, beklenen, f"<- etiket '{etiket}'{bos}")

    if sorunlu:
        print(
            "\n[HATA] En az bir koleksiyonda beklenmeyen kayıt kaldı. ÖLÇÜM ALMAYIN.\n"
            "  Qdrant klasörünü elle silip yeniden kurun:\n"
            f"    rmdir /s /q \"{ayarlar.qdrant_yolu}\"\n"
            "    python scripts\\index_profiles.py --sifirla --ornek-seti evaluation\\karar-seti-v1.csv"
        )
        return 1

    print("\nHazır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
