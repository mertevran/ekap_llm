# EKAP-İSBAK İhale Analiz Sistemi Genel Raporu

Bu rapor, EKAP-İSBAK (Elektronik Kamu Alımları Platformu - İstanbul Bilişim ve Akıllı Kent Teknolojileri A.Ş.) ihalelerini otonom olarak analiz eden ve uygunluk kararı veren yapay zeka destekli RAG (Retrieval-Augmented Generation) sisteminin mimarisini, çalışma mantığını, sistem parçalarını ve kullanılan kaynakları özetlemektedir.

---

## 1. Sistemin Amacı ve Temel Yaklaşımı
EKAP üzerinden yayınlanan kamu ihalelerinin şartnameleri, ilanları ve idari gereksinimleri oldukça hacimli yapıya sahiptir. Sistem; bu metin yığınlarını, İSBAK’ın kurumsal çalışma profilleriyle (Yazılım, Donanım, Ar-Ge vb.) karşılaştırarak anlamsal açıdan eşleştirir. Nihayetinde ihale şartlarının İSBAK yetkinliklerine uygun olup olmadığına dair insan benzeri ancak katı kurallara bağlı deterministik (açık, hatasız) bir karar (*uygun*, *uygun_değil*, *inceleme_gerekli*) üretir.

Sistem, yapay zeka halüsinasyonlarını engellemek adına LLM çıktılarını "Blackbox" (kapalı kutu) olarak kabul etmez; Python iş kuralları ve hata denetim zinciri ile modelleri sınayarak yönlendirir.

---

## 2. Sistem Bileşenleri ve Kaynakları

Sistem çalışırken farklı aşamalarda görev alan teknolojik bileşenler şunlardır:

### A. Veri Kaynağı ve Vektör Arama Katmanı
1. **PostgreSQL Veritabanı:** 
   - EKAP ihalelerine ait ham özellikler, OKAS (Ortak Kamu Alımları Sözlüğü) kodları ve ihale bölümleri salt okunur (read-only) şekilde sistem tarafından veritabanından çekilir.
2. **Parçalama (Chunking) ve İndeksleme:** 
   - İhalenin "kapsam", "teknik özellik" ve "idari" bölümleri mantıksal yapıları bozulmadan (bölüm farkındalıklı - section aware) `BAAI/bge-m3` embedding (gömme) modeli ile vektörize edilir.
3. **FAISS (Facebook AI Similarity Search):**
   - Vektörize edilen ihale şartları, bellek-içi (in-memory) L2-normalize edilmiş `IndexFlatIP` FAISS indeksinde toplanır. FAISS, binlerce ihale parçası içinden İSBAK profiline en uygun kısımları milisaniyeler içerisinde anlamsal olarak getirir.

### B. Karar Mekanizması ve LLM Kaynakları (Ollama)
Karar süreci, **Ollama** sunucusu üzerinden çalışan açık kaynaklı yerel (local) Büyük Dil Modelleri ile çift katmanlı yürütülür:
1. **Birincil Karar Motoru (Qwen):**
   - Kaynak: `qwen3.5:4b-q4_K_M`
   - Görevi: FAISS'ten gelen ilgili ihale metin parçalarını, İSBAK profilleriyle karşılaştırır. İhale zorunlu kriterleri, yeterlilik gerekçeleri ve eksik kanıt listelerini çıkartarak bir taslak karar (`uygun` vs.) üretir.
2. **İkincil Karar Motoru / İkinci Görüş (Gemma):**
   - Kaynak: `gemma4:e2b-it-q4_K_M`
   - Görevi: Birincil modelin düşük güvenle (`confidence`) karar vermesi, eksik kanıt sunması veya sistemsel bir uyuşmazlık çıkması durumunda "ikinci görüş" olarak devreye girer. Birincil modelden tamamen bağımsız olarak aynı veriyi analiz eder.

### C. Doğrulama ve Yönlendirme (Python Guardrails)
1. **IsbakDeterministicValidator & Karar Birleştirici (`isbak_decision_pipeline.py`):**
   - LLM'lerin kendi başına kuralsız kararlar vermesini engelleyen omurgadır.
   - İki model arasında fikir ayrılığı (çelişki) varsa, kararı doğrudan "inceleme_gerekli" yapar.
   - Modelin ürettiği her kriteri ve anlamsal alanı tek tek denetler.
