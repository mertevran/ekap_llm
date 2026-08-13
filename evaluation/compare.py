"""
Kayıtlı koşuları yan yana karşılaştırır — eski JSON'lara da yeni metrikleri uygular.

    python evaluation/compare.py                       # tüm koşular
    python evaluation/compare.py --subset ayar
    python evaluation/compare.py --detay               # ihale bazında değişim tablosu

Metrikler kayıtlı `detaylar` alanından yeniden hesaplanır; bu yüzden yumuşak kaçırma
gibi SONRADAN eklenen ölçütler geçmiş koşular için de görünür (v1/v2 gibi).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SONUCLAR = Path(__file__).resolve().parents[1] / "Sonuclar"


def metrikleri_hesapla(detaylar: list[dict]) -> dict:
    n = len(detaylar)
    if not n:
        return {}
    g = lambda d: (d.get("gercek_karar") or "").strip()  # noqa: E731
    t = lambda d: (d.get("tahmin_karar") or "").strip()  # noqa: E731

    dogru = sum(1 for d in detaylar if g(d) == t(d))
    sert = sum(1 for d in detaylar if g(d) == "uygun" and t(d) == "uygun_degil")
    yumusak = sum(1 for d in detaylar if g(d) == "belirsiz" and t(d) == "uygun_degil")
    alarm = sum(1 for d in detaylar if g(d) == "uygun_degil" and t(d) == "uygun")
    asiri_kapsayici = sum(1 for d in detaylar if g(d) == "belirsiz" and t(d) == "uygun")

    return {
        "n": n,
        "dogruluk": round(dogru / n, 3),
        "eleme_hatasi": round((sert + yumusak) / n, 3),
        "sert_kacirma": sert,
        "yumusak_kacirma": yumusak,
        "asiri_kapsayici": asiri_kapsayici,
        "yanlis_alarm": alarm,
    }


def kosulari_yukle(subset: str | None) -> list[tuple[str, dict]]:
    kosular = []
    for yol in sorted(SONUCLAR.rglob("v*.json")):
        try:
            d = json.loads(yol.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if "detaylar" not in d:
            continue
        if subset and d.get("subset") != subset:
            continue
        etiket = f"{d.get('model','?')}/{d.get('subset','?')}/{yol.stem}"
        kosular.append((etiket, d))
    return kosular


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", default=None, choices=["ayar", "final", "hepsi"])
    ap.add_argument("--detay", action="store_true", help="İhale bazında değişim tablosu")
    a = ap.parse_args()

    kosular = kosulari_yukle(a.subset)
    if not kosular:
        print("Karşılaştırılacak koşu bulunamadı.")
        return 1

    bas = (f"{'koşu':<26}{'n':>3} {'doğr.':>6} {'ELEME':>6} {'sert':>5} {'yumş':>5} "
           f"{'aşırı+':>7} {'y.alarm':>8} {'makine':<14} not")
    print("\n" + bas)
    print("-" * len(bas))
    makineler = set()
    for etiket, d in kosular:
        m = metrikleri_hesapla(d["detaylar"])
        makine = (d.get("ortam") or {}).get("makine", "?")
        makineler.add(makine)
        print(
            f"{etiket:<26}{m['n']:>3} {m['dogruluk']:>6.3f} {m['eleme_hatasi']:>6.3f} "
            f"{m['sert_kacirma']:>5} {m['yumusak_kacirma']:>5} {m['asiri_kapsayici']:>7} "
            f"{m['yanlis_alarm']:>8} {makine[:13]:<14} {(d.get('not') or '')[:30]}"
        )

    if len(makineler - {"?"}) > 1:
        print(
            f"\n  !! UYARI: koşular FARKLI MAKİNELERDE alınmış ({', '.join(sorted(makineler))}).\n"
            "     qwen3:8b 6GB VRAM'e tam sığmıyor, kısmen CPU'ya taşıyor; katman bölünmesi\n"
            "     değişince çıktı sabit seed'e rağmen kayabiliyor. Farklı makinelerdeki\n"
            "     koşuları doğrudan kıyaslamayın."
        )
    print(
        "\n  ELEME = insana hiç ulaşmayan ihale oranı (sert + yumuşak). ASIL BAKILACAK SAYI.\n"
        "  sert  = gerçek uygun    -> uygun_degil\n"
        "  yumş  = gerçek belirsiz -> uygun_degil   (sistemin şüpheliyi sessizce elemesi)\n"
        "  aşırı+= gerçek belirsiz -> uygun         (aşırı kapsayıcı, GÜVENLİ yön)\n"
    )

    if a.detay and len(kosular) >= 2:
        (ad_a, da), (ad_b, db) = kosular[-2], kosular[-1]
        ha = {x["tender_id"]: x for x in da["detaylar"]}
        hb = {x["tender_id"]: x for x in db["detaylar"]}
        print(f"DEĞİŞİM: {ad_a}  ->  {ad_b}\n" + "=" * 96)
        for tid, y in hb.items():
            x = ha.get(tid)
            if x is None or x["tahmin_karar"] == y["tahmin_karar"]:
                continue
            if y["eslesiyor"] and not x["eslesiyor"]:
                yon = "DÜZELDİ "
            elif x["eslesiyor"] and not y["eslesiyor"]:
                yon = "BOZULDU "
            else:
                yon = "yer değ."
            print(
                f"  [{yon}] {y['adi'][:58]:<58} etiket={y['gercek_karar']:11s} "
                f"{x['tahmin_karar']}({x['ilgi_skoru']}) -> {y['tahmin_karar']}({y['ilgi_skoru']})"
            )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
