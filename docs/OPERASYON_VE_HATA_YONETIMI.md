# Operasyon ve Hata Yönetimi
Bu belge, İSBAK İhale Karar Katmanı'nın üretim ortamındaki güvenilirliğini ve sorun giderme süreçlerini açıklamaktadır. Sistem, kısmi sonuçlar (fail-open) yerine açık başarısızlık (fail-fast) prensibiyle çalışır. 

## 1. Hata Sınıfları
- **DatabaseAccessError:** PostgreSQL veya Qdrant servislerine erişim sağlanamadığında fırlatılır. Bu bir sistem hatasıdır; ihale "inceleme_gerekli" kararına dönüştürülmez, doğrudan analiz iptal edilir.
- **DecisionServiceError:** Ollama (LLM) servisindeki zaman aşımı, HTTP 500 hataları veya şema (JSON) doğrulama sorunları bu hatayı tetikler.
- **TenderAnalysisError:** Belge bütünlüğünün olmaması (eksik veri) veya ihale profil analizinin başarısız olması gibi jenerik ihale işleme sorunlarını kapsar.
- **ReportExistsError:** Aynı İKN numarasının `--force` olmadan tekrar analiz edilmeye çalışılması durumunda fırlatılır.

## 2. Zaman Aşımı ve Yeniden Deneme (Retry) Politikası
- LLM bağlantısı: `10` saniye, Karar/Oluşturma: `120` saniye ile sınırlandırılmıştır.
- Zaman aşımı (Timeout) ve bağlantı kopması (ConnectionError) durumlarında sistem **Exponential Backoff** ile `2` defa yeniden dener. (Örn: 1 sn, ardından 2 sn).
- **Yeniden Denenmeyen Durumlar:**
  - `HTTP 404` (Model bulunamadı)
  - `HTTP 413` (Bağlam limiti aşıldı)
  - Diğer `4xx` istemci yapılandırma hataları.
  Bu hatalar hemen istisna (exception) fırlatarak sistemi kilitler.

## 3. JSON Düzeltme Davranışı
LLM (özellikle Gemma gibi küçük modeller) bazen geçerli bir JSON döndürmeyebilir veya metin kesintiye uğrayabilir (`is_truncated`).
- Eğer model geçersiz JSON üretirse, sistem yalnızca *bir* defaya mahsus yeniden deneme (retry) yaparak modeli düzeltmeye zorlar.
- İkinci çağrıda da geçersiz JSON veya yapılandırma hatası (`ValueError`) gelirse, kısmi bir analiz üretilmez, analiz `DecisionServiceError` ile sonlanır.

## 4. İkincil Model (Fallback) Davranışı
Gemma (veya yapılandırılan ikincil model), Qwen'in kararı "ilgisiz" olduğunda veya karar güven düzeyi düşük olduğunda otomatik olarak çağrılır. İkincil modele yapılan isteğin zaman aşımı veya hataları da birincil modelle aynı şekilde `DecisionServiceError` fırlatır ve kısmi sonucu gizlemez.

## 5. Güvenli Günlükleme (Logging) Politikası
Güvenlik ihlallerini ve gizlilik sızıntılarını önlemek adına:
- `INFO` seviyesinde sadece operasyonel metrikler (Analiz Kimliği/UUID, Geçen süre, Model Adı, Hata Tipi) günlüklenir.
- Veritabanı parolaları, bağlantı dizeleri veya LLM'e giden "Ham Şirket/İhale Bağlamı" kesinlikle `INFO` olarak yazdırılmaz. (Geçici test amaçlı olarak `DEBUG` seviyesinde izole edilir).
- CLI üzerinden komut çalıştırıldığında hata mesajları Türkçe, güvenli ve detay (traceback) gizlenecek şekildedir (Geliştirici modu `--verbose` hariç).

## 6. Tekrar Analiz Politikası (Idempotency)
- Sistem aynı İKN için (örn: `analysis_2026_123456.json`) oluşturulan raporları koruma eğilimindedir. 
- Analiz çalıştırıldığında raporun zaten mevcut olduğu görülürse sistem API ve DB çağrıları yapmadan iptal olur (Maliyet Tasarrufu).
- Eğer `--force` bayrağı ile çalıştırılırsa, mevcut rapor `<tarih>_<eski_id>` eklenerek yedeklenir ve yeni rapor oluşturulur. Geçmiş analizler kaybolmaz.

## 7. Bağlam Sınırı (Context Limit)
- LLM'lerin bellek aşımını engellemek için `max_tender_context_chars = 30000` limiti uygulanır.
- Bu sınır aşılırsa, ana ihale şartnamesi kesilmez. Bunun yerine *en düşük öncelikli geçmiş ihale kanıtları* bağlamdan çıkarılır.
- Kaç kanıtın çıkarıldığı JSON raporundaki `omitted_evidence` alanında ve metadata'da barındırılır.

## 8. Dış Servis Bağımlılıkları ve Otomatik Test
Sürekli Bütünleştirme (CI/CD) kapsamında GitHub Actions kullanılmaktadır. 
- Ollama, PostgreSQL ve Qdrant dış servis bağımlılıkları CI test ortamında hazır bulunmayacağından, bu servislere temas eden testler `@pytest.mark.external` işaretiyle atlanır.
- Testler `python -m pytest -q -m "not external"` ile izole çalıştırılır.

## 9. Gerçek Ortamda Doğrulanması Gerekenler
- Ağ gecikmeleri (Network latency) nedeniyle 120 saniyelik LLM zaman aşımının, canlı ortamdaki (ör: yoğun çalışan bir Ollama sunucusu) ihtiyaçları karşılayıp karşılamadığı.
- RAG kanıt sayısının (varsayılan: 5) getirdiği maliyet (Ollama üzerinde token işlemesi).
- GPU Bellek kullanımının eş zamanlı istek (concurrent runs) durumunda çökme yaratıp yaratmadığı.
