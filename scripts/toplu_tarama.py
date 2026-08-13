"""
Tüm aktif ihaleleri tarar ve sınıflandırır — projenin asıl çıktısı.

    python scripts/toplu_tarama.py --limit 20          # önce KÜÇÜK bir deneme
    python scripts/toplu_tarama.py                     # tamamı (~4.872 ihale)
    python scripts/toplu_tarama.py --devam             # kesilen koşuya devam et
    python scripts/toplu_tarama.py --ozet              # sadece mevcut sonucu raporla

NEDEN AYRI BİR SCRIPT (analyze_tender.py neden yetmiyor)
--------------------------------------------------------
`analyze_tender.py --aktif N` tüm ihaleleri ilanlarıyla birlikte belleğe alıyor
(~30MB) ve tek bir kesintide her şey baştan başlıyor. 4.872 ihalelik bir koşu
saatler sürer; elektrik kesintisi, uyku modu ya da Ctrl+C 20 saatlik işi çöpe
atamamalı. Bu script:

- AKITMALI okur — önce sadece İKN listesi, sonra tek tek ihale
- HER İHALEDEN SONRA DİSKE YAZAR (JSONL, append + flush + fsync)
- DEVAM EDEBİLİR — `--devam` ile biten İKN'leri atlar
- Ctrl+C'de temiz durur, yazılanlar korunur
- kalan süre tahmini gösterir

ÇIKTI TAKSONOMİSİ — Aşama 1'in üç sınıfı:
    uygun        -> aday, insan baksın
    belirsiz     -> aday, insan baksın (pratikte "inceleme gerekli" budur)
    uygun_degil  -> elendi

"inceleme_gerekli" AYRI BİR SINIF OLARAK ÇIKMAZ — o Aşama 2'nin etiketi ve Aşama 2
şirket kapasite verisi (belgeler, projeler, personel) doldurulmadan çalışamaz.
Aday liste = `uygun` + `belirsiz`.

SERT ÖN FİLTRE: `--sert-esik 0.44` verilirse retrieval benzerliği eşiğin altındaki
ihalelerde LLM hiç çağrılmaz. Ölçüldü: 0.44 altında tek bir insan onaylı pozitif
yok, rastgele ihalelerin %26'sı orada. Kapalıysa (0.0) her ihale modele gider.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.ortam import ortam_ozeti, ozet_satiri  # noqa: E402
from app.pipeline.servis import servis_olustur  # noqa: E402

VARSAYILAN_CIKTI = Path("Sonuclar/toplu_tarama")

_durduruldu = False


def _dur(signum, frame):  # noqa: ARG001
    global _durduruldu
    if _durduruldu:
        print("\nZorla çıkılıyor.", flush=True)
        raise SystemExit(130)
    _durduruldu = True
    print("\n\n>>> Durdurma istendi. Bu ihale bitince temiz çıkılacak "
          "(--devam ile kaldığın yerden sürer).\n", flush=True)


def bitmis_iknleri_oku(yol: Path) -> set[str]:
    """Daha önce yazılmış satırlardan İKN'leri toplar. Bozuk son satır (kesinti
    anında yarım yazılmış olabilir) sessizce atlanır."""
    if not yol.exists():
        return set()
    bitmis = set()
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            try:
                bitmis.add(json.loads(satir)["ikn"])
            except Exception:  # noqa: BLE001 — yarım satır normal, yoksay
                continue
    return bitmis


def sure_metni(saniye: float) -> str:
    saniye = int(saniye)
    s, d = divmod(saniye, 60)
    sa, dk = divmod(s, 60)
    return f"{sa}sa {dk}dk" if sa else f"{dk}dk {d}sn"


def ozet_bas(yol: Path) -> None:
    if not yol.exists():
        print(f"Sonuç dosyası yok: {yol}")
        return
    kararlar, on_filtreler, sureler = Counter(), Counter(), []
    satir_sayisi = 0
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            try:
                k = json.loads(satir)
            except Exception:  # noqa: BLE001
                continue
            satir_sayisi += 1
            kararlar[k.get("karar", "?")] += 1
            if k.get("on_filtre"):
                on_filtreler[k["on_filtre"]] += 1
            if k.get("sure_sn"):
                sureler.append(k["sure_sn"])

    print(f"\n{'='*66}\nTARAMA ÖZETİ — {satir_sayisi} ihale\n{'='*66}")
    for karar in ("uygun", "belirsiz", "uygun_degil", "HATA"):
        n = kararlar.get(karar, 0)
        if n:
            print(f"  {karar:12s} {n:5d}   %{100*n/satir_sayisi:.1f}")
    aday = kararlar.get("uygun", 0) + kararlar.get("belirsiz", 0)
    print(f"\n  ADAY LİSTE (uygun + belirsiz): {aday}   %{100*aday/max(satir_sayisi,1):.1f}")
    if on_filtreler:
        print(f"\n  LLM çağrılmadan elenenler:")
        for kural, n in on_filtreler.most_common():
            print(f"    {kural:16s} {n:5d}")
    if sureler:
        print(f"\n  ortalama süre: {sum(sureler)/len(sureler):.1f} sn/ihale")


def csv_yaz(jsonl: Path) -> Path:
    """Aday listesini (uygun + belirsiz) Excel'de açılabilir CSV'ye çıkarır."""
    csv_yolu = jsonl.with_name(jsonl.stem + "_aday.csv")
    alanlar = ["ikn", "karar", "ilgi_skoru", "adi", "idare_adi", "il",
               "ihale_turu", "ihale_tarihi", "eslesen_paket", "gerekce"]
    n = 0
    with open(jsonl, encoding="utf-8") as gir, \
         open(csv_yolu, "w", newline="", encoding="utf-8-sig") as cik:
        w = csv.DictWriter(cik, fieldnames=alanlar, delimiter=";", extrasaction="ignore")
        w.writeheader()
        satirlar = []
        for satir in gir:
            try:
                k = json.loads(satir)
            except Exception:  # noqa: BLE001
                continue
            if k.get("karar") in ("uygun", "belirsiz"):
                satirlar.append(k)
        # En güçlü adaylar üstte
        satirlar.sort(key=lambda k: (k.get("karar") != "uygun", -(k.get("ilgi_skoru") or 0)))
        for k in satirlar:
            w.writerow(k)
            n += 1
    print(f"\n  aday CSV: {csv_yolu}  ({n} satır)")
    return csv_yolu


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Sadece ilk N ihale (deneme için)")
    ap.add_argument("--devam", action="store_true", help="Bitmiş İKN'leri atla, kaldığın yerden sür")
    ap.add_argument("--ozet", action="store_true", help="Koşma, mevcut sonucu raporla")
    ap.add_argument("--cikti", type=Path, default=None, help="JSONL çıktı dosyası")
    ap.add_argument("--sert-esik", type=float, default=None,
                    help="Retrieval benzerliği bunun altındaysa LLM çağrılmaz (öneri: 0.44)")
    ap.add_argument("--not", dest="aciklama", default="")
    ap.add_argument("--dur-aday", type=int, metavar="N", default=None,
                    help="N ADAY bulununca dur. Aday = 'uygun' + 'belirsiz' (ikisi de "
                         "insana gider). Sadece 'uygun' saymak için --dur-uygun kullanın.")
    ap.add_argument("--dur-uygun", type=int, metavar="N", default=None,
                    help="N tane 'uygun' bulununca dur. 'belirsiz' sayılmaz.")
    ap.add_argument("--siralama", action="store_true",
                    help="SQL ön filtresini SIRALAMA için kullan: geçenler ÖNCE taranır. "
                         "ELEME YOK, kaçırma riski sıfır. Kesilen koşuda değerli sonuç "
                         "elde kalır. (bkz. app/decision/sql_filtre.py)")
    ap.add_argument("--eleme", action="store_true",
                    help="SQL filtresini geçmeyeni HİÇ TARAMA. %%88 tasarruf ama bir "
                         "KAÇIRMA KAPISIDIR — sadece geri çağırma 41/41 ölçüldüyse açın "
                         "(evaluation/sql_filtre_analizi.py).")
    ap.add_argument("--filtre-genislik", choices=("dar", "genis", "odakli"), default="genis")
    ap.add_argument("--filtre-kaynak", choices=("profil", "tercih", "birlesik"),
                    default="profil",
                    help="profil = iş paketi dosyaları (41/41 ölçüldü). tercih = portaldaki "
                         "Şirket Tercihleri. birlesik = ikisi. "
                         "Gerekçe: app/decision/sirket_tercihleri.py")
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    kok = Path(__file__).resolve().parents[1]
    cikti = a.cikti or (kok / VARSAYILAN_CIKTI / "tarama.jsonl")
    cikti.parent.mkdir(parents=True, exist_ok=True)

    if a.ozet:
        ozet_bas(cikti)
        return 0

    if a.sert_esik is not None:
        ayarlar.sert_on_filtre_skoru = a.sert_esik

    # Aşama 2 kapalı: kapasite verisi olmadan anlamlı çalışmıyor (bkz. modül başlığı).
    servis = servis_olustur(ayarlar, sadece_asama1=True)

    # --- SQL ÖN FİLTRESİ ---
    # İki mod, tek fark: eleme yapılıyor mu. Sıralama modunda havuz aynı kalır,
    # yalnızca sıra değişir — kaçırma riski sıfır. Eleme modu ölçülmüş bir kapıdır
    # ve geri çağırma 41/41 doğrulanmadan açılmamalıdır.
    filtre_notu = ""
    if a.siralama or a.eleme:
        from app.decision.sirket_tercihleri import filtre_kur

        tanim, tercih = filtre_kur(a.filtre_kaynak, genislik=a.filtre_genislik)
        if a.filtre_kaynak in ("tercih", "birlesik"):
            print(f"  ŞİRKET TERCİHLERİ (portal): "
                  f"kelime={', '.join(tercih.anahtar_kelimeler) or '(boş)'} | "
                  f"okas={', '.join(tercih.okas_kodlari) or '(boş)'}")
        cift = servis.depo.aktif_iknler_oncelikli(tanim, limit=a.limit)
        gecen = sum(1 for _, e in cift if e)
        if a.eleme:
            iknler = [i for i, e in cift if e]
            filtre_notu = (f"ELEME: {len(cift)} -> {len(iknler)} "
                           f"(kaynak={tanim.kaynak}, {len(tanim.terimler)} terim)")
            print(f"  !! ELEME MODU — {len(cift)-len(iknler)} ihale HİÇ TARANMAYACAK.")
            print("     Geri çağırmayı doğruladınız mı? evaluation/sql_filtre_analizi.py")
        else:
            iknler = [i for i, _ in cift]
            filtre_notu = (f"SIRALAMA: {gecen} öncelikli / {len(cift)} toplam "
                           f"(kaynak={tanim.kaynak})")
        print(f"  SQL filtresi: {filtre_notu}")
    else:
        iknler = servis.depo.aktif_ihale_iknleri(limit=a.limit)
    bitmis = bitmis_iknleri_oku(cikti) if a.devam else set()
    if not a.devam and cikti.exists() and cikti.stat().st_size > 0:
        print(f"UYARI: {cikti} zaten dolu ve --devam verilmedi.")
        print("       Üstüne yazmak yerine --devam kullanın, ya da dosyayı taşıyın.")
        return 1

    kalanlar = [i for i in iknler if i not in bitmis]

    print(f"\n{'='*66}")
    print("TOPLU TARAMA")
    print(f"  toplam aktif ihale : {len(iknler)}")
    print(f"  daha önce bitmiş   : {len(bitmis)}")
    print(f"  koşulacak          : {len(kalanlar)}")
    print(f"  model              : {ayarlar.birincil_model}  (think={ayarlar.llm_dusunme})")
    print(f"  sert eşik          : {ayarlar.sert_on_filtre_skoru or 'kapalı'}")
    print(f"  min_skor / paket / destekleyici: {ayarlar.retrieval_min_skor} / "
          f"{ayarlar.paket_dogrula} / {ayarlar.destekleyici_paket_kurali}")
    print(f"  çıktı              : {cikti}")
    print(f"  ortam              : {ozet_satiri()}")
    print(f"{'='*66}\n", flush=True)

    if not kalanlar:
        print("Koşulacak ihale yok.")
        ozet_bas(cikti)
        return 0

    signal.signal(signal.SIGINT, _dur)

    # Koşu üst bilgisi ayrı bir dosyaya — JSONL'in kendisi saf veri kalsın.
    (cikti.with_suffix(".meta.json")).write_text(
        json.dumps({
            "baslangic": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "not": a.aciklama,
            "model": ayarlar.birincil_model,
            "think": ayarlar.llm_dusunme,
            "sert_esik": ayarlar.sert_on_filtre_skoru,
            "sql_filtre": filtre_notu or "kapalı",
            "retrieval_min_skor": ayarlar.retrieval_min_skor,
            "paket_dogrula": ayarlar.paket_dogrula,
            "destekleyici_paket_kurali": ayarlar.destekleyici_paket_kurali,
            "okas_vetosu": ayarlar.okas_vetosu,
            "nitel_yakinlik": ayarlar.nitel_yakinlik,
            "negatif_dogrula": ayarlar.negatif_dogrula,
            "faaliyet_ortusmesi": ayarlar.faaliyet_ortusmesi,
            "b1_alan_terimi_istisnasi": ayarlar.b1_alan_terimi_istisnasi,
            "toplam": len(iknler),
            "ortam": ortam_ozeti(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    t0 = time.time()
    sayac = Counter()
    durma_notu = ""
    with open(cikti, "a", encoding="utf-8") as f:
        for i, ikn in enumerate(kalanlar, 1):
            if _durduruldu:
                break
            t = time.time()
            try:
                ihale = servis.depo.ikn_ile_getir(ikn)
                s = servis.ihaleyi_analiz_et(ihale)
                k = s.kapsam
                kayit = {
                    "ikn": ikn,
                    "tender_id": s.tender_id,
                    "adi": s.adi,
                    "idare_adi": s.idare_adi,
                    "il": getattr(ihale, "il", "") or "",
                    "ihale_turu": getattr(ihale, "ihale_turu", "") or "",
                    "ihale_tarihi": getattr(ihale, "ihale_tarihi", "") or "",
                    "karar": k.karar if k else "HATA",
                    "ilgi_skoru": k.ilgi_skoru if k else None,
                    "eslesen_paket": k.eslesen_paket if k else None,
                    "gerekce": k.gerekce if k else "",
                    "on_filtre": s.on_filtre_kurali,
                    "en_ust_benzerlik": (s.kullanilan_paketler[0]["benzerlik"]
                                         if s.kullanilan_paketler else None),
                    "notlar": s.notlar,
                    "sure_sn": round(time.time() - t, 1),
                }
            except Exception as e:  # noqa: BLE001 — tek ihale tüm taramayı düşürmesin
                kayit = {"ikn": ikn, "karar": "HATA",
                         "gerekce": f"{type(e).__name__}: {e}",
                         "sure_sn": round(time.time() - t, 1)}

            # ANINDA DİSKE — kesinti bu satırı kaybetmesin.
            f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

            sayac[kayit["karar"]] += 1
            gecen = time.time() - t0
            kalan_sn = (gecen / i) * (len(kalanlar) - i)
            print(
                f"[{i}/{len(kalanlar)}] {kayit['karar']:12s} "
                f"{str(kayit.get('ilgi_skoru') or ''):6s} "
                f"{(kayit.get('adi') or '')[:44]:<44} "
                f"{kayit['sure_sn']:5.1f}sn  kalan~{sure_metni(kalan_sn)}",
                flush=True,
            )

            # --- ERKEN DURMA ---
            # "Sistem sahada gezsin, ilk N adayı getirsin" testi için. Taramanın
            # KENDİSİNİ değiştirmez: aynı sırada aynı ihaleler işlenir, sadece
            # sayaç dolunca durulur. `--devam` ile kaldığı yerden sürer.
            #
            # DİKKAT — bu bir ÖLÇÜM koşusu DEĞİLDİR. Erken durdurulmuş bir tarama
            # "aday oranı" vermez: durma koşulu aday bulmaya bağlı olduğu için
            # örneklem taraflıdır (son ihale her zaman adaydır). Oran ölçmek için
            # sabit `--limit` kullanın.
            if a.dur_uygun and sayac["uygun"] >= a.dur_uygun:
                durma_notu = f"{a.dur_uygun} 'uygun' bulundu"
                break
            if a.dur_aday and (sayac["uygun"] + sayac["belirsiz"]) >= a.dur_aday:
                durma_notu = f"{a.dur_aday} aday (uygun+belirsiz) bulundu"
                break

    print(f"\n{'='*66}")
    if durma_notu:
        print(f"ERKEN DURDU — {durma_notu} ({sum(sayac.values())} ihale tarandı)")
        print("  NOT: erken durdurulmuş koşudan ADAY ORANI çıkarılmaz — örneklem taraflı.")
        print("  --devam ile kaldığı yerden sürdürebilirsin.")
    else:
        print("DURDURULDU (--devam ile sürdürülebilir)" if _durduruldu else "TAMAMLANDI")
    print(f"bu oturumda: {dict(sayac)}   süre: {sure_metni(time.time()-t0)}")
    ozet_bas(cikti)
    csv_yaz(cikti)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
