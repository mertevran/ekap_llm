"""
Aynı test setini birden çok modelle sırayla koşar ve kıyas tablosu üretir.

    python evaluation/model_kiyasla.py --modeller qwen3:8b qwen3:4b gemma3:4b
    python evaluation/model_kiyasla.py --modeller qwen3:8b qwen3:4b --subset ayar
    python evaluation/model_kiyasla.py --modeller qwen3:8b qwen3:4b \
        --data evaluation/kacirma-seti-v1.csv --subset kacirma

ADİL KIYAS KURALI (plan, Bölüm 4): model DIŞINDAKİ HER ŞEY aynı kalır — aynı
embedding, aynı retrieval, aynı prompt, aynı test seti, aynı seed, aynı num_ctx.
Bu script bunu garanti eder: tek tek `evaluate.py --model X` koşmak yerine hepsini
tek oturumda, aynı yapılandırmayla çalıştırır.

MODEL SEÇERKEN:
- Yapılandırılmış çıktı (JSON şeması) desteği şart. Ollama'nın `format` parametresi
  çoğu modelde çalışır ama küçük modeller şemaya uymakta zorlanabilir; script bunu
  "şema hatası" olarak raporlar, sessizce geçmez.
- VRAM'e dikkat: modeller sırayla yüklenir, Ollama önceki modeli boşaltır. 6GB'lık
  bir kartta her model değişiminde saniyeler kaybedilir ama çalışır.
- Daha önce ölçülen sıralama: qwen3:8b > qwen3:4b > aya-expanse:8b. gemma3:4b zayıf
  kaldı. Varsayılan model qwen3:4b'dir: hız/isabet dengesi pratikte en iyi orada.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KOK = Path(__file__).resolve().parents[1]


def model_yuklu_mu(model: str, host: str) -> bool:
    import httpx

    try:
        with httpx.Client(timeout=10.0) as c:
            y = c.get(f"{host.rstrip('/')}/api/tags")
            y.raise_for_status()
            return model in [m["name"] for m in y.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modeller", nargs="+", required=True, help="Ollama model adları")
    ap.add_argument("--subset", default="ayar")
    ap.add_argument("--data", default=None)
    ap.add_argument("--not", dest="aciklama", default="", help="Koşulara eklenecek ortak not")
    ap.add_argument("--atla-kontrol", action="store_true", help="Model yüklü mü kontrolünü atla")
    a = ap.parse_args()

    from app.config.settings import ayarlari_al
    from app.ortam import ozet_satiri

    ayarlar = ayarlari_al()

    print(f"\n{'='*78}")
    print("ÇOKLU MODEL KIYASI")
    print(f"  modeller : {', '.join(a.modeller)}")
    print(f"  subset   : {a.subset}")
    print(f"  seed     : {ayarlar.llm_seed}   num_ctx: {ayarlar.num_ctx}")
    print(f"  embedding: {ayarlar.embedding_backend}/{ayarlar.embedding_model} (DEĞİŞMİYOR)")
    print(f"  ortam    : {ozet_satiri()}")
    print(f"{'='*78}\n")

    if not a.atla_kontrol:
        eksik = [m for m in a.modeller if not model_yuklu_mu(m, ayarlar.ollama_host)]
        if eksik:
            print("Şu modeller Ollama'da yüklü değil:")
            for m in eksik:
                print(f"    ollama pull {m}")
            print("\nİndirip tekrar çalıştırın (ya da --atla-kontrol ile zorlayın).")
            return 1

    basarili, basarisiz = [], []
    for i, model in enumerate(a.modeller, 1):
        print(f"\n{'#'*78}\n### [{i}/{len(a.modeller)}] {model}\n{'#'*78}\n", flush=True)
        komut = [
            sys.executable, str(KOK / "evaluation" / "evaluate.py"),
            "--model", model,
            "--subset", a.subset,
            "--not", (a.aciklama or f"model kiyasi: {', '.join(a.modeller)}"),
        ]
        if a.data:
            komut += ["--data", a.data]

        sonuc = subprocess.run(komut, cwd=str(KOK))
        (basarili if sonuc.returncode == 0 else basarisiz).append(model)

    print(f"\n{'='*78}")
    print(f"TAMAMLANDI — başarılı: {', '.join(basarili) or '(yok)'}")
    if basarisiz:
        print(f"            BAŞARISIZ: {', '.join(basarisiz)}")
    print(f"{'='*78}\n")

    # Kıyas tablosunu doğrudan bas
    subprocess.run(
        [sys.executable, str(KOK / "evaluation" / "compare.py"), "--subset", a.subset],
        cwd=str(KOK),
    )
    return 0 if not basarisiz else 1


if __name__ == "__main__":
    raise SystemExit(main())
