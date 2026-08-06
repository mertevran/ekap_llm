# Veritabanı Kaynaklı Karar Hattı v4

## Amaç ve güvenlik sınırı

Bu sürümde FAISS (vektör dizini) yalnız aday İKN bulur. Model kararının gerçek ihale verisi PostgreSQL (ilişkisel veritabanı) kaynak tablolarından yeniden okunur. Eksik veritabanı kaydında eski FAISS yüküne sessiz dönüş yapılmaz.

`uygun` sonucu yalnız bir aday faaliyet kararıdır. İnsan onayı olmadan teklif, bildirim veya başka bir otomatik işlem başlatılamaz. `automatic_action_allowed` alanı bütün çıktılarda `false` değerindedir.

## Gerçek kaynak şeması

Karar öncesi aşağıdaki tablo ve sütunlar `information_schema` üzerinden doğrulanır:

| Tablo | Kullanılan alanlar |
|---|---|
| `public.tenders` | `id`, `ikn`, `adi`, `idare_adi`, `il`, `ihale_tarihi`, `ihale_turu`, `ihale_usulu`, `ihale_durumu`, `kapsam`, `e_ihale`, `kismi_teklif`, yer alanları, doküman sayısı, kayıt zamanları ve takip durumu |
| `public.tender_announcements` | `id`, `tender_id`, `ilan_tipi`, `ilan_tarihi`, `baslik`, `icerik`, `created_at` |
| `public.tender_characteristics` | `id`, `tender_id`, `ozellik` |
| `public.tender_okas_codes` | `id`, `tender_id`, `kod`, `ad` |

Mevcut şemada ayrı bir ihale kısmı tablosu yoktur. Bu nedenle kısım başlıkları `kapsam`, ilan ve teknik özellik metinlerindeki açık `Kısım`/`Lot (kısım)` ifadelerinden çıkarılır. Her kısımda kaynak tablo ve kayıt kimliği korunur. Kısmi teklif açık olduğu hâlde kısım listesi çıkarılamazsa kesin olumlu karar engellenir.

## Karar bağlamı

Modele aşağıdaki yapılandırılmış alanlar kanıt metinlerinden önce verilir:

1. Gerçek ihale türü, usulü ve durumu.
2. Bütün OKAS kodları ve adları.
3. Kısmi teklif durumu.
4. Çıkarılan bütün kısımlar ve kaynak kimlikleri.
5. `tender_characteristics` tablosundaki bütün teknik özellikler.
6. Vaka türüne göre seçilmiş kaynak parçaları.

Zorunlu yapılandırılmış alanlar bağlam sınırına sığmazsa sessiz kesme yapılmaz; o ihale hata raporuna alınır.

## Dinamik kanıt seçimi

Önce düzenli ifade ve ek/kök toleranslı sinyal taraması yapılır. Ardından:

| Vaka | Kanıt üst sınırı |
|---|---:|
| Tek yönlü açık eşleşme | 1 |
| Açık sinyal bulunamayan belirsiz vaka | 3 |
| Olumlu ve olumsuz sinyalin birlikte bulunduğu karma vaka | 4 |
| Kısmi teklif veya birden çok kısım | 4 |

Belirsiz, karma ve kısmi vakalarda önce farklı kaynak bölümleri seçilir. Kaynakta üst sınırdan az parça varsa yalnız mevcut parçalar kullanılır.

## Karar sözleşmesi

Modelin kısa v4 çıktısı faaliyet ve katılımı ayırır:

- `decision`: yalnız faaliyet kararı.
- `faaliyet_eslesmesi`: güçlü, kısmi, zayıf veya belirsiz.
- `katilim_belirsizlikleri`: belge, personel, iş deneyimi ve benzeri doğrulanamayan katılım şartları.
- `zorunlu_kriter_sonuclari`: yalnız gerçek ihale kaynağında açıkça bulunan şartlar.
- `uygun_kisimlar`: yalnız kısmi ihalelerde, kaynak kısım listesine bağlı sonuçlar.

İç raporda `activity_decision`, `katilim_yeterliligi_durumu` ve `final_decision` ayrı tutulur. Kısa dış rapor `tender_public_decisions.jsonl` dosyasına yazılır.

## Kurulum

Örnek ayarları kopyalayın ve gerçek salt okunur veritabanı hesabını girin:

```bash
cp .env.example .env
```

Ollama hizmeti tek istek ve tek yüklü modelle çalıştırılmalıdır:

```bash
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
```

İş parçacığı sayısını kalıcı seçmeden önce hedef sunucuda ölçün.

## Veritabanı ön kontrolü

Model çağrısı yapmadan bağlantıyı, gerekli şemayı, aday İKN kayıtlarını ve aktif durumları doğrulayın:

