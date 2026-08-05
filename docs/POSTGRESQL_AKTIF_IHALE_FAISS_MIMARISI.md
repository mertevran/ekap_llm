# PostgreSQL Aktif İhale FAISS Mimarisi

## 1. Değişikliğin Amacı

Bu belge EKAP RAG sisteminin Qdrant tabanlı eski mimarisinden PostgreSQL → FAISS tabanlı yeni mimariye geçişini açıklar. Temel hedefler:

- Aktif ihaleleri PostgreSQL'den gerçek zamanlı okumak
- Bölüm farkındalıklı parçalama ile doğal DB yapısını korumak
- BGE-M3 ile normalize float32 vektörler üretmek
- FAISS IndexFlatIP üzerinde profil bazlı anlamsal arama yapmak
- Çift model + Python doğrulayıcı karar zinciri ile `uygun / uygun_degil / inceleme_gerekli` kararı vermek

---

## 2. Doğrulanan PostgreSQL Şeması

| Tablo | Birincil Anahtar | Temel Alanlar |
|-------|-----------------|---------------|
| `public.tenders` | `id text` | `ikn`, `adi`, `idare_adi`, `il`, `ihale_tarihi`, `ihale_turu`, `ihale_usulu`, `ihale_durumu`, `kapsam`, `e_ihale`, `kismi_teklif` |
| `public.tender_announcements` | `id text` | `tender_id`, `ilan_tipi`, `ilan_tarihi`, `baslik`, `icerik` |
| `public.tender_characteristics` | `id text` | `tender_id`, `ozellik` |
| `public.tender_okas_codes` | `id text` | `tender_id`, `kod`, `ad` |
| `llm_rag.software_tender_index_state` | `tender_id text` | `source_hash`, `index_status`, `chunking_version`, `embedding_model`, `chunk_count` |

> [!IMPORTANT]
> `son_teklif_tarihi` sütunu `public.tenders`'da **yoktur**. Bu alan varsayılmamalıdır.

---

## 3. Aktif İhale Seçimi

`TenderRepository.get_active_tenders()` şu kriterlere göre aktif ihaleleri seçer:

```python
WHERE ihale_durumu = ANY(%s::text[])
```

`active_tender_status_values` ayarı `.env` dosyasından veya `Settings.active_tender_status_values` üzerinden okunur. Gerçek değerleri öğrenmek için:

```bash
python scripts/audit_postgresql_tenders.py
```

İhale tarihi `ihale_tarihi` alanından `datetime.strptime()` ile parse edilir; sözlüksel karşılaştırma yapılmaz. Parse edilemeyen tarihler `reports/active_status_uncertain.csv` dosyasına yazılır.

---

## 4. İSBAK'ın Kendi İhalelerinin Dışlanması

`ActiveTenderIndexer.run()` içinde parçalama ve BGE-M3 çağrısından **önce** `is_isbak_tender()` kontrolü yapılır:

```python
def is_isbak_tender(idare_adi: str | None) -> bool:
    if not idare_adi:
        return False
    norm = unicodedata.normalize("NFKC", idare_adi).lower()
    norm = norm.replace("a.ş.", "").replace("aş", "").replace("anonim şirketi", "")
    norm = re.sub(r'[^\w\s]', '', norm)
    norm = " ".join(norm.split())
    return "isbak" in norm or "istanbul bilisim" in norm or "istanbul bilişim" in norm
```

Dışlanan ihaleler `reports/excluded_own_tenders.csv` dosyasına yazılır. Boş içerikli ihaleler `reports/excluded_empty_tenders.csv` dosyasına yazılır.

> [!CAUTION]
> `ISBAK_AUTHORITY_ALIASES` genişletilirken dikkatli olunmalı. `"istanbul bilişim"` gibi geniş ifadeler farklı kurumları yanlışlıkla dışlayabilir. Gerçek DB denetiminden sonra `reports/isbak_authority_variants.csv` incelenerek kesin ad listesi oluşturulmalıdır.

---

## 5. İşleme Durum Tablosu

`llm_rag.software_tender_index_state` tablosu **yeniden işleme kararı** için kullanılır. Uygunluk kararı kaynağı değildir.

Yeniden işleme tetikleyicileri:

| Koşul | Eylem |
|-------|-------|
| `state is None` | İlk kez işleme |
| `index_status != 'indexed'` | Yeniden işleme |
| `source_hash` değişti | Yeniden işleme |
| `chunking_version` değişti | Yeniden işleme |
| `embedding_model` değişti | Yeniden işleme |
| `embedding_version` değişti | Yeniden işleme |
| `index_version` değişti | Yeniden işleme |
| `cache_version` değişti | Yeniden işleme |

---

## 6. source_hash ve Önbellek

`generate_source_hash(tender)` fonksiyonu ihalenin tüm alanlarından deterministik bir hash üretir. `FaissVectorCache` değişmeyen ihalelerin chunk ve vektörlerini saklar. Önbellek isabet olduğunda BGE-M3 çağrısı yapılmaz.

