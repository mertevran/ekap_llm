# İSBAK LLM Karar Katmanı ve Doğrulama Mekanizması

Bu belge, `IsbakDecisionPipeline`, `IsbakRuleValidator` ve Ollama destekli Büyük Dil Modeli (LLM) karar katmanının mimari kararlarını ve işleyiş mantığını açıklar.

## 1. Karar İşlem Hattı (Decision Pipeline)

`IsbakDecisionPipeline`, ihalenin sistemdeki yeterliliklerle uyumluluğunu değerlendiren çok aşamalı bir iş hattıdır:

### 1.1. Birincil Karar Aşaması (Qwen)
Sistem, `qwen3:8b` modelini birincil karar alıcı olarak konumlandırır. İhale belgesinden çıkarılan şartnameler ve İSBAK kurumsal şirket profili (eşleşen yetkinlikler) modele verilir. Model; ihaleyi, şartlarını ve şirketin yetkinliklerini kıyaslayarak bir değerlendirme yapar (JSON).

### 1.2. Kural Tabanlı Doğrulama (Python Validator)
LLM (Qwen) yapısal olarak halüsinasyon görebilir veya eksik bilgiyi kendi kendine tamamlayabilir. Bu riski bertaraf etmek adına Qwen'in kararı doğrudan nihai karar olarak kabul edilmez. Karar öncelikle `IsbakRuleValidator` sınıfından geçer:
- **Eksik Kanıt Kontrolü**: Model eğer "doğrudan_uygun" kararı vermişse, ancak şartlarda geçen kriterlerin bazılarının statüsü "belirsiz" ise karar "inceleme_gerekli" olarak düşürülür.
- **İlgisizlik Kontrolü**: Karar "ilgisiz" dahi olsa, "doğrudan_uygun" verilip verilmediğine dair bir çelişki olup olmadığı aranır.
- **Güven Puanı**: Modellerin döndürdüğü güven skoru belirli bir eşiğin altındaysa, otomatik olarak karar "inceleme_gerekli" kategorisine alınır.

### 1.3. İkinci Görüş Aşaması (Gemma - Fallback)
Eğer Python Validator sonrasında karar hala "ilgisiz" olarak kalmışsa, gözden kaçan bir yetkinlik olma ihtimaline karşı `gemma4:e2b-it-q4_K_M` (İkinci Görüş Modeli) çağrılır:
- Gemma "ilgisiz" onayını verirse, nihai karar "ilgisiz" olur.
- Gemma, Qwen ile çelişip "ilgili / inceleme gerekli" kararı verirse, ihale reddedilmez ve anlaşmazlık raporlanarak "inceleme_gerekli" statüsüne çekilir.

## 2. Karar Kategorileri

Sistem yalnızca üç adet katı sonuç döner:
- `doğrudan_uygun`: İhale şartları ile İSBAK yeterlilikleri arasında güçlü ve kanıtlanmış bir eşleşme vardır. Herhangi bir "belirsiz" kriter yoktur.
- `inceleme_gerekli`: Şartnamede geçen bazı yeterlilik belgelerinin, sertifikaların veya iş bitirme şartlarının sistemdeki profilde bulunamaması veya iki LLM arasındaki fikir ayrılığı durumudur. Karar insan onayına sunulur.
- `ilgisiz`: İhalenin kapsamı İSBAK'ın faaliyet ve yetkinlik alanıyla örtüşmemektedir. (İki modelin ortak kararı gereklidir).

## 3. Sistemde Olmayan ve Tasarlanmamış Özellikler
*Gerçek koda ve mimariye sadık kalmak adına, sistemde bulunmayan veya henüz geliştirilmemiş özellikler şunlardır:*
- **Otomatik İhale Başvurusu**: Sistem sadece analiz ve tavsiye yapar, ihaleye başvuru (teklif) modülü yoktur.
- **Otonom Veri Yazma**: Modeller (Qwen, Gemma) ve pipeline işlemleri Qdrant vektör tabanına veya PostgreSQL'e yeni bir kayıt atmaz; **sadece okuma (read-only)** işlemi yapar.
- **Phi Modeli**: `phi4-mini` gibi modeller sistem ayarlarında yer alabilse de, aktif karar (pipeline) iş hattında entegre bir kullanım veya üçüncü bir kontrol rolü bulunmamaktadır.
