# EKAP–İSBAK İhale Karar Destek Sistemi Mimarisi

Bu doküman, projenin genel mimarisini, veri akışını ve hangi modülün nerede olduğunu açıklamak amacıyla hazırlanmıştır. 

## 1. Projenin Genel Klasör Yapısı

Proje kök dizini çeşitli görevlere ayrılmış alt klasörlerden oluşmaktadır.

```text
LLM-dev-mert-tek_model/
├── app/                  # Uygulamanın ana kaynak kodları (Core)
├── config/               # İSBAK departman profilleri (JSON) ve yapılandırmalar
├── data/                 # İhale değerlendirme verileri ve şablonlar
├── docs/                 # Proje dokümantasyonu (varsa ekstra kılavuzlar)
├── evaluation/           # Modelin başarım testleri ve etiketleme (relevance_labels vb.)
├── logs/                 # Sistem çalışma logları (.log dosyaları)
├── outputs/              # Çıktı klasörü (genel amaçlı)
├── reporting/            # Raporlama modülü için ayrılmış yapı
├── reports/              # Üretilen CSV ve JSONL raporları (Çıktılar)
├── scripts/              # Sistemin çalıştırılması için gerekli ana betikler
├── storage/              # FAISS Vektör DB vb. kalıcı / yarı kalıcı depolar
├── tests/                # Pytest birim (unit) ve entegrasyon testleri
└── tools/                # Bakım, yama (patch) ve yardımcı araçlar
```

## 2. `app/` (Ana Uygulama) Modülleri

Sistemin bütün iş mantığı `app/` klasörü altında birbirinden bağımsız, ancak uyum içinde çalışan modüllere ayrılmıştır:

* **`config/`**: Pydantic yapılandırmaları ve çevresel değişkenlerin (`.env`) yönetimi.
* **`database/`**: PostgreSQL bağlantısı, SQLAlchemy ORM modelleri ve veritabanı işlemleri (Repository katmanı).
* **`decision/`**: Karar mekanizması. Ollama üzerinden Qwen LLM'e bağlanma ve en önemlisi **`SourceGroundedPythonValidator`** (Python tabanlı kural ve doğrulama seti) burada yer alır.
* **`domain/`**: Sistemin ortak kullandığı veri modelleri (`TenderRecord`, vb.).
* **`indexing/`**: İhale verilerinin okunup BGE-M3 modeli ile vektörel gömme (embedding) işleminden geçirilmesi ve FAISS indeksine eklenmesi.
* **`matching/`**: Şirket profilleri ile ihale vektörlerinin karşılaştırılması, "Getirim Puanı (Retrieval Score)" hesaplamaları.
* **`pipeline/`**: Veritabanından ihalenin okunmasından LLM kararına kadar geçen tüm sürecin uçtan uca zincir (Chain) halinde çalıştırılması.
* **`reporting/`**: Sonuçların `reports/` dizinine CSV, JSONL olarak yazılması.
* **`retrieval/`**: FAISS üzerinden vektör sorgulama, benzer ihaleleri bulma ve Qwen için gönderilecek kanıtların seçimi.
* **`vector_store/`**: FAISS indeks yapısının yönetilmesi (IndexIDMap, bellek-içi tutma işlemleri).

## 3. Sistem Mimarisi ve Veri Akışı

Sistem "Source-Grounded RAG" (Kaynağa Dayalı Getirim Destekli Üretim) mimarisi üzerine kuruludur.

1. **Veri Alma:** Yeni/güncellenmiş ihaleler PostgreSQL'den çekilir.
2. **Vektörizasyon (FAISS - `app/indexing/`)**: İhale metinleri `BAAI/bge-m3` modeli kullanılarak anlam vektörlerine dönüştürülür ve FAISS indeksinde (`storage/faiss/`) saklanır. Şirket profilleri de (`config/isbak/`) benzer şekilde indekslenir.
3. **Aday Eşleştirme (`app/matching/`)**: İhaleler ve profiller uzaysal vektör benzerliğine göre eşleştirilir.
4. **Gerçekleme (Grounding)**: FAISS'ten aday profil bulunduğunda, olası veri eskimelerini önlemek için ihale detayları **canlı PostgreSQL'den tekrar okunur.**
5. **Kompakt Bağlam & Kanıt Seçimi (`app/retrieval/`)**: LLM'e tüm ihale gönderilmek yerine, ihalenin içeriğine göre 1, 3 veya 4 adet en güçlü kanıt (chunk) seçilir. Profil JSON dosyası sıkıştırılarak (Compact Context) token sınırları optimize edilir.
6. **LLM Kararı (`app/decision/`)**: `qwen3.5:4b-q4_K_M` modeli (Ollama üzerinden) bu kanıtları okuyup katılım/uygunluk durumuna dair bir "taslak karar" verir.
7. **Python Doğrulama (`app/decision/SourceGroundedPythonValidator.py`)**: LLM'in kararı katı kurallarla (Negatif ve Pozitif Faaliyet kuralları, OKAS kodları, ihale başlığı uyumu) denetlenir. Eğer model halüsinasyon yapmışsa, karar `inceleme_gerekli` durumuna çekilir.
8. **Raporlama (`app/reporting/`)**: Nihai kararlar CSV ve JSONL formatlarında yöneticilere sunulur.

## 4. Kritik Dosyalar ve Betikler (`scripts/`)

Sistemin çalıştırılmasını ve yönetilmesini sağlayan dosyalar:

* **`scripts/run_tender_decision_chain.py`**: Sistemin ana motorudur. Bütün süreci (Aday Bulma > Grounding > LLM > Python > Rapor) tetikler.
* **`scripts/build_active_tenders_faiss.py`**: Veritabanındaki ihaleleri FAISS indeksine gömer (Sadece yeni veya değişmiş olanları - Incremental).
* **`scripts/build_profiles_faiss.py`**: `config/isbak/` içindeki profil JSON'larını indeksler.
* **`scripts/monitor_system_resources.py`**: CPU ve RAM kullanımını takip eder (Sistem sadece CPU üzerinde, max verimle çalışacak şekilde ayarlanmıştır).
* **`.env`**: Veritabanı ve Ollama bağlantı ayarları burada tutulur.

## 5. Veri Depolama (`storage/`)

Sistem kalıcı vektör depolaması için ağır bir veritabanı (Milvus/Qdrant) yerine lokal dosyaları tercih eder:

* **`storage/faiss/`**: İhale verilerine ait vektör `.index` ve metadata (`.pkl`) dosyaları yer alır.
* **`storage/faiss_profiles/`**: İSBAK departmanlarına ait vektör verileri yer alır.
* **`storage/model_cache/`**: (Varsa) BGE-M3 modeline ait yerel önbelleklemeler.

## Özet

Bu mimari; **hız için FAISS**, **anlamlandırma için LLM (Qwen)** ve **güvenlik/doğruluk için Python kural setlerini (Validator)** kullanan, dışarıya kapalı, güvenli ve "İnsan Onayına Dayalı (Human-in-the-Loop)" bir Karar Destek Sistemidir.
