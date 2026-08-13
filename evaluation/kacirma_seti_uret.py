"""
Kaçırma test seti üretici — `onay=evet` işaretli, insan tarafından doğrulanmış
"uygun" ihalelerden bağımsız bir test seti oluşturur.

    python evaluation/kacirma_seti_uret.py

NOT: Bu script bir kereye mahsus çalıştırılmıştır ve ürettiği dosya
(`evaluation/kacirma-seti-v1.csv`) depoda hazır bulunur. Scripti yeniden
çalıştırmanız gerekmez; girdi CSV'si bu depoda YOKTUR. Yalnızca setin nasıl
üretildiğini belgelemek için saklanıyor. Kendi girdinizle çalıştırmak isterseniz:

    python evaluation/kacirma_seti_uret.py <girdi.csv>

=============================================================================
KAYNAK VE NEDEN SADECE "evet" KULLANILIYOR
=============================================================================
Girdi CSV'si, önceki bir modelin "uygun" dediği 67 ihaleyi ve bunların insan
gözden geçirmesini (`onay` sütunu: 33 evet / 34 hayır) içeriyordu.

`onay=hayır` BİLEREK KULLANILMIYOR. Çünkü o sütun temiz bir ground truth DEĞİL —
"bu örneği RAG havuzuna alalım mı" sorusunun cevabı. Doğrulandı: mevcut karar
setiyle çakışan 10 kaydın 10'u da `hayır`, ama bunlardan üçü karar setinde `uygun`
etiketli (DİNAMİK KAVŞAK KONTROL SİSTEMİ MODERNİZASYONU, EDS Bakım-Onarım,
BİLİŞİM SİSTEMLERİ MODERNİZASYONU). Yani `hayır` iki farklı şeyi karıştırıyor:
  (a) gerçekten uygun olmayan işler,
  (b) uygun AMA test setinde olduğu için sızıntı riskiyle dışlanan işler.
Bu ikisi ayrılmadan `hayır` bir etiket olarak kullanılamaz.

`onay=evet` ise tek anlamlıdır: bir insan bakıp "evet, bu iş bize uygun" demiş.

=============================================================================
BU SET NE ÖLÇER, NE ÖLÇMEZ
=============================================================================
ÖLÇER: KAÇIRMA. Hepsi `uygun` etiketli olduğu için tek soru şu — sistem bu
doğrulanmış uygun ihalelerin kaçına `uygun_degil` diyor? Bu, projenin en pahalı
hatası ve mevcut ayar setinde sadece 5 `uygun` örnekle ölçülüyordu. Bu set onu
~30'a çıkarıyor.

ÖLÇMEZ:
- Yanlış alarm / precision (sette hiç `uygun_degil` yok).
- ZOR pozitifleri bulma yeteneği. Bu ihaleler, ÖNCEKİ bir modelin (farklı bir
  boru hattında) zaten "uygun" dediği havuzdan geliyor. Yani "kolay
  pozitifler" — bir modelin hiç bulamadığı uygun ihaleler bu sette YOK. Sonucu
  "sistem uygunları buluyor" diye okumayın; "sistem daha önce bulunmuş uygunları
  KAYBETMİYOR" diye okuyun. Bu bir regresyon testidir, bir keşif testi değil.
- Mevcut karar setiyle çakışma yok (doğrulandı) — bu yüzden bağımsız bir ölçüm.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.base import depo_olustur  # noqa: E402

# Girdi CSV yolu. Komut satırından ilk argüman olarak da verilebilir.
# Bu dosya depoda YOKTUR — bkz. dosya başlığındaki not.
KAYNAK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("aday_etiketleme.csv")
MEVCUT_SET = Path(__file__).parent / "karar-seti-v1.csv"
CIKTI = Path(__file__).parent / "kacirma-seti-v1.csv"


def main() -> int:
    if not KAYNAK.exists():
        print(f"Kaynak bulunamadı: {KAYNAK}")
        return 1

    with open(KAYNAK, encoding="utf-8-sig") as f:
        satirlar = list(csv.DictReader(f, delimiter=";"))
    with open(MEVCUT_SET, encoding="utf-8-sig") as f:
        mevcut_idler = {r["tender_id"] for r in csv.DictReader(f, delimiter=";")}

    evet = [r for r in satirlar if (r.get("onay") or "").strip() == "evet"]
    print(f"kaynak: {len(satirlar)} kayıt | onay=evet: {len(evet)}")

    # Sızıntı güvenliği: mevcut ayar/final setindeki hiçbir ihale bu sete GİREMEZ.
    temiz = [r for r in evet if r["tender_id"] not in mevcut_idler]
    print(f"mevcut karar setiyle çakışan, atılan: {len(evet) - len(temiz)}")

    # Aynı ihale birden fazla kez gözden geçirilmiş olabilir — tender_id ile tekilleştir.
    tekil: dict[str, dict] = {}
    for r in temiz:
        tekil.setdefault(r["tender_id"], r)
    print(f"tekilleştirme sonrası: {len(tekil)}")

    depo = depo_olustur()
    ihaleler = {i.id: i for i in depo.coklu_getir(list(tekil), alan="id")}
    print(f"veritabanında bulunan: {len(ihaleler)}")

    with open(CIKTI, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["tender_id", "ikn", "adi", "idare_adi", "il", "ihale_turu",
                        "karar", "kaynak", "set"],
            delimiter=";",
        )
        w.writeheader()
        n = 0
        for tid in tekil:
            i = ihaleler.get(tid)
            if i is None:
                continue
            w.writerow({
                "tender_id": i.id,
                "ikn": i.ikn,
                "adi": i.adi or "",
                "idare_adi": i.idare_adi or "",
                "il": i.il or "",
                "ihale_turu": i.ihale_turu or "",
                "karar": "uygun",  # onay=evet -> insan doğrulaması
                "kaynak": "qwen3_8b_SADECE_UYGUN_gozden_gecir.csv onay=evet",
                "set": "kacirma",
            })
            n += 1

    print(f"\nYAZILDI: {CIKTI}  ({n} ihale, hepsi 'uygun')")
    print("\nÇalıştırmak için:")
    print(f"  python evaluation/evaluate.py --data evaluation/{CIKTI.name} --subset kacirma")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