```bash
RUN_TIME=$(date +"%Y%m%d_%H%M%S")
PYTHONPATH=. python -u scripts/run_database_tender_decision_chain.py \
  --retrieval-only \
  --limit-per-profile 10 \
  --max-decisions 10 \
  --report-dir "reports/database_source_preflight_${RUN_TIME}"
```

Ön kontrol raporuna parola veya bağlantı dizesi yazılmaz.

## On ihalelik veritabanı kaynaklı karar testi

```bash
RUN_TIME=$(date +"%Y%m%d_%H%M%S")
PYTHONPATH=. python -u scripts/run_database_tender_decision_chain.py \
  --limit-per-profile 10 \
  --max-decisions 10 \
  --random-seed 20260806 \
  --report-dir "reports/database_decision_v4_${RUN_TIME}" \
  --log-level INFO
```

Geçmiş ihalelerle geriye dönük karşılaştırma yapılacaksa açıkça `--allow-inactive-database-records` verilebilir. Bu seçenek canlı aday taramasında kullanılmamalıdır.

`--random-seed` aynı aday havuzunda yeniden üretilebilir rastgele grup seçer. Farklı bir grup için tohumu değiştirin; önceki geçici Python yamasına gerek yoktur.

## İnsan etiketli doğruluk ölçümü

`data/decision_labels_template.csv` dosyasını en az 100, tercihen 100–200 ihale için doldurun. İzin verilen insan etiketleri `uygun`, `uygun_degil` ve `inceleme_gerekli` değerleridir.

```bash
PYTHONPATH=. python scripts/evaluate_labeled_decisions.py \
  --labels data/decision_labels.csv \
  --predictions reports/database_decision_v4/tender_model_decisions.jsonl \
  --output-dir reports/labeled_decision_metrics \
  --minimum-labeled 100
```

Araç sınıf bazında kesinlik, duyarlılık ve F1 ile genel doğruluk, makro ortalama, ağırlıklı ortalama ve karışıklık matrisini üretir. Eşleşen insan etiketi 100'ün altındaysa rapor oluşturulur fakat kabul testi geçersiz sayılır ve komut sıfır olmayan kodla biter.

## 24 çekirdek / 16 GB hedef sunucu testi

Aşağıdaki koşu vakaları sıralı çalıştırır; paralel model isteği açmaz:

```bash
RUN_TIME=$(date +"%Y%m%d_%H%M%S")
PYTHONPATH=. python -u scripts/run_server_capacity_benchmark.py \
  --output-dir "reports/server_capacity_${RUN_TIME}" \
  --threads 8 12 16 24 \
  --comparison-decisions 10 \
  --endurance-decisions 100 \
  --endurance-thread 12 \
  --max-swap-growth-mb 512 \
  --require-target-hardware
```

Her iş parçacığı sayısı aynı sıralı aday grubunda ölçülür. Ardından 100 kararlık dayanıklılık koşusu yapılır. `server_capacity_benchmark_summary.json` dosyası karar/saat değerini, RAM kullanımını, Ollama kullanımını ve takas büyümesini içerir.

Takas alanı yalnız acil korumadır. Ölçüm boyunca başlangıç değerine göre tepe takas büyümesi 512 MB sınırını aşarsa vaka başarısız sayılır. Sınırı yükseltmek performans düzeltmesi olarak kabul edilmemelidir.

## Uygulanan on önceliğin durumu

| Öncelik | Durum |
|---|---|
| Gerçek tür, bütün OKAS, kısmi teklif, kısım ve teknik özellik aktarımı | Uygulandı |
| Faaliyet ve katılım kararlarının ayrılması | Uygulandı ve korunuyor |
| `uygun_kisimlar` | Uygulandı; kaynakta olmayan kısım engelleniyor |
| Olumlu kararda insan onayı | Uygulandı; otomatik işlem kapalı |
| Anlamsal ve ek/kök toleranslı negatif kapsam | Uygulandı |
| Düzenli ifade ön filtresi ve 1/3/4 dinamik kanıt | Uygulandı |
| Kısa model çıktı şeması | v4 olarak uygulandı; v3 geriye dönük deneyler için korunuyor |
| 100–200 insan etiketli ölçüm | Ölçüm aracı hazır; gerçek etiketli veriyle çalıştırılmalı |
| 8/12/16/24 ve 100 karar sunucu testi | Koşucu hazır; hedef sunucuda çalıştırılmalı |
| Takas yalnız acil koruma | Tek istek politikası ve takas büyüme kapısı uygulandı |

## Kabul sınırı

Canlı otonom kullanım için yalnız teknik koşunun tamamlanması yeterli değildir. İnsan etiketli ölçüm raporu ve hedef sunucu dayanıklılık raporu üretilmeden sistem insan destekli aday tarama aracı olarak kalmalıdır.