---

## 7. İSBAK Profil Paketi

```
config/isbak/
├── company_master.json         # Kurum ana profili
├── profile_registry.json       # 20 profil kaydı
├── profiles/                   # Her profil için JSON
│   ├── AUS-01_trafik_yonetimi.json
│   └── ...
└── evaluation_rules/           # Her profil için karar kuralları
```

Profil paketi `IsbakProfileLoader.list_profiles(active_only=True)` ile yüklenir. Profil sayısı kodda sabit değildir; `profile_registry.json` içeriğinden dinamik olarak okunur.

---

## 8. Üçlü Karar Sözleşmesi

Model kararları yalnızca şu üç değerden biri olabilir:

| Karar | Anlamı |
|-------|--------|
| `uygun` | İhale İSBAK için uygun |
| `uygun_degil` | İhale İSBAK için uygun değil |
| `inceleme_gerekli` | İnsan incelemesi gerekli |

`kosullu_uygun` çalışma zamanında `inceleme_gerekli` değerine dönüştürülür ve nedeni kaydedilir. `not_evaluated` durumundaki ihaleler `uygun_degil` sayılmaz.

---

## 9. Bölüm Farkındalıklı Parçalama

`TenderDocumentBuilder.build()` her ihaleyi beş mantıksal bölüme dönüştürür:

| Bölüm | Kaynak | Parçalama Stratejisi |
|-------|--------|---------------------|
| `tender_summary` | `public.tenders` | Tek parça (`_chunk_single`) |
| `scope_and_location` | `public.tenders.kapsam` | Örtüşmeli metin (`_chunk_text_with_overlap`) |
| `announcement` | `public.tender_announcements` | Örtüşmeli metin, her ilan ayrı |
| `characteristics` | `public.tender_characteristics` | Satır tabanlı (`_chunk_characteristics`) |
| `okas` | `public.tender_okas_codes` | Satır tabanlı, kod+ad bir arada |

---

## 10. Tablo ve Madde Koruma

- OKAS kodu ve adı `" - "` ayırıcısıyla aynı satırda tutulur; satır ortasında kesilmez
- Her announcement kendi `section_id`'sine sahiptir; farklı announcement'lar karıştırılmaz
- Characteristic kayıtları kaynak `id` değerleriyle takip edilir

---

## 11. BGE-M3 Gömme Süreci

1. Her parça için `title + "\n" + text` biçiminde gömme metni oluşturulur
2. `BgeM3Embedder.embed()` metni float32 vektöre dönüştürür
3. `faiss.normalize_L2()` ile L2 normalizasyonu uygulanır
4. Vektör normu ≈ 1.0 olur; iç çarpım cosine similarity ile eşdeğer hale gelir

---

## 12. FAISS İndeks Yapısı

```python
base_index = faiss.IndexFlatIP(vector_size)   # İç çarpım (cosine ~ L2 norm sonrası)
index = faiss.IndexIDMap(base_index)           # UUID → int ID eşleşmesi
```

Her vektör `deterministic_point_id("ekap_tender_chunk", chunk_id)` ile UUID5 alır. Payload `pickle` ile `{collection}_payloads.pkl` dosyasına kaydedilir.

---

## 13. Metadata Eşleşmesi

`faiss.index.ntotal == len(payloads)` koşulu her `_save()` çağrısından önce sağlanmalıdır. Metadata kayıplarına karşı her eklemede payload sayısı kontrol edilir.

---

## 14. Atomik İndeks Üretimi

Yeni FAISS indeksi geçici dizinde oluşturulur. Başarılı doğrulama sonrası `Path.replace()` ile atomik geçiş yapılır. Başarısız üretimde mevcut çalışan indeks korunur.

Payload dosyası için:
```python
temp_payload = self.payload_file.with_suffix(".pkl.tmp")
# ... yaz ...
temp_payload.replace(self.payload_file)  # atomik
```

---

## 15. Profil Tabanlı Bilgi Getirme

Her aktif profil için `build_profile_semantic_text(profile)` çağrısıyla semantik sorgu metni oluşturulur. `IsbakTenderRetriever.retrieve()` FAISS'te en yakın `faiss_search_top_k` parçayı getirir.

> [!IMPORTANT]
> FAISS'e kayıt öncesinde profil bazlı uygunluk filtresi **uygulanmaz**. Tüm aktif ve geçerli ihaleler indekste bulunur.

---

## 16. İhale Bazında Sıralama

Aynı `ikn`'ye ait parçalar birleştirilir ve şu formülle nihai puan hesaplanır:

```
final = 0.55 * max_chunk_score
      + 0.20 * top_chunks_mean
      + 0.10 * section_diversity_score    # min(1.0, distinct_section_types / 3)
      + 0.10 * okas_support_score
      + 0.05 * title_support_score
```

Bu puan **yalnızca aday sıralama** içindir. Uygunluk kararı değildir.

---

## 17. Model Karar Zinciri

