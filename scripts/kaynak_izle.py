"""
Kaynak izleyici — koşu sırasında CPU, RAM ve GPU kullanımını örnekler.

    python scripts/kaynak_izle.py --etiket "cpu-testi"
    python scripts/kaynak_izle.py --etiket "gpu-testi" --aralik 5
    python scripts/kaynak_izle.py --etiket "cpu" --sure 3600     # 1 saat sonra kendiliğinden dur

AYRI BİR TERMİNALDE ÇALIŞTIRIN, sonra asıl koşuyu başlatın. Durdurmak için Ctrl+C;
özet ekrana basılır ve CSV `Sonuclar/kaynak_izleme/` altına yazılır.

=============================================================================
NEDEN
=============================================================================
Sistem GPU üzerinde ihale başına ~64-76 saniyede çalışıyor. Aynı işin CPU'da
yapılabilir olup olmadığı, üretim ortamında GPU'lu sunucu gerekip gerekmediğini
belirliyor — yani doğrudan donanım tedarikini ilgilendiren bir soru.

Ama "CPU'da çalıştı/çalışmadı" tek başına yetersiz bir cevap. Şunlar da lazım:
  · Kaç çekirdek doyuyor (paralellik kararı için)
  · RAM tepe değeri ne (sunucu boyutlandırması için)
  · GPU koşusunda VRAM ne kadar kullanılıyor (kaç eşzamanlı model sığar)

Bu script aynı ölçümü iki koşuda da alıyor; GPU ve CPU sayıları yan yana
konabiliyor.

=============================================================================
NE ÖLÇÜLÜYOR
=============================================================================
Sistem geneli   : CPU %, RAM (MB ve %)
Ollama süreçleri: toplam CPU %, toplam RSS (MB)  — adında "ollama" geçen tüm süreçler
GPU (varsa)     : kullanım %, VRAM (MB)          — nvidia-smi ile

CPU yüzdesi çekirdek sayısına GÖRE NORMALLEŞTİRİLMEMİŞTİR: 8 çekirdekli bir
makinede tam doyma %800 olarak görünür. Çekirdek sayısı başlıkta yazıyor.
"""

from __future__ import annotations

import argparse
import csv
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CIKTI_DIZINI = Path(__file__).resolve().parents[1] / "Sonuclar" / "kaynak_izleme"

ALANLAR = (
    "zaman", "gecen_sn",
    "cpu_yuzde", "ram_mb", "ram_yuzde",
    "ollama_cpu_yuzde", "ollama_ram_mb", "ollama_surec_sayisi",
    "gpu_yuzde", "gpu_vram_mb",
)

_durduruldu = False


def _dur(signum, frame):  # noqa: ARG001
    global _durduruldu
    _durduruldu = True
    print("\n\n  Durduruluyor, özet hazırlanıyor…", flush=True)


def gpu_oku() -> tuple[float | None, float | None]:
    """nvidia-smi ile GPU kullanımı ve VRAM. GPU yoksa (None, None)."""
    try:
        c = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if c.returncode != 0 or not c.stdout.strip():
            return None, None
        # Birden fazla GPU varsa ilkini al
        kullanim, vram = c.stdout.strip().splitlines()[0].split(",")
        return float(kullanim.strip()), float(vram.strip())
    except Exception:  # noqa: BLE001 — GPU yoksa/nvidia-smi yoksa sessiz geç
        return None, None


def ollama_surecleri(psutil):
    """Adında 'ollama' geçen tüm süreçler. Ollama sunucu + model süreci ayrı olabilir."""
    bulunan = []
    for p in psutil.process_iter(["name"]):
        try:
            ad = (p.info.get("name") or "").lower()
            if "ollama" in ad:
                bulunan.append(p)
        except Exception:  # noqa: BLE001
            continue
    return bulunan


