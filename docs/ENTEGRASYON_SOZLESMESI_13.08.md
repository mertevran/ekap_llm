# LLM V2 ↔ Backend Entegrasyon Sözleşmesi

**Temel kural:** Backend ve frontend tarafına dokunulmaz. `LLM-dev-mert` (V2),
mevcut backend sözleşmesine uyum sağlar.

## Tek temas noktası

```http
POST /api/ai-evaluations/import-batch
Content-Type: application/json

[ { ...item... }, { ...item... }, ... ]
```

Backend LLM'in modelini, prompt'unu, FAISS yapısını veya Python doğrulayıcısını
bilmez. LLM yalnızca aşağıdaki JSON alanlarını üretir:

| Alan | Durum | V2 kaynağı |
|---|---|---|
| `ikn` | zorunlu | `FinalTenderDecision.ikn` |
| `tender_id` | opsiyonel | `FinalTenderDecision.tender_id` |
| `karar` | zorunlu | entegrasyon mapping'i |
| `ilgi_skoru` | opsiyonel | `final_confidence` |
| `en_ust_benzerlik` | opsiyonel | en yüksek `profile_match_scores` |
| `eslesen_paket` | opsiyonel | `primary_profile_code` |
| `gerekce` | opsiyonel | deterministik profesyonel karar gerekçesi |
| `on_filtre` | opsiyonel | V2'de şimdilik `null` |
| `notlar` | serbest JSON | V2 tanı/inceleme metadata'sı |
| `sure_sn` | opsiyonel | dışarıdan süre verilirse kullanılır |

## Karar etiketi uyumluluğu

V2'nin iç karar sözleşmesi değiştirilmez:

- `uygun`
- `uygun_degil`
- `inceleme_gerekli`

Backend/frontend sözleşmesi korunur:

- `uygun`
- `uygun_degil`
- `belirsiz`

Bu nedenle yalnızca entegrasyon sınırında:

```text
inceleme_gerekli -> belirsiz
```

dönüşümü uygulanır. Orijinal V2 kararı `notlar.original_decision` içinde korunur.

## Dosyalar

- `app/integration/backend_contract.py`: V2 → backend DTO adaptörü ve doğrulama.
- `app/reporting/decision_reporter.py`: `backend_ai_evaluations.jsonl` üretir.
- `scripts/send_to_backend.py`: JSONL'i mevcut endpoint'e batch POST eder.

HTTP gönderimi karar pipeline'ından bilinçli olarak ayrıdır. Böylece benchmark
ve geliştirme koşuları backend verisini istemeden değiştirmez; analiz tamamlandıktan
sonra aktarım ayrıca çalıştırılır.

## Lokal contract testi

Backend olmadan:

```bash
PYTHONPATH=. python scripts/send_to_backend.py \
  reports/<run>/backend_ai_evaluations.jsonl \
  --dry-run
```

Lokal backend hazır olduğunda:

```bash
BACKEND_API_URL=http://127.0.0.1:5080/api/ai-evaluations/import-batch \
PYTHONPATH=. python scripts/send_to_backend.py \
  reports/<run>/backend_ai_evaluations.jsonl
```

Uzak EKAP PostgreSQL erişimi HTTP sözleşme testinin ön koşulu değildir.