```
IsbakTenderRetriever.retrieve()
    → İhale bazında birleştirme ve sıralama
        → IsbakDecisionPipeline.run()
            → Model 1 (Birincil): uygun / uygun_degil / inceleme_gerekli
                → IsbakDeterministicValidator.validate()
                    → Zorunlu kriter kontrolü
                    → Chunk ID doğrulama
                    → Eksik kanıt kontrolü
                        → [Tetikleyici varsa] Model 2 (İkincil): bağımsız görüş
                            → Nihai karar
```

---

## 18. Python Doğrulaması

`IsbakDeterministicValidator` şu durumlarda kararı ezer:

| Durum | Eylem |
|-------|-------|
| Zorunlu kriter `karsilanmiyor` | `forced_decision = "uygun_degil"` |
| Zorunlu kriter `bilinmiyor` | `forced_decision = "inceleme_gerekli"` |
| Model `uygun` + eksik kanıt | `forced_decision = "inceleme_gerekli"` |
| Geçersiz chunk_id referansı | `invalid_evidence_references` listesi doldurulur |
| Kaynak dışı bilgi kullanımı | `forced_decision = "inceleme_gerekli"` |

---

## 19. İkinci Görüş Mantığı

İkinci model (ikincil LLM) yalnızca şu tetikleyicilerde çalışır:

- `dusuk_guven_duzeyi`: Model 1 güven skoru eşiğin altında
- `kritik_belirsizlik`: Model 1 kritik belirsizlik bildirdi
- `validator_modeli_ezdi`: Python doğrulayıcı Model 1'i ezdi
- `zorunlu_kriter_eksik`: Zorunlu kriter karşılanmadı

İki model çelişirse sonuç otomatik olarak `inceleme_gerekli` olur.

---

## 20. Qdrant'tan Geçiş

Qdrant çalışma zamanı bağımlılıkları şu koşul sağlanana kadar **kaldırılmaz**:

1. FAISS gerçek üretim verisinde başarıyla doğrulanır
2. `faiss_total_count > 0` ve `index == metadata` eşleşmesi sağlanır
3. End-to-end retrieval testleri geçer

Doğrulama tamamlandığında `qdrant-client` bağımlılığı `requirements.txt`'ten çıkarılır. Eski `storage/qdrant/` dizini **silinmez**, arşive alınır.

---

## 21. Test Sonuçları

| Test Grubu | Dosya | Durum |
|------------|-------|-------|
| PostgreSQL alan eşleşmesi | `test_tender_active_filter.py` | ✓ |
| İSBAK dışlama | `test_isbak_exclusion.py` | ✓ |
| Durum ve önbellek | `test_index_state_cache.py` | ✓ |
| Profil paketi | `test_profile_pack.py` | ✓ |
| Parçalama bütünlüğü | `test_chunker_integrity.py` | ✓ |
| FAISS vektör | `test_faiss_vector_integrity.py` | ✓ |
| Karar zinciri | `test_retrieval_decision_contract.py` | ✓ |
| Retriever API | `test_isbak_tender_retriever.py` | ✓ (yeni API'ye taşındı) |

---

## 22. Gerçek Çalışma Sonuçları

> [!WARNING]
> Bu session'da PostgreSQL `192.168.100.36:5432` adresine **bağlantı zaman aşımı** yaşandı. Gerçek veri doğrulaması yapılamadı.
> 
> **Ortam bağımlılığı nedeniyle doğrulanamayan bölümler:**
> - `ihale_durumu` ve `takip_durumu` gerçek dağılımları
> - İSBAK idare adı varyasyonları
> - Aktif ihale sayısı
> - FAISS indeks oluşturma ve doğrulama
> - End-to-end retrieval testleri
>
> Bağlantı sağlandığında: `python scripts/audit_postgresql_tenders.py`

---

## 23. Bilinen Sınırlamalar

1. `is_isbak_tender()` yalnızca "isbak" ve "istanbul bilişim" ifadelerini arar; kurum kodu veya vergi numarası kullanılmıyor (gerçek DB audit'inden sonra güçlendirilebilir)
2. FAISS `IndexFlatIP` büyük indekslerde (`>1M` vektör) yavaş olabilir; bu aşamada yeterli
3. Parçalama overlap yalnızca uzun serbest metinlere uygulanır
4. Qdrant kodu hâlâ mevcuttur; FAISS doğrulanana kadar kaldırılmaz

---

## 24. Geri Dönüş Yöntemi

FAISS indeksi bozulursa ya da üretim başarısız olursa:

1. `storage/faiss/*.tmp` dosyaları temizlenir
2. Mevcut `*.index` ve `*_payloads.pkl` dosyaları korunur (atomik geçiş sayesinde)
3. `llm_rag.software_tender_index_state` tablosunda `index_status='failed'` kayıtlar bir sonraki çalıştırmada otomatik yeniden işlenir
4. Qdrant koleksiyonları silinmediğinden gerekirse `VECTOR_STORE_BACKEND=qdrant` ile geri dönülebilir
