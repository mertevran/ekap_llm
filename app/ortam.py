"""
Çalışma ortamı bilgisi — her ölçüm koşusuna kaydedilir.

NEDEN GEREKLİ: proje iki ayrı makinede koşuyor — iş yerindeki laptop (RTX 3060, 6GB
VRAM) ve ev PC'si (RTX 5070 Ti, 16GB VRAM). qwen3:8b 6GB'a TAM SIĞMIYOR, kısmen CPU'ya
taşıyor. Katman bölünmesi değiştiğinde kayan nokta toplama sırası da değişiyor ve
çıktı, sabit seed'e rağmen kayabiliyor.

Sonuç: FARKLI MAKİNELERDE ALINAN ÖLÇÜMLER DOĞRUDAN KIYASLANAMAZ. Bu modül, her
koşunun hangi makinede alındığını rapora yazar ki `compare.py` tablosunda elma-armut
karşılaştırması yapılmasın.
"""

from __future__ import annotations

import platform
import shutil
import subprocess


def gpu_bilgisi() -> str:
    """nvidia-smi varsa GPU adı + VRAM döndürür, yoksa 'bilinmiyor'."""
    if not shutil.which("nvidia-smi"):
        return "bilinmiyor (nvidia-smi yok)"
    try:
        cikti = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return " | ".join(satir.strip() for satir in cikti.splitlines()) or "bilinmiyor"
    except Exception:  # noqa: BLE001 — ortam bilgisi ölçümü durdurmasın
        return "bilinmiyor"


def ortam_ozeti() -> dict:
    return {
        "makine": platform.node(),
        "isletim_sistemi": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "gpu": gpu_bilgisi(),
    }


def ozet_satiri() -> str:
    o = ortam_ozeti()
    return f"{o['makine']} | {o['gpu']} | {o['isletim_sistemi']} | py{o['python']}"


if __name__ == "__main__":
    for anahtar, deger in ortam_ozeti().items():
        print(f"  {anahtar:16s}: {deger}")
