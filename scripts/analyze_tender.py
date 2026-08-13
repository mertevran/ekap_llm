"""
Tek bir ihaleyi uçtan uca analiz eder (Aşama 1 + Aşama 2) ve raporlar.

    python scripts/analyze_tender.py 2025/2196999
    python scripts/analyze_tender.py 2025/2196999 --sadece-asama1
    python scripts/analyze_tender.py 2025/2196999 --json
    python scripts/analyze_tender.py --aktif 10 --sadece-asama1   # toplu tarama denemesi

Ollama'nın açık ve modelin yüklü olmasını gerektirir.
Önce `python scripts/check_setup.py` çalıştırın.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decision.schemas import AnalizSonucu  # noqa: E402
from app.pipeline.servis import servis_olustur  # noqa: E402

RENK = {"uygun": "UYGUN", "belirsiz": "BELİRSİZ", "uygun_degil": "UYGUN DEĞİL"}


def yaz(s: AnalizSonucu, ayrintili: bool) -> None:
    print("=" * 78)
    print(f"İKN   : {s.ikn}")
    print(f"Adı   : {s.adi}")
    print(f"İdare : {s.idare_adi}")
    print(f"Durum : {s.ihale_durumu}")

    if s.on_filtre_kurali:
        print(f"\n>>> ÖN FİLTRE: {s.on_filtre_kurali} (model çağrılmadı)")

    if s.kapsam:
        k = s.kapsam
        print(f"\n--- AŞAMA 1: KAPSAM ---")
        print(f"  karar        : {RENK.get(k.karar, k.karar)}   (ilgi skoru {k.ilgi_skoru})")
        print(f"  eşleşen paket: {k.eslesen_paket or '-'}")
        print(f"  eşleşen OKAS : {', '.join(k.eslesen_okas) or '-'}")
        print(f"  gerekçe      : {k.gerekce}")

    if ayrintili and s.kullanilan_paketler:
        print("\n  retrieval — en yakın paketler:")
        for p in s.kullanilan_paketler:
            print(f"    {p['benzerlik']:.4f} [{p['oncelik']:6s}] {p['kod']} {p['baslik']}")
        for etiket, ornekler in (
            ("UYGUN", s.benzer_uygun_ornekler),
            ("BELİRSİZ", s.benzer_belirsiz_ornekler),
            ("UYGUN_DEGIL", s.benzer_red_ornekler),
        ):
            if ornekler:
                print(f"  benzer örnekler — etiket {etiket}:")
                for o in ornekler:
                    print(f"    {o['benzerlik']:.4f} {o['baslik'][:66]}")

    if s.yeterlilik:
        y = s.yeterlilik
        print(f"\n--- AŞAMA 2: YETERLİLİK ---")
        print(f"  karar : {y.karar}   (güven {y.guven})")
        print(f"  özet  : {y.ozet}")
        if y.kriterler:
            print("  kriterler:")
            for kr in y.kriterler:
                kanit = ",".join(kr.kanit_idleri) or "-"
                print(f"    [{kr.durum:12s}] {kr.kriter_id:22s} kanıt={kanit}")
                if ayrintili and kr.gerekce:
                    print(f"        {kr.gerekce}")
        if y.eksik_kanitlar:
            print(f"  eksik kanıtlar: {'; '.join(y.eksik_kanitlar)}")
        if y.riskler:
            print(f"  riskler       : {'; '.join(y.riskler)}")

    if s.dogrulama and s.dogrulama.uygulanan_kurallar:
        print(f"\n  doğrulayıcı kuralları: {', '.join(s.dogrulama.uygulanan_kurallar)}")
        if s.dogrulama.zorunlu_karar:
            print(f"  ZORUNLU KARAR       : {s.dogrulama.zorunlu_karar}")

    if s.ikincil_gorus:
        print(f"\n  ikinci görüş ({s.ikincil_gorus.model_adi}): {s.ikincil_gorus.sonuc.karar} "
              f"(güven {s.ikincil_gorus.sonuc.guven})")

    if s.sebepler:
        print("\n  sebepler:")
        for r in s.sebepler:
            print(f"    - {r}")

    if s.insan_incelemesi_gerekli:
        print("\n  >>> İNSAN İNCELEMESİ GEREKLİ <<<")

    if s.notlar:
        print(f"\n  notlar: {'; '.join(s.notlar)}")
    print(f"\n  süreler(sn): {s.sureler_sn}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ikn", nargs="*")
    ap.add_argument("--aktif", type=int, metavar="N", help="İlk N aktif ihaleyi tara")
    ap.add_argument("--sadece-asama1", action="store_true", help="Aşama 2'yi atla (hızlı kıyas)")
    ap.add_argument("--json", action="store_true", help="Sadece JSON bas")
    ap.add_argument("--ayrintili", action="store_true", help="Retrieval ve kriter gerekçelerini de bas")
    ap.add_argument("--kaydet", type=Path, help="Sonuçları JSON dosyasına yaz")
    a = ap.parse_args()

    servis = servis_olustur(sadece_asama1=a.sadece_asama1)

    if a.aktif:
        ihaleler = servis.depo.aktif_ihaleler(limit=a.aktif)
    elif a.ikn:
        ihaleler = servis.depo.coklu_getir(a.ikn, alan="ikn")
        if not ihaleler:
            print(f"Bulunamadı: {a.ikn}")
            return 1
    else:
        ap.error("İKN verin ya da --aktif N kullanın")

    sonuclar = []
    t0 = time.time()
    for i, ihale in enumerate(ihaleler, 1):
        if not a.json:
            print(f"\n[{i}/{len(ihaleler)}] {ihale.adi[:64]} ...", flush=True)
        s = servis.ihaleyi_analiz_et(ihale)
        sonuclar.append(s)
        if a.json:
            print(json.dumps(s.model_dump(), ensure_ascii=False))
        else:
            yaz(s, a.ayrintili)

    if len(ihaleler) > 1 and not a.json:
        from collections import Counter

        say = Counter(s.kapsam.karar if s.kapsam else "-" for s in sonuclar)
        print("=" * 78)
        print(f"ÖZET — {len(ihaleler)} ihale, {time.time()-t0:.0f} sn")
        print(f"  kapsam dağılımı: {dict(say)}")
        print(f"  insan incelemesi gerekli: {sum(s.insan_incelemesi_gerekli for s in sonuclar)}")

    if a.kaydet:
        a.kaydet.parent.mkdir(parents=True, exist_ok=True)
        a.kaydet.write_text(
            json.dumps([s.model_dump() for s in sonuclar], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nKaydedildi: {a.kaydet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
