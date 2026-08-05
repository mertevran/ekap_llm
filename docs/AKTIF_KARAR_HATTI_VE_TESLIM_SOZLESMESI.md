# Aktif Karar Hattı ve Teslim Sözleşmesi

## Aktif üretim akışı

Aktif LLM (büyük dil modeli) tarafı aşağıdaki sırayla çalışır:

1. Aktif ihale parçaları FAISS (benzerlik arama dizini) içinde tutulur.
2. Aktif İSBAK profillerinin hazır vektörleriyle aday ihaleler getirilir.
3. `ProfileVectorTenderMatcher`, gerçek profil dosyasındaki güçlü terimleri,
   negatif terimleri ve OKAS ön eklerini `ScoreAggregator` sınıfına aktarır.
4. Qwen, ihale kanıtları ve birincil profil üzerinden yapılandırılmış faaliyet
   kararı üretir.
5. Python doğrulaması kanıt kimliklerini, gerçek zorunlu şartları, negatif
   kapsamı ve karar tutarlılığını denetler.
6. Güven puanı yalnızca aşağı yönlü sınırlandırılır.
7. Nihai karar `FinalTenderDecision` içinde teknik ayrıntılarıyla saklanır.
8. Arka uç ve ön yüz yalnızca `to_public_dict()` sonucunu kullanır.

## Aktif giriş noktası

Toplu çalıştırma için aktif dosya:

```text
scripts/run_tender_decision_chain.py
```

Aktif mimari `qwen_python_single_model` değeridir. Gemma, Qdrant ve eski
eşleştirme çalıştırıcıları üretim akışının parçası değildir; geçmiş deneylerin
yeniden üretilebilmesi için kod tabanında tutulabilir ancak yeni arka uç
bağlantısında kullanılmamalıdır.

## Arka uç sözleşmesi

Arka uç, `FinalTenderDecision.to_dict()` iç teknik çıktısını kullanıcıya
göndermemelidir. Bunun yerine şu çağrı kullanılmalıdır:

```python
public_response = final_decision.to_public_dict()
```

Kullanıcı sözleşmesi şu alanları içerir:

```json
{
  "tender_id": "123",
  "ikn": "2026/123456",
  "tender_name": "Örnek ihale",
  "authority_name": "Örnek idare",
  "decision": "inceleme_gerekli",
  "confidence": 0.55,
  "decision_summary": "İhale konusu şirketin faaliyet alanıyla güçlü biçimde örtüşmektedir. Zorunlu iş deneyimi şartı doğrulanamadığından insan incelemesi gerekmektedir.",
  "activity_match": "guclu",
  "primary_profile_code": "ENT-05",
  "secondary_profile_codes": [],
  "matched_evidences": [
    {
      "chunk_id": "chk_12",
      "excerpt": "Akıllı ulaşım yazılımının bakım ve destek hizmeti..."
    }
  ],
  "unmet_requirements": [
    {
      "requirement": "İş deneyim belgesi",
      "status": "dogrulanamadi"
    }
  ],
  "conflicts": [],
  "human_review_required": true,
  "human_review_reason": "Gerçek zorunlu kriter doğrulanamadı."
}
```

Sözleşme kuralları:

- Karar özeti en fazla iki cümledir.
- En fazla üç kanıt döndürülür.
- Her kanıt alıntısı en fazla 200 karakterdir.
- Yalnızca kararı etkileyen şartlar ve somut çelişkiler gösterilir.
- Ham model cevabı, tam istem, tam kaynak metinleri ve teknik doğrulama iç
  ayrıntıları kullanıcı sözleşmesine girmez.

## Negatif kapsam politikası

Python doğrulaması negatif kapsamı dört sınıfa ayırır:

| Sınıf | Açıklama | Nihai davranış |
|---|---|---|
| `full` | Negatif kapsam ihale başlığında açık; olumlu sinyal yok | `uygun_degil` |
| `mixed` | Olumlu ve negatif faaliyet kapsamları birlikte | `inceleme_gerekli` |
| `ambiguous` | Negatif ifade yalnız gövde metninde veya kapsam belirsiz | Güvenli inceleme/kararın korunması |
| `none` | Doğrulanmış negatif kapsam yok | Diğer karar kuralları uygulanır |

OKAS kodu tek başına negatif kapsam kanıtı değildir. Negatif ceza gerçek ihale
başlığı ve getirilen ihale parçaları üzerinde hesaplanır.

## Raporlar

`DecisionReporter` iki ayrı JSONL (satır bazlı yapılandırılmış veri) üretir:

- `tender_model_decisions.jsonl`: iç teknik ve tanılama çıktısı.
- `tender_public_decisions.jsonl`: arka uç/ön yüz kullanıcı sözleşmesi.

Veritabanına kayıt sorumluluğu arka uçta kalabilir. LLM tarafının zorunlu
olarak PostgreSQL'e doğrudan yazması gerekmez.

## Doğrulama komutları

```bash
python -m compileall -q app scripts tests
pytest -m "not external and not legacy"
ruff check app scripts tests
```

Gerçek kabul testinde tüm aktif profiller, farklı puan aralıkları, açık uygun,
açık uygun olmayan, karma kapsam ve eksik zorunlu kanıt örnekleri dengeli
biçimde temsil edilmelidir.
