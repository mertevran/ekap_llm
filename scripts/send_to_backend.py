#!/usr/bin/env python3
"""
Tarama sonuçlarını bir web API'ye gönderir.
========================================================================
Bu script, toplu taramanın ürettiği JSONL dosyasını okur ve içindeki kayıtları
tek bir POST isteğiyle bir backend API'ye gönderir. OPSİYONELDİR — sistemin
çalışması için gerekli değildir; yalnızca sonuçları başka bir uygulamaya
aktarmak isteyenler içindir.

Kullanım:

    # Hedef adresi ortam değişkeniyle verin
    export BACKEND_API_URL=http://sunucu-adresi:5080/api/ai-evaluations/import-batch

    python scripts/send_to_backend.py Sonuclar/toplu_tarama/tam_tarama.jsonl

BACKEND_API_URL tanımlı değilse script hiçbir şey göndermeden uyarı verip çıkar.
Sunucu adresi bilerek koda gömülmemiştir.
"""

import sys
import os
import json
import urllib.request
import urllib.error

# Hedef API adresi. Ortam değişkeninden okunur; varsayılan YOKTUR.
BACKEND_URL = os.getenv("BACKEND_API_URL", "")

# Dosya adı verilmezse kullanılacak varsayılan JSONL yolu (proje köküne göre).
PROJE_KOKU = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_JSONL = os.path.join(PROJE_KOKU, "Sonuclar", "toplu_tarama", "tam_tarama.jsonl")


def send_evaluations(jsonl_path=None):
    if not BACKEND_URL:
        print("HATA: BACKEND_API_URL ortam degiskeni tanimli degil.")
        print("Ornek: export BACKEND_API_URL=http://sunucu:5080/api/ai-evaluations/import-batch")
        return False

    target_path = jsonl_path or DEFAULT_JSONL

    if not os.path.exists(target_path):
        print(f"HATA: {target_path} dosyasi bulunamadi!")
        return False

    print(f"DOSYA OKUNUYOR: {target_path}")

    items = []
    with open(target_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                if data.get("ikn"):
                    items.append(data)
            except Exception as e:
                print(f"Satir {line_num} JSON parse hatasi: {e}")

    if not items:
        print("Uyari: Gonderilecek gecerli ihale verisi bulunamadi.")
        return False

    print(f"POST HAZIRLANIYOR: {len(items)} adet LLM analizi -> {BACKEND_URL}")

    payload = json.dumps(items, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        BACKEND_URL,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status_code = resp.getcode()
            response_body = resp.read().decode("utf-8")
            print(f"SUCCESS ({status_code}): {response_body}")
            return True
    except urllib.error.HTTPError as e:
        print(f"HTTP ERROR ({e.code}): {e.read().decode('utf-8')}")
        return False
    except Exception as e:
        print(f"BAGLANTI HATASI: {e}")
        return False


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else None
    send_evaluations(filepath)
