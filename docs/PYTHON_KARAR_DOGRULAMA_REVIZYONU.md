# Python Karar Doğrulama Revizyonu

## Amaç

Bu revizyon yalnızca Qwen çıktısından sonraki Python karar katmanını değiştirir. Model, istem ve aday getirme ayarları bu çalışma kapsamında değiştirilmemiştir. Amaç, modelin yazdığı her zorunlu kriteri sorgusuz kabul eden eski davranışı kaldırmak ve nihai kararı gerçek ihale metnine dayandırmaktır.

10 ihalelik önceki çalışmada Qwen 6 `uygun`, 4 `uygun_degil` kararı üretmiş; Python katmanı bunların 9'unu `inceleme_gerekli` sonucuna çevirmiştir. Kök nedenler şunlardır:

- “Kriter belirtilmemiştir” ifadesinin eksik zorunlu kriter sayılması,
- `4.3` gibi bölüm başlıklarının gerçek şart kabul edilmesi,
- profil boşluklarının gerçek ihale şartından ayrılmaması,
- negatif kapsam terimlerinin yalnız tam cümle eşleşmesiyle aranması,
- faaliyet reddi ile katılım yeterliliği reddinin aynı bayrakta tutulması.

## Değiştirilen dosyalar

| Dosya | Değişiklik |
|---|---|
| `app/decision/criterion_evidence.py` | Yeni kaynak doğrulama katmanı. Model kriterini ilgili metin parçasında arar ve sınıflandırır. |
| `app/decision/validator.py` | Yalnız kaynakta doğrulanan gerçek kriterleri engelleyici yapar; karar ve gerekçe çelişkilerini yakalar. |
| `app/decision/activity_scope.py` | Güvenli ek ve ihale kalıbı farklarını destekleyen negatif kapsam eşleştirmesi ekler. |
| `app/decision/models.py` | Kriter kaynak değerlendirmesi, katılım reddi ayrımı ve doğrulama bağlamı alanlarını ekler. |
| `app/decision/isbak_decision_pipeline.py` | Faaliyet ve katılım sonuçlarını ayrı birleştirir; doğrulanmış ret izini raporlar. |
| `scripts/run_tender_decision_chain.py` | İhale türünü doğrulama bağlamına aktarır. |
| `app/reporting/decision_reporter.py` | Kaynak sınıflandırmalarını CSV ve JSONL raporlarına ekler. |
| `tests/decision/test_source_grounded_python_validation.py` | Gerçek hata örneklerini sabit gerileme testlerine dönüştürür. |

## Kriter karar matrisi

Python katmanı her model kriterini aşağıdaki dört kaynak durumundan birine ayırır:

| Kaynak durumu | Anlamı | Nihai karara etkisi |
|---|---|---|
| `mandatory` | Metin parçası açık bir zorunlu yeterlilik şartı içeriyor. | `bilinmiyor` ise `inceleme_gerekli`; `karsilanmiyor` ise `uygun_degil`. |
| `non_blocking` | Standart idari veya teklif hazırlama şartı. | Faaliyet kararını değiştirmez; bilgi olarak raporlanır. |
| `not_required` | Kaynak açıkça kriterin belirtilmediğini veya istenmediğini söylüyor. | Engelleyici yapılmaz. |
| `unverified` | Model iddiasını doğrulayacak açık kaynak hükmü bulunamadı. | Modelin kriter iddiası uyarı olarak tutulur; tek başına kararı kilitlemez. |

Kaynak metninin tamamen eksik olması, uydurma metin parçası kimliği kullanılması veya model kararının gerekçeleriyle çelişmesi güvenlik sorunudur ve `inceleme_gerekli` üretmeye devam eder.

## Negatif faaliyet kapsamı

Tam cümle eşleşmesine ek olarak en az iki ayırt edici sözcükten oluşan güvenli bir eşleştirme uygulanır. Bu sayede aşağıdaki eşleşmeler doğrulanır:

- `yapım işi kontrollüğü` ↔ `Bina Yapım İşi`
- `araç kiralama hizmeti` ↔ `Araç Kiralama Hizmet Alımı`
- `trafik işaret levhası alımı` ↔ `Trafik İşaret Levhaları Alımı`

Tek bir genel sözcük eşleşmesi ret için yeterli değildir. Örneğin `mali danışmanlık`, yalnız “mali” sözcüğü geçen bir yazılım ihalesini negatif kapsam saymaz.

## Faaliyet ve katılım ayrımı

`verified_rejection` alanı genel ret izini korur. Yeni `mandatory_rejection_verified` alanı ise yalnız şirketin gerçek zorunlu katılım kriterini karşılamadığının doğrulandığı durumu temsil eder.

Bu ayrım sayesinde araç kiralama gibi faaliyet dışı bir ihale `uygun_degil` kalırken, katılım durumu yanlış biçimde `karsilanmiyor` olarak yazılmaz. Katılım durumu yalnız doğrulanmış katılım şartlarına göre belirlenir.

## Raporlanabilirlik

Yeni JSONL çıktısı şunları içerir:

- Her model kriteri için kaynak sınıfı,
- Eşleşen metin parçası kimlikleri,
- Eşleşen zorunluluk veya olumsuzluk ifadeleri,
- Kısa kaynak alıntısı,
- İhale türü, OKAS kodları, kanıt metinleri ve profil sinyallerinden oluşan doğrulama bağlamı,
- Ham model kararı, nihai karar ve uygulanan birleştirme kuralı.

Bu bilgiler, model yeniden çalıştırılmadan Python kurallarının neden karar değiştirdiğinin denetlenmesini sağlar.

## Doğrulama komutları

```bash
python -m compileall -q app scripts tests
ruff check app/decision app/reporting/decision_reporter.py \
  scripts/run_tender_decision_chain.py \
  tests/decision/test_source_grounded_python_validation.py
pytest -q tests/decision tests/unit/test_validation_override_merge.py \
  tests/unit/test_single_model_decision_pipeline.py
```

## Sonraki test sırası

1. Aynı 10 ihaleyi güncel kodla yeniden çalıştırın.
2. JSONL dosyasındaki `validation.criterion_assessments` alanını inceleyin.
3. `not_required` ve `unverified` sınıflarının nihai kararı kilitlemediğini doğrulayın.
4. Gerçek iş deneyimi, sertifika veya ekipman şartlarının `mandatory` kaldığını doğrulayın.
5. Önceki ve yeni koşuda ham karar, nihai karar, birleştirme kuralı ve süreleri karşılaştırın.
6. Bu 10 kayıt doğrulandıktan sonra 30–50 insan etiketli ihaleye geçin.

Model, istem, Regex (düzenli ifade) destekli bağlam küçültme ve bölüm başına `top_k=1` ayarları bu doğruluk revizyonu doğrulandıktan sonra ayrı bir hız deneyi olarak uygulanmalıdır.
