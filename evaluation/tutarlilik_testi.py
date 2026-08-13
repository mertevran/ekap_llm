"""
T14 — TUTARLILIK TESTİ. Aynı girdi, N kez. Çıktı her seferinde aynı mı?

    python evaluation/tutarlilik_testi.py                 # 5 ihale x 5 tekrar
    python evaluation/tutarlilik_testi.py --tekrar 3 --ihale 8

NEDEN BU TEST VAR
-----------------
28.07.2026: v4 ve v5 koşuları arasında SADECE bir son-işleme kuralı değişmişti —
prompt, retrieval ve veri birebir aynıydı. Buna rağmen 15 ihalenin 10'unun çıktısı
değişti; skorlar savruldu (0.5049 -> 0.75) ve bir karar tamamen döndü. Sebep:
Ollama'ya sabit `seed` verilmemişti.

Bu, o ana kadarki tüm ölçümleri şüpheli hale getirdi: n=15'te bir ihale %6,7 demek
ve biz tek ihalelik farkları "iyileşme/gerileme" diye yorumluyorduk.

Seed eklendi (app/decision/llm_client.py). AMA SEED TEK BAŞINA YETMEYEBİLİR: 6GB
VRAM'de qwen3:8b kısmen CPU'ya taşıyor, katman bölünmesi koşular arasında değişirse
kayan nokta toplama sırası değişir. Bu yüzden düzeltmenin işe yaradığını VARSAYMAK
yerine ÖLÇÜYORUZ.

NASIL OKUNUR
------------
- Karar tutarlılığı %100 olmalı. Değilse ölçüm sonuçlarına o oranda güvenilemez;
  koşular arası farkları yorumlamadan önce bu düzeltilmeli.
- Skor tutarlılığı da %100 beklenir. Karar aynı ama skor oynuyorsa, kararlar eşik
  sınırındaki ihalelerde (0.5 civarı) er ya da geç dönecek demektir.
- Tutarsızlık kalırsa seçenekler: modeli tamamen GPU'ya sığdırmak (daha küçük model
  ya da daha fazla VRAM), ya da her ölçümü N kez koşup çoğunluk kararını almak.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.pipeline.servis import servis_olustur  # noqa: E402

VARSAYILAN_SET = Path(__file__).parent / "karar-seti-v1.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tekrar", type=int, default=5, help="Her ihale kaç kez koşulacak")
    ap.add_argument("--ihale", type=int, default=5, help="Kaç farklı ihale denenecek")
    ap.add_argument("--data", type=Path, default=VARSAYILAN_SET)
    ap.add_argument("--subset", default="ayar")
    ap.add_argument(
        "--karar",
        default=None,
        help=(
            "Sadece bu etikete sahip ihaleleri test et (uygun / belirsiz / uygun_degil). "
            "ÖNEMLİ: 'belirsiz' sınıfını ayrıca test edin — o örneklerin skorları 0.45-0.49 "
            "bandında, yani 0.5 eşiğinin dibinde. Skor gürültüsü kararı ancak orada "
            "değiştirir. 'uygun' sınıfı (skorlar 0.6-0.85) eşikten uzak olduğu için "
            "gürültüye rağmen sabit görünür ve yanıltıcı bir güven verir."
        ),
    )
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    with open(a.data, encoding="utf-8-sig") as f:
        satirlar = [
            s for s in csv.DictReader(f, delimiter=";")
            if a.subset in ("hepsi", (s.get("set") or "").strip())
            and (a.karar is None or (s.get("karar") or "").strip() == a.karar)
        ][: a.ihale]

    if not satirlar:
        print("Test edilecek satır yok.")
        return 1

    servis = servis_olustur(ayarlar, sadece_asama1=True)
    print(f"\nT14 TUTARLILIK — {len(satirlar)} ihale x {a.tekrar} tekrar")
    print(f"model={ayarlar.birincil_model}  seed={ayarlar.llm_seed}  num_ctx={ayarlar.num_ctx}\n")

    kararlar_tutarli = skorlar_tutarli = 0
    for i, satir in enumerate(satirlar, 1):
        ihale = servis.depo.id_ile_getir(satir["tender_id"])
        kararlar, skorlar = [], []
        print(f"[{i}/{len(satirlar)}] {(satir.get('adi') or '')[:52]:<52}", end=" ", flush=True)
        for _ in range(a.tekrar):
            s = servis.ihaleyi_analiz_et(ihale)
            kararlar.append(s.kapsam.karar if s.kapsam else "HATA")
            skorlar.append(s.kapsam.ilgi_skoru if s.kapsam else -1.0)
            print(".", end="", flush=True)

        k_tekil, s_tekil = set(kararlar), set(skorlar)
        k_ok, s_ok = len(k_tekil) == 1, len(s_tekil) == 1
        kararlar_tutarli += k_ok
        skorlar_tutarli += s_ok

        if k_ok and s_ok:
            print(f"  SABİT  {kararlar[0]} ({skorlar[0]})")
        elif k_ok:
            print(f"  karar sabit ({kararlar[0]}) ama SKOR OYNUYOR: "
                  f"{sorted(s_tekil)}  yayılım={max(skorlar)-min(skorlar):.3f}")
        else:
            print(f"  !! KARAR OYNUYOR: {dict(Counter(kararlar))}  skorlar={sorted(s_tekil)}")

    n = len(satirlar)
    print(f"\n--- SONUÇ ({a.karar or 'tüm sınıflar'}) ---")
    print(f"Karar tutarlılığı : {kararlar_tutarli}/{n}  (%{100*kararlar_tutarli/n:.0f})")
    print(f"Skor tutarlılığı  : {skorlar_tutarli}/{n}  (%{100*skorlar_tutarli/n:.0f})")

    if kararlar_tutarli == n and skorlar_tutarli == n:
        print("\nTAM DETERMİNİSTİK. Koşular arası farklar gerçek değişikliktir.")
        return 0

    if kararlar_tutarli == n:
        print(
            "\nKARARLAR SABİT, skorlar oynuyor. Bu, KARARLAR eşikten uzak olduğu sürece\n"
            "sorun değil — ama 0.5 eşiğinin dibindeki (0.45-0.52) ihalelerde skor\n"
            "gürültüsü kararı DÖNDÜREBİLİR (skor < 0.5 ise kod kuralı 'uygun'u\n"
            "'belirsiz'e düşürüyor).\n"
        )
        if a.karar != "belirsiz":
            print(
                "  !! BU TEST YETERLİ DEĞİL. Sadece bu sınıfa baktınız; asıl riskli olan\n"
                "     'belirsiz' sınıfı (skorları 0.45-0.49 bandında). Şunu da koşun:\n"
                "        python evaluation/tutarlilik_testi.py --karar belirsiz --ihale 6\n"
            )
        else:
            print(
                "  Sınır bölgesindeki ihalelerde bile kararlar sabit çıktı. Ölçümlere\n"
                "  güvenilebilir; yine de tek ihalelik farkları yorumlarken temkinli olun.\n"
            )
        return 0

    print(
        "\nKARARLAR OYNUYOR — ölçüm güvenilir değil. Koşular arası farkları YORUMLAMAYIN.\n"
        "n=15'te bir ihale %6,7 demek ve o kadarı gürültüden geliyor olabilir. Seçenekler:\n"
        "  1) Modeli tamamen GPU'ya sığdırın (daha küçük model / daha fazla VRAM)\n"
        "  2) Her ölçümü N kez koşup çoğunluk kararını alın (yavaş ama sağlam)\n"
        "  3) Sadece BÜYÜK farkları (>= 3 ihale) anlamlı kabul edin"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
