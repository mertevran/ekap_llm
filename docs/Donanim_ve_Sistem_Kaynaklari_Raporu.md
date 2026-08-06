# İSBAK Yapay Zeka İhale Analiz Sistemi - Donanım ve Kaynak Kullanımı Raporu

**Hazırlayan:** Antigravity
**Tarih:** 6 Ağustos 2026
**Test Kapsamı:** 30 Adet Benzersiz İhalenin Uçtan Uca Yapay Zeka (Qwen 4B) ile Analizi
**Toplam Süre:** 10.237 Saniye (~2.8 Saat)

---

## 1. Genel Performans ve Darboğaz (Bottleneck) Analizi

Sistemin çalışma süresi boyunca oluşturulan donanım metriklerine (`system_resources_summary.json`) göre, mevcut mimarinin en büyük yükü **Sistem Belleğine (RAM)** ve **İşlemciye (CPU)** bindiği tespit edilmiştir. İhale başına analiz süresinin 5.5 - 6 dakika bandında olmasının ana nedeni, işlemlerin Ekran Kartı (GPU) yerine CPU üzerinde gerçekleştirilmesidir.

## 2. Bellek (RAM) Kullanımı: Sistemin Zirve Noktası

Mevcut donanımda bulunan **12 GB RAM**, yapay zeka süreçleri için neredeyse son limitine kadar kullanılmıştır.

*   **Toplam RAM Kullanım Ortalaması:** 9.1 GB (%76)
*   **Maksimum RAM Kullanımı (Zirve Noktası):** 11.9 GB (%99.8) - *Sistem belleği neredeyse tamamen dolmuştur.*
*   **Ollama (LLM Sunucusu) RAM Tüketimi:** Ortalama 7.75 GB. En ağır şartnamelerin okunduğu anlarda modelin bellekte kapladığı alan **10.64 GB**'a kadar tırmanmıştır.
*   **Python (RAG / Vektör) RAM Tüketimi:** Metin parçalama ve FAISS vektör arama işlemleri ortalama **873 MB** RAM kullanmıştır.

*(Not: Qwen modelinin 12.000 karakterlik bir bağlam penceresi `num_ctx=12288` ile çalışması, yüksek RAM tüketiminin temel sebebidir.)*

## 3. İşlemci (CPU) Kullanımı: Ana İş Yükü Merkezi

Büyük dil modelinin (LLM) ekran kartına sığmaması sebebiyle, metin anlama ve üretme (inference) işlemlerinin tümü işlemci (CPU) üzerinden gerçekleşmiştir.

*   **Genel Sistem CPU Yükü Ortalaması:** %51
*   **Ollama Servisi CPU Kullanımı:** Ortalama **%393**. 
    *(Açıklama: Sistemdeki 8 mantıksal çekirdeğin 4'ü (`num_thread=4` konfigürasyonu gereği) aralıksız olarak tam kapasite %100 ile çalıştırılmıştır.)*

## 4. Ekran Kartı (GPU) Kullanımı: Uyku Modu

Yapay zeka modelleri teorik olarak GPU (CUDA vb.) üzerinde binlerce kat daha hızlı çalışır. Ancak loglar, sistemin GPU'yu aktif olarak kullanamadığını göstermektedir.

*   **GPU Kullanım Ortalaması:** Sadece **%1.6** (Zirve: %48)
*   **GPU Bellek (VRAM) Tüketimi:** Ortalama 959 MB (Maksimum 1 GB).
*   **Sıcaklık ve Güç:** Ortalama 41°C sıcaklık ve 3 Watt güç tüketimi.
*   *Çıkarım:* Mevcut ekran kartının belleği (VRAM) modeli ve bağlamı içine alacak kadar geniş olmadığı için, sistem otomatik olarak "CPU Offload" (İşlemciye paslama) yöntemine geçmiş ve GPU gücünden faydalanamamıştır.

## 5. Çıktı Hızı (Tokens Per Second - TPS)

İşlemlerin işlemci (CPU) tabanlı yürütülmesi üretim hızını doğrudan etkilemiştir. 

*   **Girdi Hacmi:** Karar başına modele ortalama **6.000 - 6.700 token** metin (ihale şartnamesi ve kurum profili) gönderilmektedir.
*   **Üretim Hızı:** Sistem saniyede ortalama **2 ila 4 token** arasında metin üretmektedir. (Girdi okuma süreleri dahil).
*   *Örnek Bir İşlem:* Modelin 6.723 tokenlik bir şartnameyi okuyup, 888 tokenlik (yaklaşık 1 sayfa) bir JSON kararı üretmesi **343 saniye** (yaklaşık 5.7 dakika) sürmüştür.

## 6. Özet ve Donanım Yükseltme Tavsiyesi

Mevcut sistem hata yapmadan, kusursuz bir işleyişle süreci tamamlasa da;
Eğer **ihale başı 5.5 dakikalık sürenin 30 saniye - 1 dakika bandına düşürülmesi** ve RAM kaynaklı (Out of Memory) olası çökmelerin engellenmesi hedefleniyorsa:

1.  **VRAM'i yüksek (Örn. 12GB - 16GB arası) bir Nvidia GPU'ya** sahip bir sunucu mimarisine geçilmesi,
2.  Qwen modelinin ve işlemlerin (Ollama config üzerinden) tamamen bu GPU'ya aktarılması,
gerekmektedir. Bu basit donanım terfisiyle sistem hızının minimum 5 ile 10 kat arasında artacağı öngörülmektedir.