2. **Anlamsal Denetim (`ollama_decision_model.py`):**
   - Fiyat avantajı, standart ihale süreçleri veya uydurma kaynak kodlarının (halüsinasyon) model kararlarını manipüle etmesini engeller.
   - Açık bir negatif kanıt bulunmadan ihalenin "karsilanmiyor" damgası yemesini önler.

---

## 3. Sistem Akışı (Uçtan Uca)

1. **Veri Toplama ve Hazırlık:** PostgreSQL'den okunan güncel ihaleler, hash takibi yapılarak sadece yeni/değişen kısımlarıyla parçalanıp FAISS indeksine kaydedilir (`build_active_tenders_faiss.py`).
2. **Aday Arama:** Şirket profilleri FAISS'te sorgulanır. Bulunan eşleşmelerin skorları belirli bir formülle (OKAS puanı, çeşitlilik, bölüm puanları vb.) harmanlanıp 0.35 barajı üzerindeki ihaleler seçilir.
3. **LLM Birincil Analiz:** Eşleşen ihaleler Qwen'e gönderilir. Model `ModelDecision` formatında zorunlu JSON yapısı üretir.
4. **Semantik Doğrulama & Düzeltme (Retry):**
   - Üretilen JSON yapısında, var olmayan ihale parça kodları veya haksız "uygun değil" sebepleri (örn. fiyat avantajının kriter yapılması) saptanırsa, modelden aynı işlem için belirgin bir uyarı mesajıyla (*"Fiyat avantajını çıkar, eksik kanıtı düzelt vb."*) düzeltme istenir (`max_json_corrections`).
5. **Güvenli Kurtarma (Graceful Degradation):**
   - Birincil modelin tüm düzeltme hakları bitmesine rağmen sadece küçük yardımcı kısımlarda (`ikincil_profil_kodlari` veya `zorunlu_kriter_sonuclari`) mantık hatası kaldıysa, karar hattı çöpe atılmaz. Hatalı kriterler silinerek ana karar (decision) kurtarılır.
6. **İkincil Görüş ve Karşılaştırma:** Güven düşükse veya çelişki riski varsa ikincil Gemma modeli çağrılır. Kararlar Python boru hattında (`IsbakDecisionPipeline`) karşılaştırılır ve konsensüse veya insan onayına yönlendirilir.
7. **Raporlama:** Nihai karar JSON, CSV ve Markdown olarak diskteki `reports/` dizinine kaydedilir.

---

## 4. Dosya ve Klasör Hiyerarşisi (Sistem Mimarisi)

- **`app/database/ & app/indexing/`**: PostgreSQL bağlantısı, BGE-M3 veri parçalama ve indeks oluşturma sınıfları.
- **`app/vector_store/`**: FAISS mimarisi, anlamsal arama (Retrieval) fonksiyonları.
- **`app/decision/`**: Sistemin beyni. `ollama_decision_model.py` (LLM sunucu iletişimi ve anlamsal validasyon), `isbak_decision_pipeline.py` (Modeller arası karar zinciri ve deterministik onay) ve `decision_normalizer.py` içerir.
- **`app/profiles/ & app/config/`**: İSBAK şirket JSON profilleri ve `.env` üzerinden sistem ayarları.
- **`scripts/`**: Ana çalıştırma dosyaları (`build_active_tenders_faiss.py` indeksleme için, `run_tender_decision_chain.py` karar üretim süreci için).
- **`tests/`**: Unit ve E2E testleri (pytest).

---

## 5. Sistem Sınırları ve Kurulum İhtiyaçları
- **Bağımlılıklar:** Python 3.11+, PostgreSQL (salt-okunur), Ollama servisi (Qwen ve Gemma), FAISS (CPU/GPU).
- **Donanım İhtiyacı:** BGE-M3 gömme modelleri hacimlidir, RAG performansı için gömme tarafında veya Ollama tarafında GPU tavsiye edilir. Sistem tam uyumlu olarak CPU üzerinde de çalışabilmektedir.
- **Kapalı Sistem:** Dış internete (OpenAI vs.) API çağrısı yapmaz. Tüm veri ve LLM süreçleri lokal (on-prem) güvenli ortamda tutulur.
