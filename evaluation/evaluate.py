"""
Regresyon değerlendirmesi — AŞAMA 1 (kapsam) kararını etiketli sete karşı ölçer.

    python evaluation/evaluate.py --subset final
    python evaluation/evaluate.py --subset ayar --model qwen3:4b
    python evaluation/evaluate.py --subset final --not "kontrastif ornekler acik"

NEDEN SADECE AŞAMA 1 ÖLÇÜLÜYOR: elimizdeki 35 örneklik etiketli set KAPSAM etiketleri
içeriyor (uygun/belirsiz/uygun_degil). Aşama 2 (yeterlilik) için etiketli veri HENÜZ
YOK — şirket profillerindeki belge/proje kayıtları da boş. Aşama 2'yi bu setle ölçmek
anlamsız sayı üretir. Aşama 2 ölçümü ayrı bir set gerektirir (bkz. plan Faz 4).

KIYASLANABİLİRLİK: Metrikler (doğruluk, kaçırma oranı, yanlış alarm oranı, karar
matrisi) her koşuda birebir aynı tanımla hesaplanır ve sonuç dosyaları
`Sonuclar/<model>/<set>/vN.json` deseninde sürüm sürüm saklanır. Böylece iki koşu
doğrudan karşılaştırılabilir.

  kaçırma (sert)    = gerçek 'uygun'    -> tahmin 'uygun_degil'   (EN PAHALI HATA)
  kaçırma (yumuşak) = gerçek 'belirsiz' -> tahmin 'uygun_degil'
  eleme hatası      = ikisinin toplamı — İNSANA HİÇ ULAŞMAYAN ihaleler
  yanlış alarm      = gerçek 'uygun_degil' -> tahmin 'uygun'

NEDEN "YUMUŞAK KAÇIRMA" AYRI ÖLÇÜLÜYOR (28.07.2026'da eklendi): v1'den v2'ye geçişte
doğruluk %80'den %73,3'e düştü ama sert kaçırma ve yanlış alarm İKİSİ DE 0,0 kaldı —
yani metrik "her şey yolunda" dedi. Oysa gerçekte olan şuydu:

    belirsiz -> uygun        : 3 -> 2
    belirsiz -> uygun_degil  : 0 -> 2      <-- metriğin GÖRMEDİĞİ gerileme

`belirsiz` etiketli bir ihaleye 'uygun_degil' demek, o ihaleyi insan incelemesine hiç
göndermemek demektir; kapsam dışı sayılıp elenir. Bu, 'uygun'a kaydırmaktan çok daha
pahalı bir hatadır — sistemin varlık sebebi zaten şüpheli ihaleleri insana taşımaktı.
Sadece sert kaçırmaya bakmak, tam da bu gerilemeye karşı kör bırakıyordu.

FINAL SETİ KUTSAL: `--subset final` yalnızca RAPOR EDİLECEK ölçüm içindir. Prompt
ayarı, few-shot seçimi, eşik değiştirme gibi iterasyonlar SADECE `--subset ayar` ile
yapılır. Kontrastif örnek koleksiyonuna da final satırları hiç girmez (bkz.
scripts/index_profiles.py).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.ortam import ortam_ozeti, ozet_satiri  # noqa: E402
from app.pipeline.servis import servis_olustur  # noqa: E402

VARSAYILAN_SET = Path(__file__).parent / "karar-seti-v1.csv"
SONUCLAR = Path(__file__).resolve().parents[1] / "Sonuclar"


def _kullanim_ortalamasi(detaylar: list[dict]) -> dict:
    """Token/süre sayaçlarının ortalaması. Sayaç gelmeyen koşuda boş sözlük döner."""
    kayitlar = [d["kullanim"] for d in detaylar if d.get("kullanim")]
    if not kayitlar:
        return {}
    anahtarlar = {a for k in kayitlar for a in k}
    ort = {}
    for a in sorted(anahtarlar):
        degerler = [k[a] for k in kayitlar if a in k]
        if degerler:
            ort[a] = round(sum(degerler) / len(degerler), 1)
    return ort


def seti_oku(yol: Path, subset: str) -> list[dict]:
    with open(yol, encoding="utf-8-sig") as f:
        satirlar = list(csv.DictReader(f, delimiter=";"))
    if subset == "hepsi":
        return satirlar
    return [s for s in satirlar if (s.get("set") or "").strip() == subset]


def sonraki_versiyon(model: str, subset: str) -> Path:
    klasor = SONUCLAR / model.replace(":", "_") / subset
    klasor.mkdir(parents=True, exist_ok=True)
    mevcut = []
    for d in klasor.glob("v*.json"):
        try:
            mevcut.append(int(d.stem[1:]))
        except ValueError:
            pass
    return klasor / f"v{max(mevcut, default=0) + 1}.json"


def csv_yaz(rapor: dict, json_yolu: Path) -> Path:
    yol = json_yolu.with_suffix(".csv")
    ust = {a: rapor[a] for a in ("model", "subset", "tarih", "dogruluk", "kacirma_orani", "yanlis_alarm_orani")}
    alanlar = list(ust) + [
        "tender_id", "adi", "gercek_karar", "tahmin_karar", "eslesiyor",
        "ilgi_skoru", "eslesen_paket", "gerekce", "sure_sn",
        "retrieval_en_ust_skor", "skor_yankisi", "dusunce_uzunlugu",
    ]
    # `extrasaction="ignore"`: detaylara yeni teşhis alanı eklendiğinde (ör.
    # `kullanim` token sayaçları) CSV yazımı PATLAMASIN. Rapor JSON'u zaten
    # tüm alanları taşıyor; CSV insan gözü için sabit bir alt küme.
    with open(yol, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=alanlar, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for satir in rapor["detaylar"]:
            w.writerow({**ust, **satir})
    return yol


def main() -> int:
    ap = argparse.ArgumentParser()
    # Serbest metin: yerleşik "ayar"/"final"/"hepsi" dışında özel setler de olabilir
    # (ör. "kacirma" — bkz. evaluation/kacirma_seti_uret.py).
    ap.add_argument("--subset", default="final")
    ap.add_argument("--data", type=Path, default=VARSAYILAN_SET)
    ap.add_argument("--model", default=None, help="Boş bırakılırsa .env'deki BIRINCIL_MODEL")
    ap.add_argument("--k", type=int, default=None, help="Retrieval'da kaç paket getirilecek")
    ap.add_argument("--not", dest="aciklama", default="", help="Bu koşuya dair serbest not")
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    if a.model:
        ayarlar.birincil_model = a.model
    if a.k:
        ayarlar.retrieval_paket_k = a.k

    satirlar = seti_oku(a.data, a.subset)
    if not satirlar:
        print(f"'{a.subset}' alt kümesinde satır yok: {a.data}")
        return 1

    if a.subset == "final":
        print("!" * 74)
        print("! FINAL SET — bu ölçüm RAPOR İÇİNDİR. Sonuca bakıp prompt/eşik ayarlamayın;")
        print("! iterasyon için --subset ayar kullanın. (Final'e bakarak ayar yapmak, ölçümü")
        print("! kendi kendine doğrulayan bir şeye çevirir ve sayıyı anlamsızlaştırır.)")
        print("!" * 74)

    # Aşama 2 kapalı: bu set kapsam etiketleri içeriyor, yeterlilik değil.
    servis = servis_olustur(ayarlar, sadece_asama1=True)
    print(f"\n{len(satirlar)} örnek | subset={a.subset} | model={ayarlar.birincil_model} "
          f"| veri={ayarlar.data_backend} | k={ayarlar.retrieval_paket_k} "
          f"| seed={ayarlar.llm_seed}")
    print(f"ortam: {ozet_satiri()}\n", flush=True)

    detaylar, matris = [], {}
    dogru = kacirma = yumusak_kacirma = yanlis_alarm = hata = 0
    t_basla = time.time()

    for i, satir in enumerate(satirlar, 1):
        gercek = (satir.get("karar") or "").strip()
        adi = (satir.get("adi") or "")[:46]
        print(f"[{i:2d}/{len(satirlar)}] {adi:<46s}", end=" ", flush=True)

        t0 = time.time()
        # Bu iki alan TEŞHİS içindir, metriğe girmez:
        #   en_ust_benzerlik -> ilgi_skoru'nun retrieval kosinüsünün yankısı olup
        #                       olmadığını sonradan ölçebilmek için (29.07 şüphesi).
        #   dusunce_uzunlugu -> qwen3'ün düşünme modu ne kadar token yakıyor.
        en_ust_benzerlik = dusunce_uzunlugu = None
        kullanim: dict = {}
        try:
            ihale = servis.depo.id_ile_getir(satir["tender_id"])
            sonuc = servis.ihaleyi_analiz_et(ihale)
            k = sonuc.kapsam
            tahmin, skor, paket, gerekce = k.karar, k.ilgi_skoru, k.eslesen_paket, k.gerekce
            if sonuc.kullanilan_paketler:
                en_ust_benzerlik = sonuc.kullanilan_paketler[0].get("benzerlik")
            dusunce_uzunlugu = len(getattr(servis.birincil, "son_dusunce", "") or "")
            # Ollama'nın kendi token/süre sayaçları — prefill/decode ayrımı tahminle
            # değil ölçümle bilinsin (bkz. llm_client::_kullanim).
            kullanim = dict(getattr(servis.birincil, "son_kullanim", {}) or {})
        except Exception as e:  # noqa: BLE001 — tek örnek tüm koşuyu düşürmesin
            tahmin, skor, paket, gerekce = "HATA", None, None, f"{type(e).__name__}: {e}"
            hata += 1
        sure = round(time.time() - t0, 1)

        eslesiyor = tahmin == gercek
        dogru += int(eslesiyor)
        if gercek == "uygun" and tahmin == "uygun_degil":
            kacirma += 1
        # Yumuşak kaçırma: insana gitmesi gereken ihale sessizce elendi.
        if gercek == "belirsiz" and tahmin == "uygun_degil":
            yumusak_kacirma += 1
        if gercek == "uygun_degil" and tahmin == "uygun":
            yanlis_alarm += 1
        matris.setdefault(gercek, Counter())[tahmin] += 1

        detaylar.append({
            "tender_id": satir["tender_id"], "adi": satir.get("adi", ""),
            "gercek_karar": gercek, "tahmin_karar": tahmin, "eslesiyor": eslesiyor,
            "ilgi_skoru": skor, "eslesen_paket": paket, "gerekce": gerekce, "sure_sn": sure,
            "retrieval_en_ust_skor": en_ust_benzerlik,
            "skor_yankisi": (
                skor is not None and en_ust_benzerlik is not None
                and abs(skor - en_ust_benzerlik) < 1e-9
            ),
            "dusunce_uzunlugu": dusunce_uzunlugu,
            "kullanim": kullanim,
        })

        # Koşan sayaç: her satırda o ana kadarki doğruluk. Uzun koşularda gidişatı
        # sonu beklemeden görmek için.
        print(
            f"[{'OK ' if eslesiyor else 'FARK'}] {gercek:11s}->{tahmin:11s} "
            f"ilgi={str(skor):6s} {sure:5.1f}sn   {dogru:2d}/{i:<2d} = %{100*dogru/i:.0f}",
            flush=True,
        )
        if not eslesiyor:
            print(f"         └─ {gerekce[:150]}", flush=True)

    n = len(satirlar)
    toplam = time.time() - t_basla
    rapor = {
        "model": ayarlar.birincil_model,
        "subset": a.subset,
        "tarih": datetime.now().isoformat(timespec="seconds"),
        "not": a.aciklama,
        "konfig": {
            "veri_kaynagi": ayarlar.data_backend,
            "embedding": ayarlar.embedding_backend,
            "paket_k": ayarlar.retrieval_paket_k,
            "ornek_k": ayarlar.retrieval_ornek_k,
            "num_ctx": ayarlar.num_ctx,
            "seed": ayarlar.llm_seed,
            "dusunme": ayarlar.llm_dusunme,
            "num_gpu": ayarlar.llm_num_gpu,
            "min_skor": ayarlar.retrieval_min_skor,
            "paket_dogrula": ayarlar.paket_dogrula,
            "destekleyici_kurali": ayarlar.destekleyici_paket_kurali,
            "okas_vetosu": ayarlar.okas_vetosu,
            "nitel_yakinlik": ayarlar.nitel_yakinlik,
            "negatif_dogrula": ayarlar.negatif_dogrula,
            "faaliyet_ortusmesi": ayarlar.faaliyet_ortusmesi,
            "b1_alan_terimi_istisnasi": ayarlar.b1_alan_terimi_istisnasi,
            "asama": "sadece_asama1_kapsam",
        },
        # Teşhis toplamları — metrik DEĞİL, yorum için (bkz. detaylardaki aynı adlı alanlar).
        "skor_yankisi_sayisi": sum(1 for d in detaylar if d.get("skor_yankisi")),
        "ortalama_dusunce_uzunlugu": (
            round(sum(d["dusunce_uzunlugu"] for d in detaylar
                      if d.get("dusunce_uzunlugu") is not None)
                  / max(1, sum(1 for d in detaylar if d.get("dusunce_uzunlugu") is not None)))
        ),
        # Ollama'nın kendi token/süre sayaçlarının ortalaması. `uretim_payi` en
        # önemlisi: sürenin yüzde kaçı ÜRETİMDE geçti. Prompt kısaltmanın kazanç
        # tavanı (100 - uretim_payi)'dir; düşünme kapatmanınki `uretim_payi`.
        "ortalama_kullanim": _kullanim_ortalamasi(detaylar),
        # Farklı makinelerde alınan koşular doğrudan kıyaslanamaz (bkz. app/ortam.py)
        "ortam": ortam_ozeti(),
        "toplam_ornek": n,
        "toplam_sure_sn": round(toplam, 1),
        "hata_sayisi": hata,
        "dogruluk": round(dogru / n, 3),
        "kacirma_sayisi": kacirma,
        "kacirma_orani": round(kacirma / n, 3),
        "yumusak_kacirma_sayisi": yumusak_kacirma,
        "yumusak_kacirma_orani": round(yumusak_kacirma / n, 3),
        "eleme_hatasi_sayisi": kacirma + yumusak_kacirma,
        "eleme_hatasi_orani": round((kacirma + yumusak_kacirma) / n, 3),
        "yanlis_alarm_sayisi": yanlis_alarm,
        "yanlis_alarm_orani": round(yanlis_alarm / n, 3),
        "karar_matrisi": {g: dict(t) for g, t in matris.items()},
        "detaylar": detaylar,
    }

    yol = sonraki_versiyon(ayarlar.birincil_model, a.subset)
    yol.write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_yolu = csv_yaz(rapor, yol)

    eleme = kacirma + yumusak_kacirma
    print(f"\n--- ÖZET ({ayarlar.birincil_model}, {toplam:.0f}sn) ---")
    print(f"Doğruluk                          : {rapor['dogruluk']}  ({dogru}/{n})")
    print(f"ELEME HATASI (insana ulaşmayan)   : {rapor['eleme_hatasi_orani']}  ({eleme}/{n})   <-- ASIL BAKILACAK")
    print(f"  ├─ sert kaçırma  (uygun->degil) : {rapor['kacirma_orani']}  ({kacirma}/{n})")
    print(f"  └─ yumuşak       (belirsiz->degil): {rapor['yumusak_kacirma_orani']}  ({yumusak_kacirma}/{n})")
    print(f"Yanlış alarm oranı                : {rapor['yanlis_alarm_orani']}  ({yanlis_alarm}/{n})")
    yanki = rapor["skor_yankisi_sayisi"]
    if yanki:
        print(f"! SKOR YANKISI                    : {yanki}/{n} ihalede ilgi_skoru, retrieval")
        print(f"                                    en üst skoruna BİREBİR eşit — model kendi")
        print(f"                                    skorunu üretmiyor, prompt'takini kopyalıyor.")
    if rapor["ortalama_dusunce_uzunlugu"]:
        print(f"Düşünme (ortalama)                : {rapor['ortalama_dusunce_uzunlugu']} karakter"
              f"  (think={ayarlar.llm_dusunme})")
    ku = rapor.get("ortalama_kullanim") or {}
    if ku:
        print(f"Token (ortalama)                  : girdi {ku.get('girdi_token','?')} -> "
              f"çıktı {ku.get('cikti_token','?')}")
        print(f"  üretim hızı                     : {ku.get('cikti_token_sn','?')} token/sn")
        if ku.get("uretim_payi") is not None:
            print(f"  SÜRENİN ÜRETİMDE GEÇEN PAYI     : %{ku['uretim_payi']}   <-- prompt kısaltmanın")
            print(f"                                    tavanı %{round(100-ku['uretim_payi'],1)}")
    if hata:
        print(f"Teknik hata                       : {hata}")
    print("Karar matrisi (gerçek -> tahmin):")
    for g, t in matris.items():
        print(f"  {g:12s} -> {dict(t)}")
    if n < 30:
        print(f"\nUYARI: n={n} — bu boyutta tek bir örnek doğruluğu ~%{100/n:.0f} oynatır.")
        print("Küçük farkları anlamlı kabul etmeyin — örneklem sayısı sınırlı.")
    print(f"\nJSON: {yol}\nCSV : {csv_yolu}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