def _ozet(baslik: str, degerler: list[float], birim: str = "") -> str:
    if not degerler:
        return f"  {baslik:26s} (veri yok)"
    return (f"  {baslik:26s} en düşük {min(degerler):8.1f}{birim}   "
            f"ortalama {sum(degerler)/len(degerler):8.1f}{birim}   "
            f"TEPE {max(degerler):8.1f}{birim}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aralik", type=float, default=5.0, help="Örnekleme aralığı (sn, varsayılan 5)")
    ap.add_argument("--etiket", default="", help="Koşuyu tanımlayan not (ör. 'cpu-testi')")
    ap.add_argument("--sure", type=float, default=None, help="Bu kadar saniye sonra kendiliğinden dur")
    ap.add_argument("--cikti", type=Path, default=None)
    a = ap.parse_args()

    try:
        import psutil
    except ImportError:
        print('HATA: psutil kurulu değil.\n  pip install psutil')
        return 1

    signal.signal(signal.SIGINT, _dur)

    CIKTI_DIZINI.mkdir(parents=True, exist_ok=True)
    damga = datetime.now().strftime("%Y%m%d_%H%M%S")
    ad = f"{damga}_{a.etiket}" if a.etiket else damga
    yol = a.cikti or (CIKTI_DIZINI / f"{ad.replace(' ', '_')}.csv")

    cekirdek = psutil.cpu_count(logical=True)
    toplam_ram = psutil.virtual_memory().total / 1024**2
    gpu_var = gpu_oku()[0] is not None

    print(f"\n{'=' * 74}")
    print("KAYNAK İZLEME")
    print(f"{'=' * 74}")
    print(f"  etiket        : {a.etiket or '(yok)'}")
    print(f"  çekirdek      : {cekirdek} mantıksal")
    print(f"  toplam RAM    : {toplam_ram:,.0f} MB")
    print(f"  GPU           : {'var (nvidia-smi okunuyor)' if gpu_var else 'yok / okunamıyor'}")
    print(f"  aralık        : {a.aralik} sn")
    print(f"  çıktı         : {yol}")
    print(f"\n  Asıl koşuyu ŞİMDİ başka bir terminalde başlatın.")
    print(f"  Durdurmak için Ctrl+C.\n")
    print(f"  {'geçen':>8s} {'CPU%':>7s} {'RAM MB':>9s} {'ollama%':>8s} {'ollama MB':>10s} "
          f"{'GPU%':>6s} {'VRAM MB':>9s}")
    print(f"  {'-' * 66}")

    # psutil.cpu_percent ilk çağrıda 0 döner (referans noktası kuruyor) — ısıtıyoruz.
    psutil.cpu_percent(interval=None)
    for p in ollama_surecleri(psutil):
        try:
            p.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001
            pass

    satirlar: list[dict] = []
    t0 = time.time()
    with open(yol, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=ALANLAR, delimiter=";")
        w.writeheader()

        while not _durduruldu:
            time.sleep(a.aralik)
            gecen = time.time() - t0
            if a.sure and gecen >= a.sure:
                break

            bellek = psutil.virtual_memory()
            o_cpu = o_ram = 0.0
            surecler = ollama_surecleri(psutil)
            for p in surecler:
                try:
                    o_cpu += p.cpu_percent(interval=None)
                    o_ram += p.memory_info().rss / 1024**2
                except Exception:  # noqa: BLE001 — süreç bu arada kapanmış olabilir
                    continue
            g_kullanim, g_vram = gpu_oku()

            satir = {
                "zaman": datetime.now().isoformat(timespec="seconds"),
                "gecen_sn": round(gecen, 1),
                "cpu_yuzde": round(psutil.cpu_percent(interval=None), 1),
                "ram_mb": round(bellek.used / 1024**2, 1),
                "ram_yuzde": round(bellek.percent, 1),
                "ollama_cpu_yuzde": round(o_cpu, 1),
                "ollama_ram_mb": round(o_ram, 1),
                "ollama_surec_sayisi": len(surecler),
                "gpu_yuzde": g_kullanim,
                "gpu_vram_mb": g_vram,
            }
            satirlar.append(satir)
            w.writerow(satir)
            f.flush()   # koşu yarıda kesilse bile veri kaybolmasın

            print(f"  {satir['gecen_sn']:>7.0f}s {satir['cpu_yuzde']:>7.1f} "
                  f"{satir['ram_mb']:>9.0f} {satir['ollama_cpu_yuzde']:>8.1f} "
                  f"{satir['ollama_ram_mb']:>10.0f} "
                  f"{'' if g_kullanim is None else f'{g_kullanim:>6.0f}'} "
                  f"{'' if g_vram is None else f'{g_vram:>9.0f}'}", flush=True)

    # ---------------------------------------------------------------- ÖZET
    print(f"\n{'=' * 74}\nÖZET — {len(satirlar)} örnek, {(time.time()-t0)/60:.1f} dakika\n{'=' * 74}")
    if not satirlar:
        print("  Örnek alınamadı.")
        return 1

    al = lambda k: [s[k] for s in satirlar if s[k] is not None]  # noqa: E731
    print(_ozet("sistem CPU", al("cpu_yuzde"), "%"))
    print(_ozet("sistem RAM", al("ram_mb"), " MB"))
    print(_ozet("ollama CPU", al("ollama_cpu_yuzde"), "%"))
    print(_ozet("ollama RAM", al("ollama_ram_mb"), " MB"))
    if gpu_var:
        print(_ozet("GPU kullanım", al("gpu_yuzde"), "%"))
        print(_ozet("GPU VRAM", al("gpu_vram_mb"), " MB"))

    tepe_cpu = max(al("ollama_cpu_yuzde") or [0])
    print(f"\n  Ollama CPU tepesi {tepe_cpu:.0f}% — {cekirdek} çekirdeğin "
          f"{tepe_cpu/100:.1f} tanesine denk geliyor.")
    if tepe_cpu >= cekirdek * 90:
        print("  Çekirdekler DOYMUŞ — paralel istek hız kazandırmaz, çekirdek eklemek gerekir.")
    elif tepe_cpu < cekirdek * 50:
        print("  Çekirdekler doymamış — paralel istek hız kazandırabilir.")

    print(f"\n  CSV: {yol}")
    print("  GPU ve CPU koşularını aynı script'le ölçüp yan yana koyun.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
