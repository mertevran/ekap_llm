"""
Retrieval eşiği analizi — `min_skor` değerini VERİDEN seçmek için. LLM ÇAĞIRMAZ.

    python evaluation/esik_analizi.py                 # 300 rastgele + etiketli setler
    python evaluation/esik_analizi.py --rastgele 500

=============================================================================
NEDEN BU SCRIPT VAR
=============================================================================
29.07 triyaj koşusu: 10 rastgele aktif ihalenin 6'sına `uygun` dendi. Aralarında
cami inşaatı, okul onarımı, öğrenci taşıma servisi var.

Kök sebep `paketleri_bul`un EŞİKSİZ çalışması (devir dokümanı madde 10): benzerlik
ne olursa olsun en yakın 5 paket dönüyor ve prompt'a **"İLGİLİ İŞ PAKETLERİ"**
başlığıyla yazılıyor. Kontrastif örnekler için de aynısı: cami inşaatına
**"BENZER GEÇMİŞ İHALELER — ETİKET: UYGUN"** diye trafik projeleri gösteriliyor.
Model bu girdiyle ne yapsa savunulabilir; ona İSBAK'ın alanına girdiği söylenmiş.

Çözüm bir `min_skor` eşiği. AMA eşik tahminle seçilmez — bu script veriden seçtirir.

=============================================================================
YÖNTEM
=============================================================================
Üç grubun EN ÜST paket benzerliği karşılaştırılır:

    rastgele    N aktif ihale       -> beklenen NEGATİF dağılımı (ezici çoğunluk
                                       İSBAK'ın işi değildir)
    kacirma     33 ihale            -> insan onaylı POZİTİF dağılımı
    ayar/final  etiketine göre      -> uygun / belirsiz / uygun_degil ayrı ayrı

Sonra her aday eşik için iki sayı basılır:

    ELENEN NEGATİF   eşiğin altında kalan rastgele ihale oranı  (kazanç)
    ELENEN POZİTİF   eşiğin altında kalan `uygun` ihale oranı   (BEDEL — kaçırma)

İyi bir eşik ilkini yükseltip ikincisini 0'da tutandır. Dağılımlar tamamen üst
üste biniyorsa EŞİK ÇÖZÜM DEĞİLDİR — bunu da bu script söyler.

=============================================================================
EŞİK NASIL UYGULANMALI (ölçüm bittiğinde)
=============================================================================
İki seçenek var, bu script ikisini de sayısallaştırır:

  (a) SERT: eşiği geçen paket yoksa LLM hiç çağrılmaz -> `uygun_degil`.
      Hızlı ve ucuz AMA bir kaçırma kapısı. `ELENEN POZİTİF` 0 değilse
      DOĞRUDAN kaçırma üretir. Projenin değişmez ilkesi gereği riskli.

  (b) YUMUŞAK: LLM yine çağrılır ama prompt'a sahte bir "ilgili paketler"
      listesi konmaz; "hiçbir iş paketiyle anlamlı örtüşme bulunamadı" denir.
      Kaçırma riski yok, sadece modelin girdisi dürüstleşir.

VARSAYILAN TERCİH (b) — kaçırma en pahalı hatadır. (a) sadece `ELENEN POZİTİF`
geniş bir eşik aralığında tam 0 çıkarsa tartışılır.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics as ist
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.database.base import depo_olustur  # noqa: E402
from app.domain.models import kapsam_metni  # noqa: E402
from app.embedding.embedders import embedder_olustur  # noqa: E402
from app.retrieval.profil_retriever import ProfilRetriever, sorgu_metni  # noqa: E402

KOK = Path(__file__).resolve().parents[1]
KARAR_SETI = Path(__file__).parent / "karar-seti-v1.csv"
KACIRMA_SETI = Path(__file__).parent / "kacirma-seti-v1.csv"
CIKTI = KOK / "Sonuclar" / "esik_analizi.json"

ADAY_ESIKLER = [0.30, 0.35, 0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.56]


def _yuzdelik(degerler: list[float], p: float) -> float:
    if not degerler:
        return float("nan")
    s = sorted(degerler)
    i = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[i]


def _ozet(ad: str, d: list[float]) -> str:
    if not d:
        return f"  {ad:22s}    (veri yok)"
    return (
        f"  {ad:22s} n={len(d):4d}  "
        f"min={min(d):.3f}  p10={_yuzdelik(d,10):.3f}  medyan={ist.median(d):.3f}  "
        f"p90={_yuzdelik(d,90):.3f}  maks={max(d):.3f}"
    )


def _histogram(d: list[float], genislik: int = 34) -> list[str]:
    kovalar = defaultdict(int)
    for x in d:
        kovalar[min(9, int(x * 10))] += 1
    en_buyuk = max(kovalar.values()) if kovalar else 1
    return [
        f"    {i/10:.1f}-{(i+1)/10:.1f} {kovalar[i]:4d} " + "█" * round(genislik * kovalar[i] / en_buyuk)
        for i in range(10)
    ]


def _seti_oku(yol: Path) -> list[dict]:
    if not yol.exists():
        return []
    with open(yol, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rastgele", type=int, default=300, help="Kaç rastgele aktif ihale")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    depo = depo_olustur(ayarlar)
    embedder = embedder_olustur(ayarlar)
    retriever = ProfilRetriever(ayarlar.qdrant_yolu, embedder)

    print(f"\nEŞİK ANALİZİ — LLM ÇAĞRILMIYOR (sadece embedding + Qdrant)")
    print(f"embedding={ayarlar.embedding_backend}/{ayarlar.embedding_model} "
          f"| paket_k={ayarlar.retrieval_paket_k}\n")

    gruplar: dict[str, list[float]] = defaultdict(list)
    ornekler: dict[str, list[dict]] = defaultdict(list)

    def _isle(grup: str, ihale) -> None:
        metin = sorgu_metni(ihale.adi or "", kapsam_metni(ihale))
        paketler = retriever.paketleri_bul(metin, k=ayarlar.retrieval_paket_k)
        if not paketler:
            return
        gruplar[grup].append(paketler[0].benzerlik)
        ornekler[grup].append({
            "ikn": ihale.ikn,
            "adi": (ihale.adi or "").replace("\n", " ")[:80],
            "en_ust_skor": paketler[0].benzerlik,
            "en_ust_paket": paketler[0].baslik,
        })

    try:
        # --- 1) Rastgele aktif ihaleler = beklenen negatif dağılımı ---
        iknler = depo.aktif_ihale_iknleri()
        secilen = random.Random(a.seed).sample(iknler, min(a.rastgele, len(iknler)))
        print(f"1) rastgele {len(secilen)} aktif ihale (havuz {len(iknler)})", flush=True)
        t0 = time.time()
        for i, ikn in enumerate(secilen, 1):
            try:
                _isle("rastgele", depo.ikn_ile_getir(ikn))
            except Exception as e:  # noqa: BLE001
                print(f"   ! {ikn}: {type(e).__name__}", flush=True)
            if i % 25 == 0:
                print(f"   {i}/{len(secilen)}  ({time.time()-t0:.0f} sn)", flush=True)

        # --- 2) Kaçırma seti = insan onaylı pozitifler ---
        kacirma = _seti_oku(KACIRMA_SETI)
        print(f"\n2) kaçırma seti — {len(kacirma)} insan onaylı `uygun`", flush=True)
        for s in kacirma:
            try:
                _isle("kacirma_uygun", depo.id_ile_getir(s["tender_id"]))
            except Exception as e:  # noqa: BLE001
                print(f"   ! {s.get('ikn')}: {type(e).__name__}", flush=True)

        # --- 3) Karar seti, etikete göre ---
        karar = _seti_oku(KARAR_SETI)
        print(f"3) karar seti — {len(karar)} etiketli örnek", flush=True)
        for s in karar:
            etiket = (s.get("karar") or "").strip()
            if not etiket:
                continue
            try:
                _isle(f"etiket_{etiket}", depo.id_ile_getir(s["tender_id"]))
            except Exception as e:  # noqa: BLE001
                print(f"   ! {s.get('ikn')}: {type(e).__name__}", flush=True)
    finally:
        retriever.kapat()

    # ---------------------------------------------------------------- RAPOR
    print(f"\n{'='*78}\nEN ÜST PAKET BENZERLİĞİ — DAĞILIMLAR\n{'='*78}")
    sira = ["rastgele", "kacirma_uygun", "etiket_uygun", "etiket_belirsiz", "etiket_uygun_degil"]
    for g in sira:
        print(_ozet(g, gruplar.get(g, [])))

    for g in ("rastgele", "kacirma_uygun"):
        if gruplar.get(g):
            print(f"\n  {g}:")
            print("\n".join(_histogram(gruplar[g])))

    # Pozitif havuz = insan onaylı uygunlar (kaçırma seti + karar setinin `uygun`ları)
    pozitif = gruplar.get("kacirma_uygun", []) + gruplar.get("etiket_uygun", [])
    negatif = gruplar.get("rastgele", [])

    print(f"\n{'='*78}\nADAY EŞİKLER\n{'='*78}")
    print(f"  {'eşik':>6}  {'elenen negatif':>16}  {'ELENEN POZİTİF':>16}   yorum")
    print(f"  {'':>6}  {'(kazanç)':>16}  {'(kaçırma bedeli)':>16}")
    print("  " + "-" * 74)
    en_iyi = None
    for e in ADAY_ESIKLER:
        if not negatif or not pozitif:
            break
        en = sum(1 for x in negatif if x < e) / len(negatif)
        ep = sum(1 for x in pozitif if x < e) / len(pozitif)
        yorum = ""
        if ep == 0:
            yorum = "pozitif kaybı YOK"
            if en_iyi is None or en > en_iyi[1]:
                en_iyi = (e, en)
        elif ep <= 0.03:
            yorum = "sınırda"
        else:
            yorum = "KAÇIRMA ÜRETİR"
        print(f"  {e:>6.2f}  {100*en:>15.0f}%  {100*ep:>15.0f}%   {yorum}")

    print(f"\n{'='*78}\nYORUM\n{'='*78}")
    if not negatif or not pozitif:
        print("  Yeterli veri toplanamadı.")
    elif en_iyi and en_iyi[1] >= 0.20:
        e, en = en_iyi
        print(f"  ÖNERİ: min_skor = {e:.2f}")
        print(f"  Rastgele ihalelerin %{100*en:.0f}'i eşiğin altında kalıyor ve hiçbir insan")
        print(f"  onaylı `uygun` ihale kaybedilmiyor. Bu ihalelerde prompt'a artık sahte bir")
        print(f"  'İLGİLİ İŞ PAKETLERİ' listesi konmayacak.")
        print(f"\n  UYGULAMA: yumuşak yol (b) — LLM yine çağrılsın, sadece girdisi dürüstleşsin.")
        print(f"  Sert yol (a) ancak bu tablo geniş bir aralıkta %0 pozitif kaybı gösterirse.")
    else:
        print("  DAĞILIMLAR AYRIŞMIYOR. Pozitif kaybı olmadan anlamlı sayıda negatif eleyen")
        print("  bir eşik yok — `min_skor` tek başına çözüm değil.")
        print("  Alternatifler: (1) prompt'taki 'İLGİLİ İŞ PAKETLERİ' başlığını nötrleştir")
        print("  ('en yakın paketler' + benzerliğin düşük olduğu açıkça yazılır),")
        print("  (2) hibrit skorlama (semantik + sözcüksel + OKAS), (3) profil metinlerini")
        print("  zenginleştir (`urunler_ve_hizmetler` alanları hâlâ BOŞ).")

    # En üst skoru YÜKSEK çıkan rastgele ihaleler: eşik ne olursa olsun geçecekler.
    # Yanlış alarmların geleceği yer burası — gözle bakmaya değer.
    riskli = sorted(ornekler.get("rastgele", []), key=lambda x: -x["en_ust_skor"])[:15]
    if riskli:
        print(f"\n{'='*78}\nEN YÜKSEK SKORLU 15 RASTGELE İHALE (yanlış alarm adayları)\n{'='*78}")
        for r in riskli:
            print(f"  {r['en_ust_skor']:.4f}  {r['en_ust_paket'][:30]:30s}  {r['adi'][:44]}")

    CIKTI.parent.mkdir(parents=True, exist_ok=True)
    CIKTI.write_text(json.dumps({
        "tarih": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rastgele_n": len(negatif), "seed": a.seed,
        "embedding": f"{ayarlar.embedding_backend}/{ayarlar.embedding_model}",
        "dagilimlar": {g: v for g, v in gruplar.items()},
        "ornekler": {g: v for g, v in ornekler.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nYazıldı: {CIKTI}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
