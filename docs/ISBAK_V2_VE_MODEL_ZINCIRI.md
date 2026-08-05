# İSBAK Sınıflandırıcı V2 ve Model Zinciri Temeli

## Bu sürümde çözülen sorunlar

- Aynı terim birden fazla kaynakta geçse bile yalnızca en güçlü kaynak puanlanır.
- Geniş OKAS kodları tek başına güçlü eşleşme üretmez.
- Genel terimler düşük ağırlıkla değerlendirilir.
- Profil dışı olumsuz terimler eşleşmeyi bastırır.
- Güçlü eşleşme için alan-özgü güçlü terim gerekir.
- Yalnızca OKAS veya genel terim varsa kayıt inceleme düzeyinde kalır.

## Model zinciri

`app/decision` altında şu temel akış hazırdır:

Qwen -> kod doğrulaması -> gerektiğinde Gemma -> nihai karar

Bu dosyalar henüz Ollama çağrısı yapmaz. Önce sınıflandırma ve veri temeli
doğrulanır; ardından mevcut model çağrı katmanına bağlanır.

## Kurulum

```bash
bash isbak_classifier_v2_model_chain_foundation/install_local.sh   /mnt/c/Users/merte/OneDrive/Masaüstü/ekap_rag_3model
```

## Sınamalar

```bash
PYTHONPATH=. pytest -q   tests/test_isbak_profile_loader.py   tests/unit/test_isbak_tender_profile_classifier.py   tests/unit/test_isbak_tender_payload.py   tests/unit/test_isbak_tender_profile_classifier_v2.py   tests/unit/test_isbak_decision_pipeline.py
```

## 100 kayıtlık V2 denemesi

```bash
PYTHONPATH=. python scripts/classify_isbak_tenders_v2.py   --limit 100   --batch-size 50
```
