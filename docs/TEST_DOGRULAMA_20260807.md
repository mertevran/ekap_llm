# SQL Encoder V1 — Test Doğrulama Kaydı

Tarih: 2026-08-07

## Kapsam

Bu kayıt, SQL Encoder katmanının doğrudan karar zincirine bağlanan sürümünü kapsar. Ayrı sunucu, istemci veya ağ taşıması yoktur.

## Yerel doğrulama komutları

```bash
python -m compileall -q app scripts tests
ruff check app scripts tests
mypy --follow-imports silent app/sql_encoder

PYTHONPATH=. pytest -q -m "not external" tests/unit tests/decision
PYTHONPATH=. pytest -q -m external tests/integration/test_sql_encoder_database.py
```

## 2026-08-07 sonucu

| Kontrol | Sonuç |
|---|---|
| Python derleme denetimi | Başarılı |
| Ruff kod kalitesi | Başarılı |
| SQL Encoder Mypy tür denetimi | 10 kaynak dosyada başarılı |
| SQL Encoder ve FAISS alt-küme testleri | 33 geçti |
| Tüm harici olmayan proje testleri | 392 geçti, 19 kontrollü atlandı, 24 harici test seçilmedi |

Atlanan testler bu doğrulama ortamında `sentence-transformers`, Qdrant veya gerçek FAISS dosyaları bulunmamasından kaynaklanır. Canlı PostgreSQL ve Ollama kabulü aşağıdaki Ubuntu komutlarıyla kullanıcı ortamında yapılacaktır.

## Canlı kabul gereksinimleri

Yerel ve yalıtılmış testler, gerçek PostgreSQL şemasını, Ollama modelini veya kullanıcının tam FAISS dosyalarını doğrulamaz. Üretim kabulünde aşağıdaki iki komut Ubuntu terminalinde ayrıca çalıştırılmalıdır:

```bash
PYTHONPATH=. python -u scripts/check_sql_encoder.py \
  --request "Aktif durumdaki 5 rastgele ihaleyi getir." \
  --random-seed 20260806

RUN_TIME=$(date +%Y%m%d_%H%M%S)
PYTHONPATH=. python -u scripts/run_sql_encoder_tender_decision_chain.py \
  --request "Aktif durumdaki 5 rastgele ihaleyi getir." \
  --selection-random-seed 20260806 \
  --report-dir "reports/sql_encoder_preflight_${RUN_TIME}" \
  -- --retrieval-only --log-level INFO
```

Kabul ölçütleri:

- `sql_encoder_selection.json` tam 5 benzersiz İKN içermelidir.
- SQL yalnız `SELECT` olmalı ve kullanıcı değerleri `parameters` alanında bulunmalıdır.
- `sql_encoder_selection_coverage.json` içinde `missing_ikns` boş ve `coverage_complete` doğru olmalıdır.
- Seçili adaylar eski tam FAISS indeksinden gelmeli ve aynı İKN iki kez modele gönderilmemelidir.
