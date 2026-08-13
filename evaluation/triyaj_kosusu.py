"""
Triyaj koşusu — YANLIŞ ALARM ölçümünün ilk adımı.

    python evaluation/triyaj_kosusu.py                    # 100 rastgele aktif ihale
    python evaluation/triyaj_kosusu.py --n 50 --seed 7
    python evaluation/triyaj_kosusu.py --n 10             # önce küçük bir deneme

=============================================================================
NEDEN BU SCRIPT VAR
=============================================================================
Mevcut iki test setinin ikisi de tek yönü ölçüyor:

    karar-seti-v1 (ayar)  15 örnek, içinde sadece 4 `uygun_degil`
    kacirma-seti-v1       33 örnek, HEPSİ `uygun`

"Her ihaleye uygun de" diyen sahte bir model kaçırma setinde 33/33 alır ve ELEME
hatası 0 çıkar — yani mevcut metriklerimiz onu FARK ETMEZ. 29.07'de yapılan üç
değişikliğin (kontrastif örnekler, mal/hizmet kuralı, think kapatma) üçü de
kapsayıcılık yönünde itti; bir kısmı iyileşme, bir kısmı dejenerasyon olabilir.
Ayırt edecek ölçüm yok.

İnsan onaylı `uygun_degil` verisi de elimizde yok.

=============================================================================
YÖNTEM — ETİKETLEME YÜKÜNÜ MODELE TRİYAJ YAPTIRARAK DÜŞÜRMEK
=============================================================================
Rastgele seçilmiş bir aktif ihale neredeyse kesin İSBAK'ın işi DEĞİL (yemek,
temizlik, ilaç, inşaat, akaryakıt...). Yani rastgele örneklemin ezici çoğunluğu
`uygun_degil` olmalı. Bu bize bedava bir negatif havuz verir:

  1. Sabit seed'le N rastgele aktif ihale seç
  2. Aşama 1'i koştur
  3. `uygun_degil` DEMEDİKLERİNİ ayrı bir CSV'ye çıkar -> insan sadece onlara bakar

Sistem sağlamsa 100'ün ~90'ına `uygun_degil` der ve elle 10 satır bakılır.
30'una `uygun` derse cevap zaten etiketlemeye gerek kalmadan gelmiştir.

BU BİR DOĞRULUK ÖLÇÜMÜ DEĞİLDİR. Etiket yok; ölçtüğü şey KARAR DAĞILIMI ve
skorların nereye yığıldığı. Etiketli yanlış alarm seti bu koşunun çıktısından
kurulacak.

YAN ÜRÜN: devir dokümanı madde 10 — `paketleri_bul` eşiksiz çalışıyor, benzerlik
ne olursa olsun en yakın 5 paketi döndürüyor. Alakasız ihalelerde retrieval
skorlarının ne çıktığı burada görülecek; `min_skor` eşiği için veri toplanıyor.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.ortam import ortam_ozeti, ozet_satiri  # noqa: E402
from app.pipeline.servis import servis_olustur  # noqa: E402

CIKTI_DIZINI = Path(__file__).resolve().parents[1] / "Sonuclar" / "triyaj"

# İnsan bu sütunu elle dolduracak. Boş bırakılan satır "henüz bakılmadı" demektir.
ETIKET_SUTUNU = "insan_karar"
ETIKET_SECENEKLERI = "uygun / belirsiz / uygun_degil"


def _histogram(skorlar: list[float], genislik: int = 40) -> str:
    """0.0-1.0 arasını 10 kovaya böler. Skorların nereye yığıldığını görmek için."""
    kovalar = [0] * 10
    for s in skorlar:
        kovalar[min(9, int(s * 10))] += 1
    en_buyuk = max(kovalar) or 1
    satirlar = []
    for i, adet in enumerate(kovalar):
        cubuk = "█" * round(genislik * adet / en_buyuk)
        isaret = "  <- karar eşiği" if i == 5 else ""
        satirlar.append(f"  {i/10:.1f}-{(i+1)/10:.1f} {adet:4d} {cubuk}{isaret}")
    return "\n".join(satirlar)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100, help="Kaç rastgele ihale (varsayılan 100)")
    ap.add_argument(
        "--seed", type=int, default=42,
        help="Örneklem seed'i. AYNI SEED = AYNI ÖRNEKLEM; koşular kıyaslanabilir kalsın diye.",
    )
    ap.add_argument("--not", dest="aciklama", default="", help="Bu koşuya dair serbest not")
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    servis = servis_olustur(ayarlar, sadece_asama1=True)

    # --- Örneklem: önce sadece İKN'leri çek, sonra seçilenlerin gövdesini getir ---
    tum_iknler = servis.depo.aktif_ihale_iknleri()
    if a.n > len(tum_iknler):
        print(f"! {a.n} istendi ama sadece {len(tum_iknler)} aktif ihale var; hepsi alınıyor.")
    secilen = random.Random(a.seed).sample(tum_iknler, min(a.n, len(tum_iknler)))

    print(f"\nTRİYAJ KOŞUSU — {len(secilen)} rastgele aktif ihale (havuz {len(tum_iknler)})")
    print(f"seed={a.seed} | model={ayarlar.birincil_model} | think={ayarlar.llm_dusunme} "
          f"| veri={ayarlar.data_backend}")
    print(f"min_skor={ayarlar.retrieval_min_skor} | paket_dogrula={ayarlar.paket_dogrula} "
          f"| destekleyici_kurali={ayarlar.destekleyici_paket_kurali}")
    print(f"ortam: {ozet_satiri()}")
    print("\nBU BİR DOĞRULUK ÖLÇÜMÜ DEĞİL — etiket yok. Karar dağılımına bakılıyor.\n", flush=True)

    detaylar: list[dict] = []
    dagilim: Counter = Counter()
    hata = 0
    t_basla = time.time()

    for i, ikn in enumerate(secilen, 1):
        t0 = time.time()
        adi = paket = gerekce = ""
        karar = "HATA"
        skor = en_ust = None
        on_filtre = None
        # `tender_id` ZORUNLU: etiketlenen dosya sonra evaluate.py'nin okuduğu
        # şemaya çevrilecek ve orası ihaleyi id ile getiriyor (`id_ile_getir`).
        tender_id = idare = tur = ""
        try:
            ihale = servis.depo.ikn_ile_getir(ikn)
            tender_id = ihale.id
            idare = (ihale.idare_adi or "").replace("\n", " ").strip()
            tur = ihale.ihale_turu or ""
            adi = (ihale.adi or "").replace("\n", " ").strip()
            sonuc = servis.ihaleyi_analiz_et(ihale)
            on_filtre = sonuc.on_filtre_kurali
            if sonuc.kapsam is not None:
                k = sonuc.kapsam
                karar, skor, paket, gerekce = k.karar, k.ilgi_skoru, k.eslesen_paket or "", k.gerekce
            else:
                # Ön-filtre LLM'i hiç çağırmadan kesmiş (İSBAK kendi ihalesi / tarih geçmiş).
                karar = "on_filtre"
                gerekce = on_filtre or ""
            if sonuc.kullanilan_paketler:
                en_ust = sonuc.kullanilan_paketler[0].get("benzerlik")
        except Exception as e:  # noqa: BLE001 — tek örnek tüm koşuyu düşürmesin
            gerekce = f"{type(e).__name__}: {e}"
            hata += 1
        sure = round(time.time() - t0, 1)
        # Ön-filtre kararlarını AYRI say: onlar LLM'in değil kodun kararı (İSBAK'ın
        # kendi ihalesi). `uygun_degil` sayısına karışırlarsa sistem olduğundan
        # seçici görünür.
        dagilim[f"{karar} [ön-filtre]" if on_filtre else karar] += 1

        detaylar.append({
            "tender_id": tender_id, "ikn": ikn, "adi": adi,
            "idare_adi": idare, "ihale_turu": tur,
            "karar": karar, "ilgi_skoru": skor,
            "retrieval_en_ust_skor": en_ust, "eslesen_paket": paket,
            "on_filtre_kurali": on_filtre, "gerekce": gerekce, "sure_sn": sure,
        })

        # Canlı akış — tek tek bakabilmen için karar ve skor önde.
        isaret = {"uygun": "!!", "belirsiz": " ?", "uygun_degil": "  ",
                  "on_filtre": " -", "HATA": "XX"}.get(karar, "  ")
        print(f"[{i:3d}/{len(secilen)}] {isaret} {karar:11s} skor={str(skor):6s} "
              f"{sure:5.1f}sn  {adi[:58]}", flush=True)
        if karar in ("uygun", "belirsiz"):
            print(f"           └─ {gerekce[:150]}", flush=True)

    toplam = time.time() - t_basla
    n = len(secilen)
    # Ön-filtreyle kesilenlerin skoru 0.0'dır (gerçek bir yargı değil) — histogramı bozmasın.
    skorlar = [
        d["ilgi_skoru"] for d in detaylar
        if isinstance(d["ilgi_skoru"], (int, float)) and not d["on_filtre_kurali"]
    ]
    incelenecek = [d for d in detaylar if d["karar"] in ("uygun", "belirsiz")]
    llm_gordu = sum(1 for d in detaylar if not d["on_filtre_kurali"] and d["karar"] != "HATA")

    rapor = {
        "tur": "triyaj",
        "model": ayarlar.birincil_model,
        "tarih": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "not": a.aciklama,
        "seed": a.seed,
        "havuz_boyutu": len(tum_iknler),
        "konfig": {
            "veri_kaynagi": ayarlar.data_backend,
            "embedding": ayarlar.embedding_backend,
            "paket_k": ayarlar.retrieval_paket_k,
            "ornek_k": ayarlar.retrieval_ornek_k,
            "num_ctx": ayarlar.num_ctx,
            "seed_llm": ayarlar.llm_seed,
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
        },
        "ortam": ortam_ozeti(),
        "toplam_ornek": n,
        "toplam_sure_sn": round(toplam, 1),
        "hata_sayisi": hata,
        "karar_dagilimi": dict(dagilim),
        "llm_karar_verdi": llm_gordu,
        "incelenecek_sayisi": len(incelenecek),
        "detaylar": detaylar,
    }

    CIKTI_DIZINI.mkdir(parents=True, exist_ok=True)
    surum = 1
    while (CIKTI_DIZINI / f"v{surum}.json").exists():
        surum += 1
    json_yolu = CIKTI_DIZINI / f"v{surum}.json"
    json_yolu.write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")

    alanlar = ["tender_id", "ikn", "adi", "idare_adi", "ihale_turu",
               "karar", "ilgi_skoru", "retrieval_en_ust_skor",
               "eslesen_paket", "on_filtre_kurali", "gerekce", "sure_sn"]
    tam_csv = CIKTI_DIZINI / f"v{surum}_tam.csv"
    with open(tam_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=alanlar, delimiter=";")
        w.writeheader()
        w.writerows(detaylar)

    # Etiketleme sayfası: SADECE uygun/belirsiz. `uygun_degil` denenler zaten
    # beklenen davranış — onlara bakmak zaman kaybı.
    etiket_csv = CIKTI_DIZINI / f"v{surum}_incelenecek.csv"
    with open(etiket_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[ETIKET_SUTUNU] + alanlar, delimiter=";")
        w.writeheader()
        for d in incelenecek:
            w.writerow({ETIKET_SUTUNU: "", **d})

    print(f"\n{'='*74}\nÖZET — {toplam/60:.1f} dakika, {toplam/max(1,n):.1f} sn/ihale\n{'='*74}")
    print("Karar dağılımı:")
    for k, v in dagilim.most_common():
        print(f"  {k:12s} {v:4d}  %{100*v/n:.0f}")
    if hata:
        print(f"  (hata: {hata})")

    print(f"\nSkor dağılımı — SADECE LLM'in gördükleri (n={len(skorlar)}):")
    print(_histogram(skorlar) if skorlar else "  (skor yok)")
    print("  Not: retrieval eşiksiz çalışıyor (devir dokümanı madde 10). Alakasız")
    print("  ihalelerde bile en yakın 5 paket dönüyor; skorların nereye yığıldığı")
    print("  `min_skor` eşiği için veri.")

    uygun = dagilim.get("uygun", 0)
    payda = max(1, llm_gordu)
    print(f"\nİNSAN İNCELEMESİNE ÇIKAN: {len(incelenecek)}/{llm_gordu}  "
          f"(%{100*len(incelenecek)/payda:.0f} — LLM'in karar verdikleri içinde)")
    print("Rastgele örneklemde bu oranın DÜŞÜK olması beklenir — rastgele bir ihale")
    print("neredeyse kesin İSBAK'ın işi değildir.")
    if uygun / payda > 0.30:
        print(f"\n!! UYARI: örneklemin %{100*uygun/payda:.0f}'ine 'uygun' dendi. Bu oran")
        print("   rastgele bir havuz için çok yüksek — sistem aşırı kapsayıcı olabilir.")
        print("   Etiketlemeye başlamadan önce birkaç örneği gözle kontrol edin.")

    print(f"\nYazıldı:\n  {json_yolu}\n  {tam_csv}\n  {etiket_csv}   <-- ETİKETLENECEK DOSYA")
    print(f"\nEtiketleme: '{ETIKET_SUTUNU}' sütununu doldur ({ETIKET_SECENEKLERI}).")
    print("Boş bırakılan satır 'henüz bakılmadı' sayılır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
