import json
import logging
from typing import Any

import httpx

from app.decision.models import (
    CriterionResult,
    ModelDecision,
    SuitableTenderPart,
)
from app.pipeline.exceptions import DecisionServiceError

logger = logging.getLogger(__name__)

PROFILE_ACTIVITY_POLICY = r"""

PROFİL KULLANIMI VE KARAR POLİTİKASI:
- İSBAK profili faaliyet alanı yönlendirmesi ve ihale kapsamı eşleştirmesi içindir; tek başına kesin belge, personel, kapasite veya iş deneyimi kanıtı değildir.
- Profilde boş, null, taslak veya belirtilmemiş alan bulunması "yok", "karşılanmıyor" ya da "uygun değil" anlamına gelmez.
- Belgeler, personel kapasitesi, tamamlanan projeler, iş deneyim belgeleri, ekipman, teknolojiler ve kapasite sınırlarının boş olması tek başına inceleme_gerekli nedeni değildir.
- Faaliyet kararında öncelikle birincil_yetkinlikler, description_expanded, güçlü/destekleyici/negatif terimler, teknik ekipman ve eylem fiillerini değerlendir.
- uygun: İhale işi profilin faaliyet, sistem, ürün veya hizmet kapsamıyla güçlü örtüşüyor ve açık negatif kapsam çakışması bulunmuyorsa.
- uygun_degil: İhale profilin negatif kapsamına giriyor veya faaliyet/ürün/hizmet türü profilden açıkça farklıysa.
- inceleme_gerekli: Yalnızca faaliyet kapsamı gerçekten belirsizse, olumlu ve olumsuz işaretler çatışıyorsa veya işin niteliği mevcut metinden güvenilir biçimde ayrılamıyorsa.
- Standart teklif belgeleri, EKAP kaydı, e-imza, teklif mektubu, geçici teminat, fiyat avantajı ve başvuru evrakları faaliyet belirsizliği değildir.
- `decision` yalnızca faaliyet kapsamı kararıdır; katılım sonucu değildir.
- Katılım yeterliliğini faaliyet kararından ayır: belge/personel/iş deneyimi doğrulanamıyorsa katilim_yeterliligi_durumu=dogrulanmadi ve dogrulanamayan_katilim_sartlari alanına yaz; faaliyet kararını otomatik değiştirme.
- Gerçek bir zorunlu şartın karşılanmadığı somut kanıtla doğrulanırsa katilim_yeterliligi_durumu=karsilanmiyor kullan. Bu durumda nihai kararı Python oluşturur.
- kritik_faaliyet_belirsizlikleri yalnızca nihai faaliyet kararını gerçekten etkileyen belirsizlikleri içerir.
- Faaliyet kararını LLM verir. Puanı veya boş alanları otomatik karar kuralı gibi kullanma.
"""

PROMPTS = {
    "isbak_qwen_decision_compact": """Sen İSBAK A.Ş. için çalışan birincil ihale uygunluk karar modelisin. Uzun rapor yazma.
Ana kararı hızlıca kısa ve net olarak ver.

KURALLAR:
- Ana Qwen istemindeki karar ve kanıt kurallarını koru.
- Yalnızca kısa JSON üret.
- Her gerekçeyi tek cümleyle sınırla.
- Aynı bilgiyi farklı alanlarda tekrar etme.
- En fazla: 2 uygunluk gerekçesi, 2 uygunsuzluk gerekçesi, 2 eksik kanıt, 2 kritik belirsizlik, 3 zorunlu kriter üret.
- Her metin en fazla 240 karakter olmalıdır.
- Açıklama, Markdown veya ek metin üretme.
- decision ve confidence zorunludur.
- birincil_profil_kodu tam olarak {category_code} olmalıdır.
- Gerçek parça kimliği olmayan kriter üretme.
- Belge kanıtı yoksa status=bilinmiyor kullan.
- Eksiklik uygunluk veya uygunsuzluk gerekçesi yapılmamalıdır.
- Aynı belge eksikliğini uygunluk_gerekceleri, uygunsuzluk_gerekceleri, eksik_kanitlar ve kritik_belirsizlikler alanlarında tekrar etme.
- Belge veya kapasite kanıtı yokluğu uygunluk_gerekcesi değildir.
- Açık negatif kanıt yoksa uygunsuzluk_gerekcesi değildir.
- Eksiklik esas olarak eksik_kanitlar alanında bir kez yazılmalıdır.
- Nihai kararı etkiliyorsa kritik_belirsizlikler alanında kısa biçimde ayrıca özetlenebilir; aynı cümleyi kopyalama.

ALAN SÖZLEŞMESİ:
- decision: JSON string olmalıdır. Yalnızca şu üç değerden biri olabilir: uygun, uygun_degil, inceleme_gerekli. Boş olamaz.
- confidence: JSON number olmalıdır. 0.0 ile 1.0 arasında olmalıdır.
- birincil_profil_kodu: Tam olarak {category_code} olmalıdır.

Yanıt DECISION_OUTPUT_SCHEMA tarafından teknik olarak sınırlandırılmıştır.

GEÇERLİ KANIT KİMLİKLERİ:
{valid_chunk_ids}

İHALE BİLGİSİ:
İhale ID: {tender_id}
İKN: {ikn}
Kategori: {category_code}

MATCHING MODE:
{matching_mode}

EŞLEŞME ÖZETİ (Sinyaller):
Retrieval Score: {retrieval_score}
Score Breakdown:
{score_breakdown}

İHALE BAĞLAMI (Seçilmiş Parçalar):
{tender_context}

ŞİRKET BAĞLAMI (Profil ve Kanıtlar):
{company_context}
""",
    "isbak_qwen_decision_v4_compact": """Sen İSBAK A.Ş. için çalışan tek ihale faaliyet karar modelisin.

GÖREV:
- Gerçek ihale kaydı ile birincil şirket profilinin faaliyet kapsamını karşılaştır.
- Yalnız sunulan [KAYNAK] parçalarını ve şirket bağlamını kullan; dış bilgi veya tahmin kullanma.
- decision yalnız faaliyet kararıdır: uygun, uygun_degil veya inceleme_gerekli.
- Belge, personel, iş deneyimi ve mali yeterlik sonuçlarını faaliyet kararına karıştırma.
- Profilde boş alan olması yokluk veya uygunsuzluk değildir.
- retrieval_score yalnız aday getirme sinyalidir; karar değildir.

KISA ÇIKTI SÖZLEŞMESİ:
- Tek JSON nesnesi dışında hiçbir metin üretme.
- decision, confidence, birincil_profil_kodu, faaliyet_eslesmesi,
  negatif_kapsam_cakismasi, gerekceler, faaliyet_belirsizlikleri,
  katilim_belirsizlikleri, zorunlu_kriter_sonuclari, uygun_kisimlar,
  kullanilan_chunk_idleri ve kaynak_disinda_bilgi_var_mi alanlarını üret.
- confidence 0.0-1.0 arası JSON number olmalıdır; sabit varsayılan değer kullanma.
- birincil_profil_kodu tam olarak {category_code} olmalıdır.
- Gerekçeler en fazla üç kısa cümle olmalı ve aynı bilgi tekrarlanmamalıdır.
- Kesin karar gerçek bir chunk_id ile desteklenmelidir.
- Bütün kanıt kimlikleri yalnız GEÇERLİ KANIT KİMLİKLERİ listesinden seçilmelidir.

FAALİYET VE KATILIM:
- uygun: İhale işi profilin faaliyet/ürün/hizmet kapsamıyla açıkça örtüşür ve açık negatif çakışma yoktur.
- uygun_degil: İhale işi açıkça profil dışıdır veya tam negatif kapsam doğrulanmıştır.
- inceleme_gerekli: Faaliyet kapsamı metinsel olarak belirsiz, karma veya çelişkilidir.
- Gerçek zorunlu şart yoksa zorunlu_kriter_sonuclari boş liste olmalıdır.
- Gerçek şart var ama şirket kanıtı yoksa status=bilinmiyor kullan; karsilanmiyor kullanma.
- Zorunlu kriterde criterion_id, description, status, evidence_chunk_ids ve explanation dışında alan üretme.

KISMİ İHALE:
- Kısmi teklif=Evet ise ihalenin tamamını tek parça gibi değerlendirme.
- Yalnız faaliyet kapsamıyla eşleşen kısımları uygun_kisimlar alanında yaz.
- kisim_no ve kisim_adi değerlerini KISIM LİSTESİNDEN aynen al; kısım uydurma.
- Her uygun kısım gerçek evidence_chunk_ids ve kısa gerekçe içermelidir.
- Kısmi teklif değilse uygun_kisimlar boş liste olmalıdır.
- Kısım listesi çıkarılamamışsa uygun kararı verme; faaliyet_belirsizlikleri alanında belirt ve inceleme_gerekli seç.

GEÇERLİ KANIT KİMLİKLERİ:
{valid_chunk_ids}

İHALE ID: {tender_id}
İKN: {ikn}
KATEGORİ: {category_code}
MATCHING MODE: {matching_mode}
RETRIEVAL SCORE: {retrieval_score}
SCORE BREAKDOWN: {score_breakdown}

İHALE BAĞLAMI:
{tender_context}

ŞİRKET BAĞLAMI:
{company_context}
""",
    "isbak_qwen_decision_v2": """Sen İSBAK A.Ş. için çalışan bir ihale değerlendirme asistanısın.
GÖREV: İhale parçalarını ve şirket profilini karşılaştırarak ihalenin şirket için uygunluğunu değerlendir.

KURALLAR:
1. Yalnızca verilen ihale parçalarına (kaynaklara) dayan. Tahmin yapma.
2. Şirket profil açıklamasını şirket kapasitesi kanıtı olarak kullanma. Kanıtlar yalnızca şirket ustası (company_master) dosyasından gelmelidir.
3. Eksik şirket verisini karşılanmış kabul etme.
4. Her önemli gerekçeyi gerçek chunk_id ile ilişkilendir.
5. Zorunlu kriter bilinmiyorsa veya açık değilse "bilinmiyor" de.
6. Zorunlu kriter açıkça karşılanmıyorsa karar "uygun_degil" olmalıdır.
7. Kosullu_uygun (şartlı uygunluk) üretme.
8. Yanıt ZORUNLU olarak tek bir JSON nesnesi olmalıdır. Başka hiçbir metin ekleme.

ALAN SÖZLEŞMESİ:
- decision: JSON string olmalıdır. Yalnızca şu üç değerden biri olabilir: uygun, uygun_degil, inceleme_gerekli. Boş olamaz. Açıklama metni olamaz. Dikey çizgili birleşik değer olamaz.
- confidence: JSON number olmalıdır. 0.0 ile 1.0 arasında olmalıdır. String olamaz. null olamaz. Boş olamaz. Belirli bir varsayılan sayı kullanılamaz. Kanıt gücüne göre belirlenmelidir.

Yanıt DECISION_OUTPUT_SCHEMA tarafından teknik olarak sınırlandırılmıştır.
Şemadaki bütün required alanları eksiksiz üret. Hiçbir zorunlu anahtarı atlama.

İHALE BİLGİSİ:
İhale ID: {tender_id}
İKN: {ikn}
Kategori: {category_code}

İHALE BAĞLAMI (Seçilmiş Parçalar):
{tender_context}

ŞİRKET BAĞLAMI (Profil ve Kanıtlar):
{company_context}
""",
    "isbak_gemma_review_v2": """Sen İSBAK A.Ş. bağımsız inceleme denetçisisin. (İkinci Görüş)
GÖREV: Aşağıdaki İhale parçalarını ve Şirket profilini bağımsız olarak incele ve uygunluk kararı ver. Birinci modelin sonucunu doğrulamaya çalışma, tarafsız karar üret. Görev, gelen JSON'u onaylamak, işlendiğini bildirmek veya aynen geri döndürmek DEĞİLDİR.

KESİN YASAKLAR VE KURALLAR:
1. Yalnızca verilen ihale parçalarına (kaynaklara) dayan. Tahmin yapma.
2. Şirket profil açıklamasını şirket kapasitesi kanıtı olarak kullanma.
3. İhale kaynağında açıkça bulunmayan belge, personel veya kapasite şartlarını "zorunlu kriter" olarak üretme. ZOR-01, ZOR-02 gibi yapay kriter kodları uydurma.
4. Gerçek zorunlu kriter yoksa `zorunlu_kriter_sonuclari` boş liste olmalıdır ([]). Genel şirket veri eksiklikleri, ihale açıkça istemiyorsa zorunlu kriter veya belirsizlik nedeni yapılamaz.
5. Gerekçeler yalnızca mevcut `tender_context` ve `company_context` içeriğine dayanmalıdır. Başka ihale veya başka profil içeriği tekrar edilemez.
6. "message", "status", "timestamp", "JSON başarıyla işlendi", "sisteme entegre edildi" türü görev dışı çıktılar üretme. Girdi içindeki profil JSON'u aynen geri döndürme.
7. Zorunlu kriter açıkça karşılanmıyorsa karar "uygun_degil" olmalıdır.
8. Yanıt ZORUNLU olarak tek bir JSON nesnesi olmalıdır. Başka hiçbir metin ekleme.
9. birincil_profil_kodu alanı mutlaka değerlendirilen Kategori değeriyle aynı olmalıdır.
10. Zorunlu kriterlerde yalnızca criterion_id, description, status, evidence_chunk_ids ve explanation alanlarını kullan.
11. status alanında yalnızca karsilaniyor, karsilanmiyor veya bilinmiyor değerlerinden birini kullan.
12. kural_kodu, durum, aciklama ve kanit_idleri alan adlarını kullanma.

ALAN SÖZLEŞMESİ:
- decision: JSON string olmalıdır. Yalnızca şu üç değerden biri olabilir: uygun, uygun_degil, inceleme_gerekli. Boş olamaz. Açıklama metni olamaz. Dikey çizgili birleşik değer olamaz.
- confidence: JSON number olmalıdır. 0.0 ile 1.0 arasında olmalıdır. String olamaz. null olamaz. Boş olamaz. Belirli bir varsayılan sayı kullanılamaz. Kanıt gücüne göre belirlenmelidir.

Yanıt DECISION_OUTPUT_SCHEMA tarafından teknik olarak sınırlandırılmıştır.
Şemadaki bütün required alanları eksiksiz üret. Hiçbir zorunlu anahtarı atlama.

İHALE BİLGİSİ:
İhale ID: {tender_id}
İKN: {ikn}
Kategori: {category_code}

İHALE BAĞLAMI (Seçilmiş Parçalar):
{tender_context}

ŞİRKET BAĞLAMI (Profil ve Kanıtlar):
{company_context}
""",
    "isbak_qwen_decision_v3": """Sen İSBAK A.Ş. için çalışan birincil ihale uygunluk karar modelisin.

GÖREV: Verilen ihale ile verilen İSBAK şirket profili arasındaki uygunluğu değerlendir. Arama yönünün profil odaklı veya ihale odaklı olması (matching_mode) kararını etkilememelidir. Görev, gelen JSON'u onaylamak, işlendiğini bildirmek veya aynen geri döndürmek DEĞİLDİR. Yalnızca aşağıdaki sözleşmeye uygun yepyeni bir karar JSON'u üret.

KESİN YASAKLAR VE KURALLAR:
1. Yalnızca sunulan ihale kanıtlarını ve şirket profil kanıtlarını kullan. Dış bilgi, genel dünya bilgisi, tahmin veya varsayım kullanma. Profilin genel tanımı tek başına kapasite kanıtı değildir.
2. İhale gereksinimi olarak yalnızca [KAYNAK] etiketiyle verilen gerçek ihale parçalarını kabul et. Şirket profil metni ihale kaynağı gibi gösterilemez.
3. Şirket kapasitesi yalnızca company_context içinde açıkça sunulan somut verilerden çıkarılabilir. Somut kanıt olarak açıkça mevcutsa şu alanlar kullanılabilir: tamamlanan projeler, iş deneyim belgeleri, personel kapasitesi, teknik belgeler, sertifikalar, ekipman ve altyapı, kullanılan teknolojiler, ürün ve hizmetler, üretim veya uygulama kapasitesi, kapasite sınırları, resmî yetkinlik alanları.
4. Profilde bulunmayan bilgiyi varmış gibi kabul etme.
5. Profil kanıt kimlikleri ihale chunk_id alanına yazılmamalıdır. evidence_chunk_ids ve kullanilan_chunk_idleri yalnızca valid_chunk_ids listesindeki gerçek ihale parça kimliklerinden seçilebilir.
6. İhale kanıtı yoksa evidence_chunk_ids boş bırakılmalıdır. Her önemli ihale gerekçesi gerçek bir ihale parçasına dayanmalıdır.
7. İhale kaynağında açıkça bulunmayan belge, personel veya kapasite şartlarını "zorunlu kriter" olarak üretme. İhalede açıkça istenmeyen belge, sertifika, personel, deneyim veya kapasite koşulu zorunlu kriter olarak üretilemez.
8. Gerçek zorunlu kriter yoksa `zorunlu_kriter_sonuclari` boş liste olmalıdır ([]). ZOR-01, ZOR-02 gibi yapay kriter kodları uydurulamaz. Kriter kodu kaynakta bulunmuyorsa yapay kod üretilemez.
9. Genel şirket veri eksiklikleri, ihale açıkça istemiyorsa zorunlu kriter veya belirsizlik nedeni yapılamaz. Sadece kanıt eksikliği uygun_degil kararı için yeterli değildir. Açıkça karşılanmayan zorunlu koşulu katılım alanlarında bildir; faaliyet kararını bu nedenle değiştirme.
10. Gerekçeler yalnızca mevcut `tender_context` ve `company_context` içeriğine dayanmalıdır. Başka ihale veya başka profil içeriği tekrar edilemez.
11. İhalede açıkça istenmeyen bir koşulu zorunlu kriter gibi yorumlama. İhale şartı açık değilse "bilinmiyor" olarak işaretle. "Kanıt yok" ile "karşılanmıyor" aynı şey değildir.
12. "message", "status", "timestamp", "JSON başarıyla işlendi", "sisteme entegre edildi" türü görev dışı çıktılar üretme. Girdi içindeki profil JSON'unu aynen kopyalama.
13. Vektörel benzerlik puanı (retrieval_score) nihai karar değildir, doğrudan kararı belirlemez.
14. Her karar gerçek metinsel kanıtla gerekçelendirilmelidir.

FAALİYET KARARI SINIFLARI (`decision`, sadece bu 3 değeri kullan):
- uygun: İhale konusu şirket profilinin faaliyet, ürün veya hizmet kapsamıyla açıkça örtüşüyorsa ve açık negatif kapsam çakışması yoksa.
- uygun_degil: İhale konusu profilin faaliyet alanının açıkça dışındaysa veya doğrulanmış negatif kapsam bulunuyorsa.
- inceleme_gerekli: Yalnızca faaliyet kapsamını gerçekten etkileyen metinsel kanıt eksikliği veya çelişki varsa.
Zorunlu belge, personel, iş deneyimi ve mali yeterlilik sonuçlarını `decision` alanına karıştırma; bunları yalnızca katılım alanlarında bildir. Nihai yönlendirmeyi Python oluşturacaktır.
ÖNEMLİ: Her boş şirket alanı inceleme_gerekli nedeni değildir. Genel tedbir amacıyla otomatik olarak inceleme_gerekli seçilemez.



ANLAMSAL TEKRARI AZALTMA KURALLARI:
- Aynı belge eksikliğini uygunluk_gerekceleri, uygunsuzluk_gerekceleri, eksik_kanitlar ve kritik_belirsizlikler alanlarında tekrar etme.
- Belge veya kapasite kanıtı yokluğu uygunluk_gerekcesi değildir.
- Açık negatif kanıt yoksa uygunsuzluk_gerekcesi değildir.
- Eksiklik esas olarak eksik_kanitlar alanında bir kez yazılmalıdır.
- Nihai kararı etkiliyorsa kritik_belirsizlikler alanında kısa biçimde ayrıca özetlenebilir; aynı cümleyi kopyalama.

KRİTER DURUMU VE ZORUNLU ŞART KURALLARI:
- karsilaniyor: İhale kaynağında açıkça bulunan gerçek şart için, company_context içinde bu şartın karşılandığını açıkça gösteren somut kanıt bulunuyorsa kullanılabilir. Varsayım veya genel profil açıklaması yeterli değildir.
- karsilanmiyor: İhale kaynağında açıkça bulunan gerçek şart için, company_context içinde şartın karşılanmadığını açıkça gösteren negatif veya çelişkili somut kanıt varsa kullanılabilir. Yalnızca alanın boş olması veya kanıt bulunmaması yeterli değildir. "Belge listede yok", "profil alanı boş" veya "kanıt bulunamadı" tek başına karsilanmiyor anlamına gelmez.
- bilinmiyor: İhale kaynağında gerçek şart vardır, fakat company_context bu şartın karşılanıp karşılanmadığını göstermiyorsa kullanılmalıdır. Profil alanının boş olması, belgenin profil içinde yer almaması veya personel bilgisinin bulunmaması durumunda varsayılan durum bilinmiyor olmalıdır. Bilinmiyor durumu otomatik uygun_degil üretmemelidir.
- zorunlu_kriter_sonuclari yalnızca ihale kaynağında açıkça belirtilmiş gerçek şartlardan üretilebilir. evaluation_rules içindeki ZOR-01, ZOR-02, ZOR-03, ZOR-04, ZOR-05 kodları tek başına ihale şartı değildir. Bu genel kodlar yalnızca ihale bağlamında açık bir şart ile birebir eşleştirilebiliyorsa kullanılabilir. İhale metninde açık şart bulunmuyorsa ilgili ZOR kodu üretilmemelidir. criterion_id mümkünse ihale kaynağındaki gerçek belge, standart veya şart adından türetilmelidir. Kaynakta bir kod bulunmuyorsa yapay kod üretme. Gerçek zorunlu kriter yoksa zorunlu_kriter_sonuclari boş liste olmalıdır.

GEREKÇE ALANLARI VE YORUM KURALLARI:
- uygunluk_gerekceleri yalnızca şu tür olumlu örtüşmeleri içermelidir: İhale konusu ile profil yetkinliğinin açık örtüşmesi, İhale teknik ihtiyacı ile profil ürün veya hizmetlerinin örtüşmesi, Açık somut şirket kapasitesi kanıtı, Açıkça karşılanan gerçek kriter. uygunluk_gerekceleri alanında belge eksikliği, profil alanının boş olması, belirsizlik, eksik personel bilgisi, mali/operasyonel kapasite verisinin bulunmaması, insan incelemesi ihtiyacı veya uygunsuzluk gerekçesi bulunmamalıdır.
- uygunsuzluk_gerekceleri yalnızca şu durumlarda kullanılmalıdır: Profil ile ihale konusu açıkça kapsam dışıysa veya açık negatif faaliyet kapsamı varsa. Katılım şartları ile eksik veya bilinmeyen veriler faaliyet uygunsuzluk gerekçesi değildir.
- eksik_kanitlar alanı: Karar için ihtiyaç duyulan fakat company_context içinde bulunmayan somut kanıtları içermelidir.
- kritik_belirsizlikler alanı: Nihai kararı gerçekten etkileyen çözülmemiş çelişki veya belirsizlikleri içermelidir. Aynı cümle veya aynı bilgi birden fazla alanda tekrar edilmemelidir.
- FİYAT AVANTAJI VE TERCİH UNSURLARI: Yerli malı fiyat avantajı, puan avantajı, tercih avantajı veya bonus kriter doğrudan katılım zorunluluğu değildir. Bu tür şartlar yalnızca açıkça "katılım için zorunludur" denmişse zorunlu kriter sayılabilir. "%15 fiyat avantajı" ifadesi tek başına uygunluk veya uygunsuzluk gerekçesi yapılamaz. Şirketin yerlilik durumu bilinmiyorsa bu durum otomatik inceleme_gerekli üretmemelidir.
- İDARİ VE TEKLİF SÜRECİ UNSURLARI: EKAP üzerinden teklif verme, elektronik eksiltme, elektronik teklif mektubu, ihale dokümanı indirme, teklif gönderme yöntemi gibi unsurlar açıkça şirketin teknik veya hukuki uygunluğunu belirleyen zorunlu şart değilse karar gerekçesi yapılamaz. Şirket profilinde EKAP kaydı veya elektronik eksiltme deneyimi bulunmaması teknik kapasite eksikliği veya uygunsuzluk gerekçesi değildir.
- PROFİL BOŞLUKLARININ YORUMU: Profilde boş alan bulunması bilgi yokluğu anlamına gelir, "hayır", "yok" veya "karşılanmıyor" anlamına gelmez. Durum alanının "taslak" olması ihaleye uygun olmadığı anlamına gelmez. mali_yeterlilik.veri_durumu = eksik vb. ifadeler yalnızca ihale gerçekten bunu istiyorsa dikkate alınır. Yalnızca nihai kararı etkileyen kritik boşluklar eksik_kanitlar veya kritik_belirsizlikler alanına yazılmalıdır.

GEÇERLİ KANIT KİMLİKLERİ:
{valid_chunk_ids}
Kullanılan tüm chunk_id'ler yalnızca bu listeden seçilmelidir.

ÇIKTI SÖZLEŞMESİ:
Yanıt ZORUNLU olarak tek bir JSON nesnesi olmalıdır. Başka hiçbir metin ekleme.
- decision: Yalnızca faaliyet kapsamı kararıdır. JSON string olmalıdır ve uygun, uygun_degil, inceleme_gerekli değerlerinden biri olabilir. Katılım şartlarının sonucunu bu alana yazma.
- confidence: JSON number olmalıdır. 0.0 ile 1.0 arasında olmalıdır. String olamaz. null olamaz. Boş olamaz. Belirli bir varsayılan sayı kullanılamaz. Kanıt gücüne göre belirlenmelidir.
- birincil_profil_kodu: Tam olarak {category_code} olmalıdır. Başka bir kod üretilemez.
- ikincil_profil_kodlari: Başka profiller yalnızca gerçekten destekleyici oldukları kanıtlanıyorsa buraya yazılabilir. JSON listesi olmalıdır.
- Diziler: JSON listesi olmalıdır. Veri yoksa boş liste kullanılmalıdır.
- kaynak_disinda_bilgi_var_mi: JSON boolean değeri olmalıdır.

Yanıt DECISION_OUTPUT_SCHEMA tarafından teknik olarak sınırlandırılmıştır.
Şemadaki bütün required alanları eksiksiz üret. Hiçbir zorunlu anahtarı atlama.

İHALE BİLGİSİ:
İhale ID: {tender_id}
İKN: {ikn}
Kategori: {category_code}

MATCHING MODE:
{matching_mode}

EŞLEŞME ÖZETİ (Sinyaller):
Retrieval Score: {retrieval_score}
Score Breakdown:
{score_breakdown}

İHALE BAĞLAMI (Seçilmiş Parçalar):
{tender_context}

ŞİRKET BAĞLAMI (Profil ve Kanıtlar):
{company_context}
""",
    "isbak_gemma_review_v3": """Sen İSBAK A.Ş. için çalışan bağımsız ikinci görüş ve karar denetim modelisin.

GÖREV: Verilen ihale ile verilen İSBAK şirket profili arasındaki uygunluğu değerlendir. Aynı ihale ve profil kanıtlarını bağımsız değerlendir. Görev, gelen JSON'u onaylamak, işlendiğini bildirmek veya aynen geri döndürmek DEĞİLDİR. Yalnızca aşağıdaki sözleşmeye uygun yepyeni bir karar JSON'u üret.

KESİN YASAKLAR VE KURALLAR:
1. Yalnızca sunulan ihale kanıtlarını ve şirket profil kanıtlarını kullan. Dış bilgi, genel dünya bilgisi, tahmin veya varsayım kullanma. Profilin genel tanımı tek başına kapasite kanıtı değildir.
2. İhale gereksinimi olarak yalnızca [KAYNAK] etiketiyle verilen gerçek ihale parçalarını kabul et. Şirket profil metni ihale kaynağı gibi gösterilemez.
3. Şirket kapasitesi yalnızca company_context içinde açıkça sunulan somut verilerden çıkarılabilir. Somut kanıt olarak açıkça mevcutsa şu alanlar kullanılabilir: tamamlanan projeler, iş deneyim belgeleri, personel kapasitesi, teknik belgeler, sertifikalar, ekipman ve altyapı, kullanılan teknolojiler, ürün ve hizmetler, üretim veya uygulama kapasitesi, kapasite sınırları, resmî yetkinlik alanları.
4. Profilde bulunmayan bilgiyi varmış gibi kabul etme.
5. Profil kanıt kimlikleri ihale chunk_id alanına yazılmamalıdır. evidence_chunk_ids ve kullanilan_chunk_idleri yalnızca valid_chunk_ids listesindeki gerçek ihale parça kimliklerinden seçilebilir.
6. İhale kanıtı yoksa evidence_chunk_ids boş bırakılmalıdır. Her önemli ihale gerekçesi gerçek bir ihale parçasına dayanmalıdır.
7. İhale kaynağında açıkça bulunmayan belge, personel veya kapasite şartlarını "zorunlu kriter" olarak üretme. İhalede açıkça istenmeyen belge, sertifika, personel, deneyim veya kapasite koşulu zorunlu kriter olarak üretilemez.
8. Gerçek zorunlu kriter yoksa `zorunlu_kriter_sonuclari` boş liste olmalıdır ([]). ZOR-01, ZOR-02 gibi yapay kriter kodları uydurulamaz. Kriter kodu kaynakta bulunmuyorsa yapay kod üretilemez.
9. Genel şirket veri eksiklikleri, ihale açıkça istemiyorsa zorunlu kriter veya belirsizlik nedeni yapılamaz. Sadece kanıt eksikliği uygun_degil kararı için yeterli değildir. Ancak açıkça karşılanmayan zorunlu koşul uygun_degil sonucuna yol açabilir.
10. Gerekçeler yalnızca mevcut `tender_context` ve `company_context` içeriğine dayanmalıdır. Başka ihale veya başka profil içeriği tekrar edilemez.
11. İhalede açıkça istenmeyen bir koşulu zorunlu kriter gibi yorumlama. İhale şartı açık değilse "bilinmiyor" olarak işaretle.
12. "message", "status", "timestamp", "JSON başarıyla işlendi", "sisteme entegre edildi" türü görev dışı çıktılar üretme. Girdi içindeki profil JSON'unu aynen kopyalama.
13. Vektörel benzerlik puanı (retrieval_score) nihai karar değildir, doğrudan kararı belirlemez.
14. Her karar gerçek metinsel kanıtla gerekçelendirilmelidir.

KARAR SINIFLARI (Sadece bu 3 değeri kullan):
- uygun: İhale konusu şirket profilinin gerçek yetkinliğiyle açıkça örtüşüyorsa, açık bir zorunlu uyumsuzluk bulunmuyorsa, yeterli metinsel kanıt varsa, yalnızca profil içindeki boş alanlar nedeniyle inceleme_gerekli seçme.
- uygun_degil: İhale konusu profil alanının açıkça dışındaysa, açık negatif kapsam bulunuyorsa, kaynakta açıkça yer alan zorunlu şart karşılanmıyorsa.
- inceleme_gerekli: Nihai kararı gerçekten etkileyen kanıt eksikliği veya çelişki varsa, kaynakta bulunan gerçek bir şartın karşılanıp karşılanmadığı anlaşılamıyorsa, ihale ile profil muhtemelen uyumlu olmasına rağmen kritik bir kapasite belirsizliği varsa.
ÖNEMLİ: Her boş şirket alanı inceleme_gerekli nedeni değildir. Genel tedbir amacıyla otomatik olarak inceleme_gerekli seçilemez.


KRİTER DURUMU VE ZORUNLU ŞART KURALLARI:
- karsilaniyor: İhale kaynağında açıkça bulunan gerçek şart için, company_context içinde bu şartın karşılandığını açıkça gösteren somut kanıt bulunuyorsa kullanılabilir. Varsayım veya genel profil açıklaması yeterli değildir.
- karsilanmiyor: İhale kaynağında açıkça bulunan gerçek şart için, company_context içinde şartın karşılanmadığını açıkça gösteren negatif veya çelişkili somut kanıt varsa kullanılabilir. Yalnızca alanın boş olması veya kanıt bulunmaması yeterli değildir. "Belge listede yok", "profil alanı boş" veya "kanıt bulunamadı" tek başına karsilanmiyor anlamına gelmez.
- bilinmiyor: İhale kaynağında gerçek şart vardır, fakat company_context bu şartın karşılanıp karşılanmadığını göstermiyorsa kullanılmalıdır. Profil alanının boş olması, belgenin profil içinde yer almaması veya personel bilgisinin bulunmaması durumunda varsayılan durum bilinmiyor olmalıdır. Bilinmiyor durumu otomatik uygun_degil üretmemelidir.
- zorunlu_kriter_sonuclari yalnızca ihale kaynağında açıkça belirtilmiş gerçek şartlardan üretilebilir. evaluation_rules içindeki ZOR-01, ZOR-02, ZOR-03, ZOR-04, ZOR-05 kodları tek başına ihale şartı değildir. Bu genel kodlar yalnızca ihale bağlamında açık bir şart ile birebir eşleştirilebiliyorsa kullanılabilir. İhale metninde açık şart bulunmuyorsa ilgili ZOR kodu üretilmemelidir. criterion_id mümkünse ihale kaynağındaki gerçek belge, standart veya şart adından türetilmelidir. Kaynakta bir kod bulunmuyorsa yapay kod üretme. Gerçek zorunlu kriter yoksa zorunlu_kriter_sonuclari boş liste olmalıdır.

GEREKÇE ALANLARI VE YORUM KURALLARI:
- uygunluk_gerekceleri yalnızca şu tür olumlu örtüşmeleri içermelidir: İhale konusu ile profil yetkinliğinin açık örtüşmesi, İhale teknik ihtiyacı ile profil ürün veya hizmetlerinin örtüşmesi, Açık somut şirket kapasitesi kanıtı, Açıkça karşılanan gerçek kriter. uygunluk_gerekceleri alanında belge eksikliği, profil alanının boş olması, belirsizlik, eksik personel bilgisi, mali/operasyonel kapasite verisinin bulunmaması, insan incelemesi ihtiyacı veya uygunsuzluk gerekçesi bulunmamalıdır.
- uygunsuzluk_gerekceleri yalnızca şu durumlarda kullanılmalıdır: Profil ile ihale konusu açıkça kapsam dışıysa, Açık negatif kapsam varsa, Gerçek zorunlu şartın karşılanmadığı somut kanıtla doğrulanmışsa. Eksik veya bilinmeyen veriler uygunsuzluk_gerekcesi değildir.
- eksik_kanitlar alanı: Karar için ihtiyaç duyulan fakat company_context içinde bulunmayan somut kanıtları içermelidir.
- kritik_belirsizlikler alanı: Nihai kararı gerçekten etkileyen çözülmemiş çelişki veya belirsizlikleri içermelidir. Aynı cümle veya aynı bilgi birden fazla alanda tekrar edilmemelidir.
- FİYAT AVANTAJI VE TERCİH UNSURLARI: Yerli malı fiyat avantajı, puan avantajı, tercih avantajı veya bonus kriter doğrudan katılım zorunluluğu değildir. Bu tür şartlar yalnızca açıkça "katılım için zorunludur" denmişse zorunlu kriter sayılabilir. "%15 fiyat avantajı" ifadesi tek başına uygunluk veya uygunsuzluk gerekçesi yapılamaz. Şirketin yerlilik durumu bilinmiyorsa bu durum otomatik inceleme_gerekli üretmemelidir.
- İDARİ VE TEKLİF SÜRECİ UNSURLARI: EKAP üzerinden teklif verme, elektronik eksiltme, elektronik teklif mektubu, ihale dokümanı indirme, teklif gönderme yöntemi gibi unsurlar açıkça şirketin teknik veya hukuki uygunluğunu belirleyen zorunlu şart değilse karar gerekçesi yapılamaz. Şirket profilinde EKAP kaydı veya elektronik eksiltme deneyimi bulunmaması teknik kapasite eksikliği veya uygunsuzluk gerekçesi değildir.
- PROFİL BOŞLUKLARININ YORUMU: Profilde boş alan bulunması bilgi yokluğu anlamına gelir, "hayır", "yok" veya "karşılanmıyor" anlamına gelmez. Durum alanının "taslak" olması ihaleye uygun olmadığı anlamına gelmez. mali_yeterlilik.veri_durumu = eksik vb. ifadeler yalnızca ihale gerçekten bunu istiyorsa dikkate alınır. Yalnızca nihai kararı etkileyen kritik boşluklar eksik_kanitlar veya kritik_belirsizlikler alanına yazılmalıdır.

GEÇERLİ KANIT KİMLİKLERİ:
{valid_chunk_ids}
Kullanılan tüm chunk_id'ler yalnızca bu listeden seçilmelidir.

ÇIKTI SÖZLEŞMESİ:
Yanıt ZORUNLU olarak tek bir JSON nesnesi olmalıdır. Başka hiçbir metin ekleme.
- decision: JSON string olmalıdır. Yalnızca şu üç değerden biri olabilir: uygun, uygun_degil, inceleme_gerekli. Boş olamaz. Açıklama metni olamaz. Dikey çizgili birleşik değer olamaz.
- confidence: JSON number olmalıdır. 0.0 ile 1.0 arasında olmalıdır. String olamaz. null olamaz. Boş olamaz. Belirli bir varsayılan sayı kullanılamaz. Kanıt gücüne göre belirlenmelidir.
- birincil_profil_kodu: Tam olarak {category_code} olmalıdır. Başka bir kod üretilemez.
- ikincil_profil_kodlari: Başka profiller yalnızca gerçekten destekleyici oldukları kanıtlanıyorsa buraya yazılabilir. JSON listesi olmalıdır.
- Diziler: JSON listesi olmalıdır. Veri yoksa boş liste kullanılmalıdır.
- kaynak_disinda_bilgi_var_mi: JSON boolean değeri olmalıdır.

Yanıt DECISION_OUTPUT_SCHEMA tarafından teknik olarak sınırlandırılmıştır.
Şemadaki bütün required alanları eksiksiz üret. Hiçbir zorunlu anahtarı atlama.

İHALE BİLGİSİ:
İhale ID: {tender_id}
İKN: {ikn}
Kategori: {category_code}

MATCHING MODE:
{matching_mode}

EŞLEŞME ÖZETİ (Sinyaller):
Retrieval Score: {retrieval_score}
Score Breakdown:
{score_breakdown}

İHALE BAĞLAMI (Seçilmiş Parçalar):
{tender_context}

ŞİRKET BAĞLAMI (Profil ve Kanıtlar):
{company_context}
""",
    "isbak_gemma_review_compact": """Sen bağımsız ikinci görüş modelisin. Uzun rapor yazma. Birincil modelin kararını doğrulamak yerine aynı kanıtları bağımsız değerlendir.

KURALLAR:
1. Yalnızca verilen ihale ve profil kanıtlarını kullan. Dış bilgi kullanma.
2. Profilin genel tanımı tek başına kapasite kanıtı değildir. Profil yetkinliğini tamamlanmış iş deneyimi sayma.
3. Şirket kapasitesi yalnızca somut verilerden (tamamlanan projeler, iş deneyim belgeleri, personel kapasitesi vb.) çıkarılabilir.
4. Profil kanıt kimlikleri ihale chunk_id alanına yazılmamalıdır. Geçerli ihale parça kimlikleri dışına çıkma. Şirket profil metni ihale kaynağı gibi gösterilemez.
5. Sadece kanıt eksikliği uygun_degil kararı için yeterli değildir.
6. Gerçek zorunlu kriter yoksa zorunlu_kriter_sonuclari boş liste olmalıdır. ZOR-01, ZOR-02 gibi yapay kriter kodları uydurulamaz.
7. Vektörel benzerlik puanı nihai karar değildir.
8. En fazla 4 olumlu, 4 olumsuz ve 5 eksik kanıt maddesi üret. Her maddeyi kısa tut (en fazla 300 karakter). İnsan incelemesi gerekçesini en fazla 500 karakter tut.
9. Yalnızca JSON nesnesi döndür. Markdown kullanma. JSON dışında açıklama yazma.


KRİTER DURUMU VE ZORUNLU ŞART KURALLARI:
- karsilaniyor: İhale kaynağında açıkça bulunan gerçek şart için, company_context içinde bu şartın karşılandığını açıkça gösteren somut kanıt bulunuyorsa kullanılabilir. Varsayım veya genel profil açıklaması yeterli değildir.
- karsilanmiyor: İhale kaynağında açıkça bulunan gerçek şart için, company_context içinde şartın karşılanmadığını açıkça gösteren negatif veya çelişkili somut kanıt varsa kullanılabilir. Yalnızca alanın boş olması veya kanıt bulunmaması yeterli değildir. "Belge listede yok", "profil alanı boş" veya "kanıt bulunamadı" tek başına karsilanmiyor anlamına gelmez.
- bilinmiyor: İhale kaynağında gerçek şart vardır, fakat company_context bu şartın karşılanıp karşılanmadığını göstermiyorsa kullanılmalıdır. Profil alanının boş olması, belgenin profil içinde yer almaması veya personel bilgisinin bulunmaması durumunda varsayılan durum bilinmiyor olmalıdır. Bilinmiyor durumu otomatik uygun_degil üretmemelidir.
- zorunlu_kriter_sonuclari yalnızca ihale kaynağında açıkça belirtilmiş gerçek şartlardan üretilebilir. evaluation_rules içindeki ZOR-01, ZOR-02, ZOR-03, ZOR-04, ZOR-05 kodları tek başına ihale şartı değildir. Bu genel kodlar yalnızca ihale bağlamında açık bir şart ile birebir eşleştirilebiliyorsa kullanılabilir. İhale metninde açık şart bulunmuyorsa ilgili ZOR kodu üretilmemelidir. criterion_id mümkünse ihale kaynağındaki gerçek belge, standart veya şart adından türetilmelidir. Kaynakta bir kod bulunmuyorsa yapay kod üretme. Gerçek zorunlu kriter yoksa zorunlu_kriter_sonuclari boş liste olmalıdır.

GEREKÇE ALANLARI VE YORUM KURALLARI:
- uygunluk_gerekceleri yalnızca şu tür olumlu örtüşmeleri içermelidir: İhale konusu ile profil yetkinliğinin açık örtüşmesi, İhale teknik ihtiyacı ile profil ürün veya hizmetlerinin örtüşmesi, Açık somut şirket kapasitesi kanıtı, Açıkça karşılanan gerçek kriter. uygunluk_gerekceleri alanında belge eksikliği, profil alanının boş olması, belirsizlik, eksik personel bilgisi, mali/operasyonel kapasite verisinin bulunmaması, insan incelemesi ihtiyacı veya uygunsuzluk gerekçesi bulunmamalıdır.
- uygunsuzluk_gerekceleri yalnızca şu durumlarda kullanılmalıdır: Profil ile ihale konusu açıkça kapsam dışıysa, Açık negatif kapsam varsa, Gerçek zorunlu şartın karşılanmadığı somut kanıtla doğrulanmışsa. Eksik veya bilinmeyen veriler uygunsuzluk_gerekcesi değildir.
- eksik_kanitlar alanı: Karar için ihtiyaç duyulan fakat company_context içinde bulunmayan somut kanıtları içermelidir.
- kritik_belirsizlikler alanı: Nihai kararı gerçekten etkileyen çözülmemiş çelişki veya belirsizlikleri içermelidir. Aynı cümle veya aynı bilgi birden fazla alanda tekrar edilmemelidir.
- FİYAT AVANTAJI VE TERCİH UNSURLARI: Yerli malı fiyat avantajı, puan avantajı, tercih avantajı veya bonus kriter doğrudan katılım zorunluluğu değildir. Bu tür şartlar yalnızca açıkça "katılım için zorunludur" denmişse zorunlu kriter sayılabilir. "%15 fiyat avantajı" ifadesi tek başına uygunluk veya uygunsuzluk gerekçesi yapılamaz. Şirketin yerlilik durumu bilinmiyorsa bu durum otomatik inceleme_gerekli üretmemelidir.
- İDARİ VE TEKLİF SÜRECİ UNSURLARI: EKAP üzerinden teklif verme, elektronik eksiltme, elektronik teklif mektubu, ihale dokümanı indirme, teklif gönderme yöntemi gibi unsurlar açıkça şirketin teknik veya hukuki uygunluğunu belirleyen zorunlu şart değilse karar gerekçesi yapılamaz. Şirket profilinde EKAP kaydı veya elektronik eksiltme deneyimi bulunmaması teknik kapasite eksikliği veya uygunsuzluk gerekçesi değildir.
- PROFİL BOŞLUKLARININ YORUMU: Profilde boş alan bulunması bilgi yokluğu anlamına gelir, "hayır", "yok" veya "karşılanmıyor" anlamına gelmez. Durum alanının "taslak" olması ihaleye uygun olmadığı anlamına gelmez. mali_yeterlilik.veri_durumu = eksik vb. ifadeler yalnızca ihale gerçekten bunu istiyorsa dikkate alınır. Yalnızca nihai kararı etkileyen kritik boşluklar eksik_kanitlar veya kritik_belirsizlikler alanına yazılmalıdır.

ALAN SÖZLEŞMESİ:
- decision: JSON string olmalıdır. Yalnızca şu üç değerden biri olabilir: uygun, uygun_degil, inceleme_gerekli. Boş olamaz. Açıklama metni olamaz. Dikey çizgili birleşik değer olamaz.
- confidence: JSON number olmalıdır. 0.0 ile 1.0 arasında olmalıdır. String olamaz. null olamaz. Boş olamaz. Belirli bir varsayılan sayı kullanılamaz. Kanıt gücüne göre bağımsız hesaplanmalıdır.
- birincil_profil_kodu: Tam olarak {category_code} olmalıdır.

Yanıt DECISION_OUTPUT_SCHEMA tarafından teknik olarak sınırlandırılmıştır.
Şemadaki bütün required alanları eksiksiz üret. Hiçbir zorunlu anahtarı atlama.


KRİTER DURUMU VE ZORUNLU ŞART KURALLARI:
- karsilaniyor: İhale kaynağında açıkça bulunan gerçek şart için, company_context içinde bu şartın karşılandığını açıkça gösteren somut kanıt bulunuyorsa kullanılabilir. Varsayım veya genel profil açıklaması yeterli değildir.
- karsilanmiyor: İhale kaynağında açıkça bulunan gerçek şart için, company_context içinde şartın karşılanmadığını açıkça gösteren negatif veya çelişkili somut kanıt varsa kullanılabilir. Yalnızca alanın boş olması veya kanıt bulunmaması yeterli değildir. "Belge listede yok", "profil alanı boş" veya "kanıt bulunamadı" tek başına karsilanmiyor anlamına gelmez.
- bilinmiyor: İhale kaynağında gerçek şart vardır, fakat company_context bu şartın karşılanıp karşılanmadığını göstermiyorsa kullanılmalıdır. Profil alanının boş olması, belgenin profil içinde yer almaması veya personel bilgisinin bulunmaması durumunda varsayılan durum bilinmiyor olmalıdır. Bilinmiyor durumu otomatik uygun_degil üretmemelidir.
- zorunlu_kriter_sonuclari yalnızca ihale kaynağında açıkça belirtilmiş gerçek şartlardan üretilebilir. evaluation_rules içindeki ZOR-01, ZOR-02, ZOR-03, ZOR-04, ZOR-05 kodları tek başına ihale şartı değildir. Bu genel kodlar yalnızca ihale bağlamında açık bir şart ile birebir eşleştirilebiliyorsa kullanılabilir. İhale metninde açık şart bulunmuyorsa ilgili ZOR kodu üretilmemelidir. criterion_id mümkünse ihale kaynağındaki gerçek belge, standart veya şart adından türetilmelidir. Kaynakta bir kod bulunmuyorsa yapay kod üretme. Gerçek zorunlu kriter yoksa zorunlu_kriter_sonuclari boş liste olmalıdır.

GEREKÇE ALANLARI VE YORUM KURALLARI:
- uygunluk_gerekceleri yalnızca şu tür olumlu örtüşmeleri içermelidir: İhale konusu ile profil yetkinliğinin açık örtüşmesi, İhale teknik ihtiyacı ile profil ürün veya hizmetlerinin örtüşmesi, Açık somut şirket kapasitesi kanıtı, Açıkça karşılanan gerçek kriter. uygunluk_gerekceleri alanında belge eksikliği, profil alanının boş olması, belirsizlik, eksik personel bilgisi, mali/operasyonel kapasite verisinin bulunmaması, insan incelemesi ihtiyacı veya uygunsuzluk gerekçesi bulunmamalıdır.
- uygunsuzluk_gerekceleri yalnızca şu durumlarda kullanılmalıdır: Profil ile ihale konusu açıkça kapsam dışıysa, Açık negatif kapsam varsa, Gerçek zorunlu şartın karşılanmadığı somut kanıtla doğrulanmışsa. Eksik veya bilinmeyen veriler uygunsuzluk_gerekcesi değildir.
- eksik_kanitlar alanı: Karar için ihtiyaç duyulan fakat company_context içinde bulunmayan somut kanıtları içermelidir.
- kritik_belirsizlikler alanı: Nihai kararı gerçekten etkileyen çözülmemiş çelişki veya belirsizlikleri içermelidir. Aynı cümle veya aynı bilgi birden fazla alanda tekrar edilmemelidir.
- FİYAT AVANTAJI VE TERCİH UNSURLARI: Yerli malı fiyat avantajı, puan avantajı, tercih avantajı veya bonus kriter doğrudan katılım zorunluluğu değildir. Bu tür şartlar yalnızca açıkça "katılım için zorunludur" denmişse zorunlu kriter sayılabilir. "%15 fiyat avantajı" ifadesi tek başına uygunluk veya uygunsuzluk gerekçesi yapılamaz. Şirketin yerlilik durumu bilinmiyorsa bu durum otomatik inceleme_gerekli üretmemelidir.
- İDARİ VE TEKLİF SÜRECİ UNSURLARI: EKAP üzerinden teklif verme, elektronik eksiltme, elektronik teklif mektubu, ihale dokümanı indirme, teklif gönderme yöntemi gibi unsurlar açıkça şirketin teknik veya hukuki uygunluğunu belirleyen zorunlu şart değilse karar gerekçesi yapılamaz. Şirket profilinde EKAP kaydı veya elektronik eksiltme deneyimi bulunmaması teknik kapasite eksikliği veya uygunsuzluk gerekçesi değildir.
- PROFİL BOŞLUKLARININ YORUMU: Profilde boş alan bulunması bilgi yokluğu anlamına gelir, "hayır", "yok" veya "karşılanmıyor" anlamına gelmez. Durum alanının "taslak" olması ihaleye uygun olmadığı anlamına gelmez. mali_yeterlilik.veri_durumu = eksik vb. ifadeler yalnızca ihale gerçekten bunu istiyorsa dikkate alınır. Yalnızca nihai kararı etkileyen kritik boşluklar eksik_kanitlar veya kritik_belirsizlikler alanına yazılmalıdır.

GEÇERLİ KANIT KİMLİKLERİ:
{valid_chunk_ids}

İHALE BİLGİSİ:
İhale ID: {tender_id}
İKN: {ikn}
Kategori: {category_code}

MATCHING MODE:
{matching_mode}

EŞLEŞME ÖZETİ (Sinyaller):
Retrieval Score: {retrieval_score}
Score Breakdown:
{score_breakdown}

İHALE BAĞLAMI (Seçilmiş Parçalar):
{tender_context}

ŞİRKET BAĞLAMI (Profil ve Kanıtlar):
{company_context}
"""
}

QWEN_CORRECTION_MSG = """
ÖNCEKİ YANITINIZ GEÇERSİZDİ.
HATA: {error}

ÖNEMLİ:
- Önceki hatalı yanıtı açıklama.
- Önceki karar, güven, gerekçe veya kriterleri kopyalama.
- İhale ve şirket bağlamını baştan değerlendir.
- message, status ve timestamp alanı üretme.
- "JSON başarıyla işlendi" veya "sisteme entegre edildi" mesajı üretme.
- Profil JSON'unu geri döndürme.
- Kararı gerçek kanıta göre seç. Belirli bir karar sınıfını varsayılan olarak seçme.
- confidence değerini kanıt gücüne göre üret. Sabit confidence değeri kullanma.
- İhalede olmayan zorunlu kriter üretme. Gerçek kriter yoksa zorunlu_kriter_sonuclari boş liste olsun.
- uygunluk_gerekceleri list[str] olmalıdır. uygunsuzluk_gerekceleri list[str] olmalıdır.
- Her iki listede en fazla 3 kısa madde bulunmalıdır. Gerekçe listelerinde nesne bulunamaz.
- Aynı bilgiyi farklı alanlarda tekrar etme.
- Önceki yanıtta eksik kanıtı karsilanmiyor olarak işaretlediysen bunu düzelt.
- Profilde kanıt yoksa ve negatif kanıt da yoksa status=bilinmiyor kullan.
- Kanıt bulunmaması tek başına karsilanmiyor değildir.
- Eksik kanıtları uygunluk_gerekceleri alanından çıkar.
- Zorunlu olmayan fiyat avantajı, elektronik eksiltme veya teklif süreci unsurunu zorunlu kriter veya uygunsuzluk gerekçesi yapma.
- evaluation_rules içindeki genel ZOR kodlarını ihale şartı olmadan kopyalama.
- Aynı hatalı alan dağılımını tekrar üretme.
- Boş string üretme; bilgi yoksa ilgili listeyi [] yap.
- Uygunluk gerekçeleri yalnızca olumlu örtüşme veya açık karşılanan şart içerebilir.
- Eksiklik, bilinmezlik ve kanıt yokluğu uygunluk gerekçesi değildir.
- Fiyat avantajı ve standart teklif süreci kritik belirsizlik değildir.
- Üretilen her zorunlu kriter gerçek bir evidence_chunk_id içermelidir.
- Kriter için gerçek ihale chunk_id bulunamıyorsa kriter nesnesini üretme.
- Hata mesajında belirtilen alanı düzeltmeden aynı çıktıyı tekrar etme.
- criterion_id alanına chk_* parça kimliği yazma.
- Gerçek kriter veya belge adını yaz.
- Parça kimliğini yalnızca evidence_chunk_ids alanında kullan.

- Yalnızca tek bir geçerli JSON nesnesi döndür. Markdown veya açıklama ekleme.
- Hata mesajında adı geçen eksik veya geçersiz alanı mutlaka düzelt.
- decision ve confidence bütün yanıtlarda zorunludur.
- Bir önceki yanıtta bulunmayan zorunlu alanları tamamla.
- Şemadaki required listesindeki hiçbir alan atlanamaz.
- Önceki hatalı JSON nesnesini aynen tekrar etme.
- Aynı hatalı cevabı yeniden döndürme.
- Hata mesajında adı geçen eksik veya geçersiz alanı mutlaka düzelt.
- decision ve confidence bütün yanıtlarda zorunludur.
- Bir önceki yanıtta bulunmayan zorunlu alanları tamamla.
- Şemadaki required listesindeki hiçbir alan atlanamaz.
- Önceki hatalı JSON nesnesini aynen tekrar etme.
- Aynı hatalı cevabı yeniden döndürme.

ALAN SÖZLEŞMESİ:
- decision: JSON string olmalıdır. Yalnızca şu üç değerden biri olabilir: uygun, uygun_degil, inceleme_gerekli. Boş olamaz. Açıklama metni olamaz. Dikey çizgili birleşik değer olamaz.
- confidence: JSON number olmalıdır. 0.0 ile 1.0 arasında olmalıdır. String olamaz. null olamaz. Boş olamaz. Belirli bir varsayılan sayı kullanılamaz. Kanıt gücüne göre belirlenmelidir.

Yanıt DECISION_OUTPUT_SCHEMA tarafından teknik olarak sınırlandırılmıştır.
Şemadaki bütün required alanları eksiksiz üret. Hiçbir zorunlu anahtarı atlama.
"""

GEMMA_CORRECTION_MSG = """
ÖNCEKİ YANITINIZ GEÇERSİZDİ.
HATA: {error}

ÖNEMLİ:
- Önceki hatalı yanıtı açıklama.
- Şirket profil nesnesini aynen döndürme.
- Şu alanları kök çıktı olarak üretme: profil_kodu, profil_adi, profil_ailesi, profil_surumu, durum, birincil_yetkinlikler, destekleyici_profiller, urunler_ve_hizmetler, context_policy, description_expanded.
- birincil_profil_kodu tam olarak {category_code} olmalıdır.
- Başka profil kodları yalnızca gerçekten destekleyiciyse ikincil_profil_kodlari içinde bulunabilir.
- Kararı gerçek kanıta göre bağımsız seç. Belirli bir karar sınıfını varsayılan seçme.
- Sabit confidence değeri kullanma.
- Gerçek zorunlu kriter yoksa zorunlu_kriter_sonuclari boş liste olsun.
- Kriter nesnesi yalnızca şu alanları içerebilir: criterion_id, description, status, evidence_chunk_ids, explanation.
- Şu alan adları yasaktır: kural_kodu, durum, aciklama, kanit_idleri.
- status yalnızca: karsilaniyor, karsilanmiyor, bilinmiyor olabilir.
- Önceki yanıtta eksik kanıtı karsilanmiyor olarak işaretlediysen bunu düzelt.
- Profilde kanıt yoksa ve negatif kanıt da yoksa status=bilinmiyor kullan.
- Kanıt bulunmaması tek başına karsilanmiyor değildir.
- Eksik kanıtları uygunluk_gerekceleri alanından çıkar.
- Zorunlu olmayan fiyat avantajı, elektronik eksiltme veya teklif süreci unsurunu zorunlu kriter veya uygunsuzluk gerekçesi yapma.
- evaluation_rules içindeki genel ZOR kodlarını ihale şartı olmadan kopyalama.
- Aynı hatalı alan dağılımını tekrar üretme.
- Boş string üretme; bilgi yoksa ilgili listeyi [] yap.
- Uygunluk gerekçeleri yalnızca olumlu örtüşme veya açık karşılanan şart içerebilir.
- Eksiklik, bilinmezlik ve kanıt yokluğu uygunluk gerekçesi değildir.
- Fiyat avantajı ve standart teklif süreci kritik belirsizlik değildir.
- Üretilen her zorunlu kriter gerçek bir evidence_chunk_id içermelidir.
- Kriter için gerçek ihale chunk_id bulunamıyorsa kriter nesnesini üretme.
- Hata mesajında belirtilen alanı düzeltmeden aynı çıktıyı tekrar etme.
- criterion_id alanına chk_* parça kimliği yazma.
- Gerçek kriter veya belge adını yaz.
- Parça kimliğini yalnızca evidence_chunk_ids alanında kullan.

- Yalnızca tek bir geçerli JSON nesnesi döndür. Markdown veya açıklama ekleme.
- Hata mesajında adı geçen eksik veya geçersiz alanı mutlaka düzelt.
- decision ve confidence bütün yanıtlarda zorunludur.
- Bir önceki yanıtta bulunmayan zorunlu alanları tamamla.
- Şemadaki required listesindeki hiçbir alan atlanamaz.
- Önceki hatalı JSON nesnesini aynen tekrar etme.
- Aynı hatalı cevabı yeniden döndürme.

ALAN SÖZLEŞMESİ:
- decision: JSON string olmalıdır. Yalnızca şu üç değerden biri olabilir: uygun, uygun_degil, inceleme_gerekli. Boş olamaz. Açıklama metni olamaz. Dikey çizgili birleşik değer olamaz.
- confidence: JSON number olmalıdır. 0.0 ile 1.0 arasında olmalıdır. String olamaz. null olamaz. Boş olamaz. Belirli bir varsayılan sayı kullanılamaz. Kanıt gücüne göre belirlenmelidir.

Yanıt DECISION_OUTPUT_SCHEMA tarafından teknik olarak sınırlandırılmıştır.
Şemadaki bütün required alanları eksiksiz üret. Hiçbir zorunlu anahtarı atlama.
"""

COMPACT_DECISION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "decision",
        "confidence",
        "birincil_profil_kodu",
        "faaliyet_eslesmesi",
        "negatif_kapsam_cakismasi",
        "gerekceler",
        "faaliyet_belirsizlikleri",
        "katilim_belirsizlikleri",
        "zorunlu_kriter_sonuclari",
        "uygun_kisimlar",
        "kullanilan_chunk_idleri",
        "kaynak_disinda_bilgi_var_mi",
    ],
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["uygun", "uygun_degil", "inceleme_gerekli"],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "birincil_profil_kodu": {"type": "string", "minLength": 1},
        "faaliyet_eslesmesi": {
            "type": "string",
            "enum": ["guclu", "kismi", "zayif", "belirsiz"],
        },
        "negatif_kapsam_cakismasi": {"type": "boolean"},
        "gerekceler": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
            "maxItems": 3,
        },
        "faaliyet_belirsizlikleri": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
            "maxItems": 2,
        },
        "katilim_belirsizlikleri": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
            "maxItems": 3,
        },
        "zorunlu_kriter_sonuclari": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "criterion_id",
                    "description",
                    "status",
                    "evidence_chunk_ids",
                    "explanation",
                ],
                "properties": {
                    "criterion_id": {"type": "string", "minLength": 1, "maxLength": 160},
                    "description": {"type": "string", "minLength": 1, "maxLength": 300},
                    "status": {
                        "type": "string",
                        "enum": ["karsilaniyor", "karsilanmiyor", "bilinmiyor"],
                    },
                    "evidence_chunk_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 300},
                        "uniqueItems": True,
                        "minItems": 1,
                    },
                    "explanation": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        },
        "uygun_kisimlar": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kisim_no", "kisim_adi", "evidence_chunk_ids", "gerekce"],
                "properties": {
                    "kisim_no": {"type": "string", "minLength": 1, "maxLength": 40},
                    "kisim_adi": {"type": "string", "minLength": 1, "maxLength": 300},
                    "evidence_chunk_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 300},
                        "uniqueItems": True,
                        "minItems": 1,
                    },
                    "gerekce": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        },
        "kullanilan_chunk_idleri": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
            "uniqueItems": True,
        },
        "kaynak_disinda_bilgi_var_mi": {"type": "boolean"},
    },
    "additionalProperties": False,
}


# v3 deneylerinin yeniden üretilebilmesi için eski ayrıntılı şema korunur.
DECISION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "decision", "confidence", "birincil_profil_kodu", "ikincil_profil_kodlari",
        "faaliyet_eslesmesi", "negatif_kapsam_cakismasi", "katilim_yeterliligi_durumu",
        "uygunluk_gerekceleri", "uygunsuzluk_gerekceleri", "zorunlu_kriter_sonuclari",
        "kritik_faaliyet_belirsizlikleri", "dogrulanamayan_katilim_sartlari",
        "eksik_kanitlar", "kritik_belirsizlikler", "kullanilan_chunk_idleri",
        "kaynak_disinda_bilgi_var_mi", "insan_incelemesi_gerekcesi"
    ],
    "properties": {
        "decision": {"type": "string", "enum": ["uygun", "uygun_degil", "inceleme_gerekli"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "birincil_profil_kodu": {"type": "string", "minLength": 1},
        "ikincil_profil_kodlari": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "uniqueItems": True},
        "faaliyet_eslesmesi": {"type": "string", "enum": ["guclu", "kismi", "zayif", "belirsiz"]},
        "negatif_kapsam_cakismasi": {"type": "boolean"},
        "katilim_yeterliligi_durumu": {"type": "string", "enum": ["dogrulandi", "dogrulanmadi", "karsilanmiyor", "uygulanamaz"]},
        "uygunluk_gerekceleri": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 3},
        "uygunsuzluk_gerekceleri": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 3},
        "zorunlu_kriter_sonuclari": {
            "type": "array", "maxItems": 5,
            "items": {"type": "object", "additionalProperties": False,
                "required": ["criterion_id", "description", "status", "evidence_chunk_ids", "explanation"],
                "properties": {
                    "criterion_id": {"type": "string", "minLength": 1, "maxLength": 160},
                    "description": {"type": "string", "minLength": 1, "maxLength": 300},
                    "status": {"type": "string", "enum": ["karsilaniyor", "karsilanmiyor", "bilinmiyor"]},
                    "evidence_chunk_ids": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "uniqueItems": True, "minItems": 1},
                    "explanation": {"type": "string", "minLength": 1, "maxLength": 400}
                }
            }
        },
        "kritik_faaliyet_belirsizlikleri": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 3},
        "dogrulanamayan_katilim_sartlari": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 5},
        "eksik_kanitlar": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 3},
        "kritik_belirsizlikler": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 3},
        "kullanilan_chunk_idleri": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "uniqueItems": True},
        "kaynak_disinda_bilgi_var_mi": {"type": "boolean"},
        "insan_incelemesi_gerekcesi": {"type": "string", "maxLength": 500}
    },
    "additionalProperties": False
}

class OllamaDecisionModel:
    def __init__(
        self,
        name: str,
        host: str = "http://localhost:11434",
        prompt_version: str = "isbak_qwen_decision_v2",
        timeout_seconds: float = 60.0,
    ):
        self.name = name
        self.host = host
        self.prompt_version = prompt_version

        from app.config import get_settings

        settings = get_settings()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds != 60.0
            else float(settings.ollama_decision_timeout_seconds)
        )
        self.max_attempts = settings.ollama_max_attempts
        self.backoff_seconds = settings.ollama_retry_backoff_seconds

        self.gemma_num_ctx = settings.gemma_decision_num_ctx
        self.gemma_num_predict = settings.gemma_decision_num_predict
        self.qwen_num_ctx = settings.qwen_decision_num_ctx
        self.qwen_num_predict = settings.qwen_decision_num_predict

        self.ollama_num_thread = settings.ollama_num_thread
        self.ollama_num_batch = settings.ollama_num_batch
        self.max_json_corrections = settings.max_json_corrections

        if self.prompt_version not in PROMPTS:
            raise ValueError(f"Unknown prompt version: {prompt_version}")

    def analyze(
        self,
        *,
        tender_id: str,
        ikn: str,
        category_code: str,
        tender_context: str,
        company_context: str,
        matching_mode: str = "profile_to_tender",
        retrieval_score: float = 0.0,
        score_breakdown: dict[str, Any] | None = None,
        valid_chunk_ids: list[str] | None = None,
        primary_profile_code: str = "",
    ) -> ModelDecision:
        logger.info(
            f"Analyzing tender {tender_id} ({ikn}) with model {self.name} (prompt: {self.prompt_version})"
        )
        logger.debug(f"Tender context: {tender_context}")
        logger.debug(f"Company context: {company_context}")

        if matching_mode not in ["profile_to_tender", "tender_to_profile"]:
            raise ValueError(f"Geçersiz matching_mode: {matching_mode}")

        if not (0.0 <= retrieval_score <= 1.0):
            raise ValueError(f"Geçersiz retrieval_score: {retrieval_score}. 0.0-1.0 aralığında olmalıdır.")

        category_code = category_code.strip().upper()

        if valid_chunk_ids:
            seen = set()
            cleaned_ids = []
            for v in valid_chunk_ids:
                if v and str(v).strip() and v not in seen:
                    seen.add(v)
                    cleaned_ids.append(str(v).strip())
            valid_chunk_ids = cleaned_ids

        active_prompt_version = self.prompt_version
        prompt_template = PROMPTS[active_prompt_version]

        sb_str = json.dumps(score_breakdown or {}, ensure_ascii=False, indent=2, default=str)
        vc_str = json.dumps(valid_chunk_ids or [], ensure_ascii=False, indent=2, default=str)

        if (
            "v3" in active_prompt_version
            or "v4" in active_prompt_version
            or "compact" in active_prompt_version
        ):
            active_base_prompt = prompt_template.format(
                tender_id=tender_id,
                ikn=ikn,
                category_code=category_code,
                matching_mode=matching_mode,
                retrieval_score=retrieval_score,
                score_breakdown=sb_str,
                valid_chunk_ids=vc_str,
                tender_context=tender_context,
                company_context=company_context,
            )
        else:
            active_base_prompt = prompt_template.format(
                tender_id=tender_id,
                ikn=ikn,
                category_code=category_code,
                tender_context=tender_context,
                company_context=company_context,
            )

        if (
            "v3" in active_prompt_version
            or ("compact" in active_prompt_version and "v4" not in active_prompt_version)
        ):
            active_base_prompt += PROFILE_ACTIVITY_POLICY

        current_prompt = active_base_prompt
        json_correction_attempts = 0
        max_json_corrections = self.max_json_corrections
        # v4 zaten kısa sözleşmedir; aynı istemi yedek adıyla tekrar çağırma.
        has_run_compact_fallback = "v4" in active_prompt_version

        import time

        from app.pipeline.exceptions import TruncatedModelOutput
        last_error = None

        while json_correction_attempts <= max_json_corrections:
            options = {
                "temperature": 0.0,
                "num_gpu": 0,
                "num_thread": self.ollama_num_thread,
                "num_batch": self.ollama_num_batch,
            }
            is_gemma = "gemma" in active_prompt_version or "gemma" in self.name.lower()
            if is_gemma:
                options["num_ctx"] = self.gemma_num_ctx
                options["num_predict"] = self.gemma_num_predict
            else:
                options["num_ctx"] = self.qwen_num_ctx
                options["num_predict"] = self.qwen_num_predict

            active_schema = (
                COMPACT_DECISION_OUTPUT_SCHEMA
                if "v4" in active_prompt_version
                else DECISION_OUTPUT_SCHEMA
            )
            payload = {
                "model": self.name,
                "prompt": current_prompt,
                "format": active_schema,
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "options": options,
            }

            network_success = False
            response_text = ""
            thinking_text = ""
            eval_count = 0
            prompt_eval_count = 0
            done_reason = ""
            total_duration = 0

            for attempt in range(1, self.max_attempts + 1):
                try:
                    with httpx.Client(
                        timeout=self.timeout_seconds,
                        trust_env=False,
                    ) as client:
                        response = client.post(f"{self.host}/api/generate", json=payload)
                        if response.status_code == 404:
                            raise DecisionServiceError(f"Model '{self.name}' bulunamadı (404).")
                        elif 400 <= response.status_code < 500:
                            raise DecisionServiceError(f"Geçersiz Ollama isteği: {response.status_code}")
                        response.raise_for_status()

                    data = response.json()
                    response_text = str(data.get("response") or "").strip()
                    logger.debug(
                        "[MODEL_RESPONSE_PREVIEW] model=%s chars=%s preview=%r",
                        self.name,
                        len(response_text),
                        response_text[:500],
                    )
                    thinking_text = str(data.get("message", {}).get("content", data.get("thinking", ""))) 
                    eval_count = data.get("eval_count", 0)
                    prompt_eval_count = data.get("prompt_eval_count", 0)
                    done_reason = data.get("done_reason", "")
                    total_duration = data.get("total_duration", 0)

                    logger.info(
                        f"[DIAGNOSTICS] model={self.name} prompt={active_prompt_version} "
                        f"done={data.get('done')} done_reason={done_reason} "
                        f"eval_count={eval_count} prompt_eval_count={prompt_eval_count} "
                        f"total_duration={total_duration} response_length={len(response_text)} "
                        f"num_ctx={options.get('num_ctx')} num_predict={options.get('num_predict')}"
                    )

                    if not response_text and "response" not in data:
                        raise DecisionServiceError("Response alanı bulunamadı")

                    network_success = True
                    break
                except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                    logger.warning(f"Network error on attempt {attempt} for model {self.name}: {e}")
                    last_error = e
                    if attempt < self.max_attempts:
                        time.sleep(self.backoff_seconds)

            if not network_success:
                raise DecisionServiceError(f"Model servisine ulaşılamadı: {last_error}")

            if not response_text:
                raise DecisionServiceError(
                    f"Boş model yanıtı. "
                    f"model={self.name}, "
                    f"prompt_version={active_prompt_version}, "
                    f"done_reason={done_reason}, "
                    f"eval_count={eval_count}, "
                    f"prompt_eval_count={prompt_eval_count}, "
                    f"response_length={len(response_text)}, "
                    f"thinking_length={len(thinking_text)}"
                )

            try:
                # Check truncation first
                is_truncated = False
                if done_reason == "length":
                    is_truncated = True
                elif is_gemma and options.get("num_predict") and eval_count >= options["num_predict"] * 0.9:
                    is_truncated = True

                parsed_json = json.loads(response_text)
                if not isinstance(parsed_json, dict):
                    raise ValueError("JSON kök elemanı obje (dict) olmalıdır, liste değil.")

                parsed_json["_metadata"] = {
                    "json_correction_attempts": json_correction_attempts,
                    "prompt_version": active_prompt_version,
                    "timeout_seconds": self.timeout_seconds,
                    "diagnostics": {
                        "eval_count": eval_count,
                        "done_reason": done_reason,
                    }
                }

                parsed_json = self._sanitize_semantic_fields(
                    parsed_json,
                    tender_context=tender_context,
                    valid_chunk_ids=valid_chunk_ids,
                )
                self._validate_semantic_field_usage(parsed_json, tender_context, valid_chunk_ids)

                actual_category = primary_profile_code.strip().upper() if primary_profile_code else category_code
                return self._validate_and_build(parsed_json, actual_category, valid_chunk_ids)
            except (json.JSONDecodeError, ValueError) as e:
                err_str = str(e)
                if "Unterminated string" in err_str or "Expecting value" in err_str or "EOF" in err_str:
                    is_truncated = True

                if is_truncated:
                    logger.warning(f"Kesilmiş çıktı tespit edildi (TruncatedModelOutput). Hata: {e}")
                    if not has_run_compact_fallback:
                        has_run_compact_fallback = True
                        json_correction_attempts = 0 # reset attempts for fallback
                        if is_gemma:
                            active_prompt_version = "isbak_gemma_review_compact"
                            logger.info("Kompakt ikinci görüş istemi ile (fallback) baştan çağrılıyor.")
                        else:
                            active_prompt_version = "isbak_qwen_decision_compact"
                            logger.info("Kompakt birincil karar istemi ile (fallback) baştan çağrılıyor.")

                        active_base_prompt = PROMPTS[active_prompt_version].format(
                            tender_id=tender_id, ikn=ikn, category_code=category_code,
                            matching_mode=matching_mode, retrieval_score=retrieval_score,
                            score_breakdown=sb_str, valid_chunk_ids=vc_str,
                            tender_context=tender_context, company_context=company_context
                        )
                        active_base_prompt += PROFILE_ACTIVITY_POLICY
                        current_prompt = active_base_prompt
                        continue
                    else:
                        raise TruncatedModelOutput(f"Kesilmiş cevap oluştu: {err_str}")

                logger.warning(f"JSON/Schema error: {e}. Correction attempt: {json_correction_attempts}")
                last_error = e
                if json_correction_attempts < max_json_corrections:
                    json_correction_attempts += 1


                    err_str_lower = str(e).lower()
                    dynamic_prefix = ""
                    if "ikincil profil" in err_str_lower or "ikincil_profil" in err_str_lower:
                        dynamic_prefix += "\n- ikincil_profil_kodlari alanını [] yap. Önceki kodları tekrar üretme.\n"
                    if "kriter" in err_str_lower or "criterion_id" in err_str_lower:
                        dynamic_prefix += "\n- zorunlu_kriter_sonuclari alanını [] yap. Gerçek kriter ve geçerli parça kimliği kesin değilse kriter üretme.\n"
                    if "karsilanmiyor" in err_str_lower:
                        dynamic_prefix += "\n- Negatif şirket kanıtı yoksa status değerini bilinmiyor yap.\n"
                    if "fiyat avantajı" in err_str_lower:
                        dynamic_prefix += "\n- Fiyat avantajını tüm karar alanlarından çıkar.\n"

                    if is_gemma:
                        correction_msg = GEMMA_CORRECTION_MSG.format(error=e, category_code=category_code)
                    else:
                        correction_msg = QWEN_CORRECTION_MSG.format(error=e, category_code=category_code)
                    correction_msg = dynamic_prefix + correction_msg

                    if valid_chunk_ids:
                        correction_msg += (
                            "\\nKullanılan tüm kanıt kimlikleri yalnızca şu listeden seçilmelidir:\\n"
                            f"{vc_str}\\n"
                        )

                    current_prompt = active_base_prompt + correction_msg
                else:
                    if not is_gemma:
                        try:
                            fallback_json = json.loads(response_text)
                            if not isinstance(fallback_json, dict):
                                raise ValueError("Kök obje dict değil")
                        except Exception:
                            raise DecisionServiceError(f"Tüm düzeltme hakları bitti ve son çıktı geçerli JSON değil. Son Hata: {e}")

                        logger.warning(f"Birincil model tüm düzeltme haklarını tüketti. Güvenli temizleme (fallback) uygulanıyor. Son Hata: {e}")

                        # decision, confidence, birincil_profil_kodu sağlam mı kontrol et
                        try:
                            from app.decision.decision_normalizer import normalize_decision
                            normalize_decision(str(fallback_json.get("decision", "")))
                            conf = fallback_json.get("confidence")
                            if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
                                raise ValueError("confidence bozuk")
                            b_kodu = str(fallback_json.get("birincil_profil_kodu", "")).strip().upper()
                            expected_code = primary_profile_code.strip().upper() if primary_profile_code else category_code.strip().upper()
                            if b_kodu != expected_code:
                                raise ValueError("birincil_profil_kodu bozuk")
                        except ValueError as base_err:
                            raise DecisionServiceError(f"Temel alanlar bozuk olduğu için temizleme yapılamadı: {base_err}. Asıl Hata: {e}")

                        # ikincil_profil_kodlari temizleme
                        ikincil_k = fallback_json.get("ikincil_profil_kodlari", [])
                        if isinstance(ikincil_k, list) and len(ikincil_k) > 0:
                            try:
                                self._validate_ikincil_profil_kodlari(fallback_json, ikincil_k)
                            except ValueError:
                                logger.info("ikincil_profil_kodlari hataya sebep oldu, [] yapılıyor.")
                                fallback_json["ikincil_profil_kodlari"] = []

                        # zorunlu_kriter_sonuclari temizleme
                        raw_criteria = fallback_json.get("zorunlu_kriter_sonuclari", [])
                        if isinstance(raw_criteria, list):
                            valid_criteria = []
                            for crit in raw_criteria:
                                if isinstance(crit, dict):
                                    try:
                                        self._validate_single_criterion(crit, valid_chunk_ids)
                                        valid_criteria.append(crit)
                                    except ValueError as crit_err:
                                        logger.info(f"Geçersiz kriter silindi: {crit.get('criterion_id', '')} - Hata: {crit_err}")
                            fallback_json["zorunlu_kriter_sonuclari"] = valid_criteria

                        # Son olarak güvenli alan temizliği ve tekrar doğrulama
                        try:
                            fallback_json = self._sanitize_semantic_fields(
                                fallback_json,
                                tender_context=tender_context,
                                valid_chunk_ids=valid_chunk_ids,
                            )
                            self._validate_semantic_field_usage(fallback_json, tender_context, valid_chunk_ids)
                            actual_category = primary_profile_code.strip().upper() if primary_profile_code else category_code
                            return self._validate_and_build(fallback_json, actual_category, valid_chunk_ids)
                        except ValueError as final_err:
                            raise DecisionServiceError(f"Temizlenmiş çıktı ikinci doğrulamayı geçemedi: {final_err}. Asıl Hata: {e}")
                    else:
                        raise DecisionServiceError(f"Tüm düzeltme hakları bitti. Geçersiz JSON formatı veya şema hatası: {e}")

        raise DecisionServiceError(f"Geçersiz JSON formatı veya şema hatası: {last_error}")


    @staticmethod
    def _expand_compact_contract(data: dict[str, Any]) -> dict[str, Any]:
        """v4 kısa çıktısını geriye uyumlu iç karar alanlarına dönüştürür."""

        expanded = dict(data)
        decision = str(expanded.get("decision", "")).strip()
        reasons = expanded.get("gerekceler", [])
        if not isinstance(reasons, list):
            reasons = []
        activity_uncertainties = expanded.get("faaliyet_belirsizlikleri", [])
        if not isinstance(activity_uncertainties, list):
            activity_uncertainties = []
        participation_gaps = expanded.get("katilim_belirsizlikleri", [])
        if not isinstance(participation_gaps, list):
            participation_gaps = []

        expanded.setdefault("ikincil_profil_kodlari", [])
        expanded.setdefault(
            "faaliyet_eslesmesi",
            {
                "uygun": "guclu",
                "uygun_degil": "zayif",
            }.get(decision, "belirsiz"),
        )
        expanded.setdefault("negatif_kapsam_cakismasi", False)
        expanded.setdefault(
            "uygunluk_gerekceleri",
            reasons if decision == "uygun" else [],
        )
        expanded.setdefault(
            "uygunsuzluk_gerekceleri",
            reasons if decision == "uygun_degil" else [],
        )
        expanded.setdefault("kritik_faaliyet_belirsizlikleri", activity_uncertainties)
        expanded.setdefault("dogrulanamayan_katilim_sartlari", participation_gaps)
        expanded.setdefault("eksik_kanitlar", [])
        expanded.setdefault("kritik_belirsizlikler", activity_uncertainties)
        expanded.setdefault("zorunlu_kriter_sonuclari", [])
        expanded.setdefault("uygun_kisimlar", [])
        expanded.setdefault("kullanilan_chunk_idleri", [])
        expanded.setdefault("kaynak_disinda_bilgi_var_mi", False)
        expanded.setdefault(
            "insan_incelemesi_gerekcesi",
            str(activity_uncertainties[0])
            if decision == "inceleme_gerekli" and activity_uncertainties
            else "",
        )

        criteria = expanded.get("zorunlu_kriter_sonuclari", [])
        statuses = (
            {
                str(item.get("status") or "")
                for item in criteria
                if isinstance(item, dict)
            }
            if isinstance(criteria, list)
            else set()
        )
        if "karsilanmiyor" in statuses:
            participation_status = "karsilanmiyor"
        elif "bilinmiyor" in statuses or participation_gaps:
            participation_status = "dogrulanmadi"
        elif statuses and statuses == {"karsilaniyor"}:
            participation_status = "dogrulandi"
        else:
            participation_status = "uygulanamaz"
        expanded.setdefault("katilim_yeterliligi_durumu", participation_status)

        if decision == "inceleme_gerekli" and not activity_uncertainties and reasons:
            expanded["kritik_faaliyet_belirsizlikleri"] = reasons[:1]
            expanded["kritik_belirsizlikler"] = reasons[:1]
            expanded["insan_incelemesi_gerekcesi"] = str(reasons[0])

        return expanded

    def _sanitize_semantic_fields(
        self,
        data: dict[str, Any],
        *,
        tender_context: str,
        valid_chunk_ids: list[str] | None,
    ) -> dict[str, Any]:
        """Modeli yeniden çağırmadan güvenli alan temizliği yapar."""
        cleaned = self._expand_compact_contract(data)

        ignored_process_phrases = (
            "%15 fiyat avantajı",
            "fiyat avantajı",
            "yerli malı fiyat avantajı",
            "elektronik eksiltme",
            "ekap kaydı",
            "ekap üzerinden teklif",
            "elektronik teklif",
            "teklif mektubu",
            "e-imza",
            "e imza",
        )
        negative_reason_phrases = (
            "bulunmamaktadır",
            "bulunmuyor",
            "eksiktir",
            "eksik",
            "doğrulanmamıştır",
            "kanıt yok",
            "kanıt bulunamadı",
            "belirsiz",
            "bilinmiyor",
            "örtüşmemektedir",
            "kapsam dışı",
        )

        def clean_string_list(
            field_name: str,
            *,
            blocked_phrases: tuple[str, ...] = (),
        ) -> list[str]:
            value = cleaned.get(field_name, [])
            if not isinstance(value, list):
                return []

            result: list[str] = []
            seen: set[str] = set()
            for item in value:
                item_text = " ".join(str(item).split()).strip()
                lowered = item_text.casefold()
                if not item_text or any(
                    phrase.casefold() in lowered
                    for phrase in blocked_phrases
                ):
                    continue
                if lowered in seen:
                    continue
                seen.add(lowered)
                result.append(item_text)
            return result

        cleaned["uygunluk_gerekceleri"] = clean_string_list(
            "uygunluk_gerekceleri",
            blocked_phrases=negative_reason_phrases,
        )
        cleaned["uygunsuzluk_gerekceleri"] = clean_string_list(
            "uygunsuzluk_gerekceleri"
        )
        for field_name in (
            "eksik_kanitlar",
            "kritik_belirsizlikler",
            "kritik_faaliyet_belirsizlikleri",
            "dogrulanamayan_katilim_sartlari",
        ):
            cleaned[field_name] = clean_string_list(
                field_name,
                blocked_phrases=ignored_process_phrases,
            )

        cleaned["kullanilan_chunk_idleri"] = list(
            dict.fromkeys(
                chunk_id
                for chunk_id in clean_string_list("kullanilan_chunk_idleri")
                if not valid_chunk_ids or chunk_id in valid_chunk_ids
            )
        )

        raw_parts = cleaned.get("uygun_kisimlar", [])
        safe_parts: list[dict[str, Any]] = []
        if isinstance(raw_parts, list):
            for raw_part in raw_parts:
                if not isinstance(raw_part, dict):
                    continue
                part_number = " ".join(
                    str(raw_part.get("kisim_no") or "").split()
                ).strip()
                part_name = " ".join(
                    str(raw_part.get("kisim_adi") or "").split()
                ).strip()
                reason = " ".join(
                    str(raw_part.get("gerekce") or "").split()
                ).strip()
                evidence = raw_part.get("evidence_chunk_ids", [])
                if not isinstance(evidence, list):
                    evidence = []
                evidence = list(
                    dict.fromkeys(
                        str(chunk_id).strip()
                        for chunk_id in evidence
                        if str(chunk_id).strip()
                        and (
                            not valid_chunk_ids
                            or str(chunk_id).strip() in valid_chunk_ids
                        )
                    )
                )
                if not part_number or not part_name or not reason or not evidence:
                    continue
                safe_parts.append(
                    {
                        "kisim_no": part_number,
                        "kisim_adi": part_name,
                        "evidence_chunk_ids": evidence,
                        "gerekce": reason,
                    }
                )
        cleaned["uygun_kisimlar"] = safe_parts

        raw_criteria = cleaned.get("zorunlu_kriter_sonuclari", [])
        safe_criteria: list[dict[str, Any]] = []
        if isinstance(raw_criteria, list):
            for raw_criterion in raw_criteria:
                if not isinstance(raw_criterion, dict):
                    continue

                criterion = dict(raw_criterion)
                combined_text = " ".join(
                    str(criterion.get(key, ""))
                    for key in ("criterion_id", "description", "explanation")
                ).casefold()
                if any(
                    phrase.casefold() in combined_text
                    for phrase in ignored_process_phrases
                ):
                    continue

                evidence = criterion.get("evidence_chunk_ids", [])
                if not isinstance(evidence, list):
                    evidence = []
                evidence = [
                    str(chunk_id).strip()
                    for chunk_id in evidence
                    if str(chunk_id).strip()
                    and (
                        not valid_chunk_ids
                        or str(chunk_id).strip() in valid_chunk_ids
                    )
                ]
                if not evidence:
                    continue
                criterion["evidence_chunk_ids"] = list(
                    dict.fromkeys(evidence)
                )

                if criterion.get("status") == "karsilanmiyor":
                    missing_only_phrases = (
                        "bulunmamaktadır",
                        "bulunmuyor",
                        "bulunamadı",
                        "sunulmamıştır",
                        "eksik",
                        "kanıtlanmamıştır",
                        "doğrulanmamıştır",
                        "bilgi yok",
                        "veri yok",
                    )
                    if any(
                        phrase in combined_text
                        for phrase in missing_only_phrases
                    ):
                        criterion["status"] = "bilinmiyor"

                safe_criteria.append(criterion)
        cleaned["zorunlu_kriter_sonuclari"] = safe_criteria

        secondary_codes = cleaned.get("ikincil_profil_kodlari", [])
        if isinstance(secondary_codes, list) and secondary_codes:
            try:
                self._validate_ikincil_profil_kodlari(
                    cleaned,
                    secondary_codes,
                )
            except ValueError:
                cleaned["ikincil_profil_kodlari"] = []
        elif not isinstance(secondary_codes, list):
            cleaned["ikincil_profil_kodlari"] = []

        decision = str(cleaned.get("decision", "")).strip()
        activity_match = str(
            cleaned.get("faaliyet_eslesmesi", "")
        ).strip()
        negative_overlap = bool(
            cleaned.get("negatif_kapsam_cakismasi", False)
        )

        if decision == "uygun" and (
            negative_overlap
            or activity_match not in {"guclu", "kismi"}
        ):
            cleaned["decision"] = "inceleme_gerekli"
            if not cleaned["kritik_faaliyet_belirsizlikleri"]:
                cleaned["kritik_faaliyet_belirsizlikleri"] = [
                    "Faaliyet eşleşmesi ile model kararı arasında tutarsızlık bulundu."
                ]
            cleaned["confidence"] = min(
                float(cleaned.get("confidence", 0.7)),
                0.7,
            )

        if (
            decision == "uygun_degil"
            and activity_match in {"guclu", "kismi"}
            and not negative_overlap
        ):
            has_verified_unmet_criterion = any(
                criterion.get("status") == "karsilanmiyor"
                for criterion in cleaned["zorunlu_kriter_sonuclari"]
            )
            if (
                cleaned["dogrulanamayan_katilim_sartlari"]
                and not has_verified_unmet_criterion
            ):
                # Katılım verisinin doğrulanamaması faaliyet kapsamını olumsuz yapmaz.
                cleaned["decision"] = "uygun"
                cleaned["uygunsuzluk_gerekceleri"] = []
                cleaned["kritik_faaliyet_belirsizlikleri"] = []
                cleaned["insan_incelemesi_gerekcesi"] = ""
            else:
                cleaned["decision"] = "inceleme_gerekli"
                if not cleaned["kritik_faaliyet_belirsizlikleri"]:
                    cleaned["kritik_faaliyet_belirsizlikleri"] = [
                        "Faaliyet eşleşmesine rağmen olumsuz karar için açık negatif kapsam doğrulanamadı."
                    ]
                cleaned["confidence"] = min(
                    float(cleaned.get("confidence", 0.7)),
                    0.7,
                )

        if (
            cleaned.get("decision") == "inceleme_gerekli"
            and not cleaned["kritik_faaliyet_belirsizlikleri"]
        ):
            if activity_match in {"guclu", "kismi"} and not negative_overlap:
                cleaned["decision"] = "uygun"
                cleaned["insan_incelemesi_gerekcesi"] = ""
            else:
                source = cleaned["kritik_belirsizlikler"]
                cleaned["kritik_faaliyet_belirsizlikleri"] = (
                    source[:1]
                    if source
                    else [
                    "Faaliyet kapsamı kesin karar için yeterince açık değildir."
                    ]
                )

        return cleaned


    def _validate_semantic_field_usage(self, data: dict[str, Any], tender_context: str, valid_chunk_ids: list[str] | None) -> None:
        # A: Check for empty strings in specific lists
        lists_to_check = [
            "uygunluk_gerekceleri", "uygunsuzluk_gerekceleri", 
            "eksik_kanitlar", "kritik_belirsizlikler", "kritik_faaliyet_belirsizlikleri",
            "dogrulanamayan_katilim_sartlari", "kullanilan_chunk_idleri"
        ]
        for field in lists_to_check:
            val = data.get(field, [])
            if isinstance(val, list):
                for item in val:
                    if not str(item).strip():
                        raise ValueError(f"{field} içinde boş veya yalnızca boşluk içeren string bulunamaz.")

        eksik_k = data.get("eksik_kanitlar", [])
        if isinstance(eksik_k, list):
            for item in eksik_k:
                text = str(item).lower()
                if any(x in text for x in ["%15 fiyat avantajı", "yerli malı fiyat avantajı", "elektronik eksiltme", "ekap kaydı", "ekap üzerinden teklif", "teklif mektubu"]):
                    raise ValueError(f"Fiyat avantajı veya standart teklif süreci eksik_kanitlar yapılamaz: '{item}'")

        # B: Check evidence_chunk_ids and generic gaps in criteria
        raw_criteria = data.get("zorunlu_kriter_sonuclari", [])
        if isinstance(raw_criteria, list):
            for crit in raw_criteria:
                if isinstance(crit, dict):
                    self._validate_single_criterion(crit, valid_chunk_ids)

        # C: Negative patterns in uygunluk_gerekceleri
        neg_patterns = [
            "bulunmamaktadır", "bulunmuyor", "eksiktir", "eksik", 
            "doğrulanmamıştır", "kanıt yok", "kanıt bulunamadı", "belirsiz", "bilinmiyor"
        ]
        uygunluk_g = data.get("uygunluk_gerekceleri", [])
        if isinstance(uygunluk_g, list):
            for item in uygunluk_g:
                text = str(item).lower()
                for pat in neg_patterns:
                    # Simple negative pattern match. We want to reject if the main meaning is negative.
                    # Since it's a direct requirement, we will raise ValueError if pat is strictly in the string.
                    if pat in text:
                        raise ValueError(f"uygunluk_gerekceleri içinde eksiklik veya bilinmezlik ifadesi bulunamaz: '{item}'")

        # D: Generic gaps in kritik_belirsizlikler
        kritik_b = data.get("kritik_belirsizlikler", [])
        if isinstance(kritik_b, list):
            tender_ctx_lower = tender_context.lower()
            generic_gaps = [
                "mali yeterlilik eksik", "operasyonel kapasite eksik",
                "profil taslak", "durum alanı taslak"
            ]
            for item in kritik_b:
                text = str(item).lower()

                # Check price advantage / EKAP
                if any(x in text for x in ["%15 fiyat avantajı", "yerli malı fiyat avantajı", "elektronik eksiltme", "ekap kaydı", "ekap üzerinden teklif", "teklif mektubu"]):
                    raise ValueError(f"Fiyat avantajı veya standart teklif süreci kritik belirsizlik yapılamaz: '{item}'")

                for gap in generic_gaps:
                    if gap in text:
                        if "mali" in gap or "operasyonel" in gap:
                            # Allow if explicitly mentioned in tender context
                            if "mali" in gap and "mali" not in tender_ctx_lower:
                                raise ValueError(f"İhalede mali şart yokken '{item}' kritik belirsizlik olamaz.")
                            if "operasyonel" in gap and "operasyonel" not in tender_ctx_lower:
                                raise ValueError(f"İhalede operasyonel şart yokken '{item}' kritik belirsizlik olamaz.")
                        else:
                            raise ValueError(f"Genel profil boşluğu ('{gap}') ihale şartı olmadan kritik belirsizlik olamaz.")

        # E: Faaliyet ve katılım alanlarının karar sözleşmesi
        activity_match = data.get("faaliyet_eslesmesi")
        decision = data.get("decision")
        negative_overlap = bool(data.get("negatif_kapsam_cakismasi", False))
        activity_uncertainties = data.get("kritik_faaliyet_belirsizlikleri", [])
        if decision == "uygun" and negative_overlap:
            raise ValueError("uygun kararı negatif_kapsam_cakismasi=true ile birlikte kullanılamaz.")
        if decision == "uygun" and activity_match not in {"guclu", "kismi"}:
            raise ValueError("uygun kararı için faaliyet_eslesmesi guclu veya kismi olmalıdır.")
        if decision == "uygun_degil" and activity_match == "guclu" and not negative_overlap:
            raise ValueError("Güçlü faaliyet eşleşmesi ve negatif çakışma yokken uygun_degil kararı gerekçesizdir.")
        if decision == "inceleme_gerekli" and not activity_uncertainties:
            raise ValueError("inceleme_gerekli kararı kritik_faaliyet_belirsizlikleri ile açıklanmalıdır.")

        # F: Check ikincil_profil_kodlari justification
        ikincil_k = data.get("ikincil_profil_kodlari", [])
        if isinstance(ikincil_k, list) and len(ikincil_k) > 0:
            self._validate_ikincil_profil_kodlari(data, ikincil_k)



    def _validate_single_criterion(self, crit: dict, valid_chunk_ids: list[str] | None) -> None:
        if not isinstance(crit, dict):
            raise ValueError("Kriter objesi sözlük (dict) olmalıdır")

        if crit.get("criterion_id") == "string" or "karsilaniyor |" in str(crit.get("status")):
            raise ValueError("Schema echo detected (şablon metinler döndürüldü)")

        status = crit.get("status")
        if status not in ["karsilaniyor", "karsilanmiyor", "bilinmiyor"]:
            raise ValueError(f"Geçersiz kriter durumu (status): {status}")

        crit_id = str(crit.get("criterion_id", ""))
        if crit_id.startswith("chk_"):
            raise ValueError(f"criterion_id alanına ('{crit_id}') parça kimliği yazılamaz.")

        desc = str(crit.get("description", "")).lower()
        expl = str(crit.get("explanation", "")).lower()
        crit_id_lower = crit_id.lower()

        price_adv_phrases = ["fiyat avantajı", "%15 fiyat avantajı", "yerli malı fiyat avantajı", "yerli malı belgesi ile fiyat avantajı", "puan avantajı", "tercih avantajı"]
        for pa in price_adv_phrases:
            if pa in crit_id_lower or pa in desc or pa in expl:
                raise ValueError(f"Fiyat avantajı kriteri eklenemez: '{pa}'")

        if status == "karsilanmiyor":
            missing_phrases = ["bulunmamaktadır", "bulunmuyor", "bulunamadı", "sunulmamıştır", "eksik", "yetersiz", "kanıtlanmamıştır", "doğrulanmamıştır", "detaylandırılmamıştır", "bilgi yok", "veri yok"]
            for mp in missing_phrases:
                if mp in desc or mp in expl:
                    raise ValueError(f"karsilanmiyor durumu için sadece '{mp}' yeterli değildir. Açık negatif kanıt yoksa bilinmiyor olmalıdır.")

        evidence = crit.get("evidence_chunk_ids", [])
        if not evidence or not isinstance(evidence, list) or len(evidence) == 0:
            raise ValueError(f"Zorunlu kriter ({crit.get('criterion_id')}) için evidence_chunk_ids boş olamaz.")

        if status == "karsilaniyor" and not evidence:
            raise ValueError("karsilaniyor olan kriterin evidence_chunk_ids alanı dolu olmalıdır")

        if valid_chunk_ids:
            for cid in evidence:
                if cid not in valid_chunk_ids:
                    raise ValueError(f"Zorunlu kriter ({crit.get('criterion_id')}) için geçersiz chunk_id: {cid}")

    def _validate_ikincil_profil_kodlari(self, data: dict, ikincil_k: list) -> None:
        birincil_k = str(data.get("birincil_profil_kodu", ""))
        if birincil_k in ikincil_k:
            raise ValueError("Birincil profil kodu ikincil_profil_kodlari listesine eklenemez.")

        tum_gerekceler = []
        for field in ["uygunluk_gerekceleri", "uygunsuzluk_gerekceleri", "eksik_kanitlar", "kritik_belirsizlikler", "insan_incelemesi_gerekcesi"]:
            val = data.get(field)
            if isinstance(val, list):
                tum_gerekceler.extend([str(x).lower() for x in val])
            elif isinstance(val, str):
                tum_gerekceler.append(str(val).lower())

        tum_metin = " ".join(tum_gerekceler)

        for kod in ikincil_k:
            kod_lower = str(kod).lower()
            if kod_lower not in tum_metin:
                raise ValueError(f"İkincil profil kodu '{kod}' gerekçelerde desteklenmiyor. Gerekçe yoksa liste boş olmalıdır.")

    def _validate_and_build(self, data: dict[str, Any], primary_profile_code: str, valid_chunk_ids: list[str] | None) -> ModelDecision:
        from app.decision.decision_normalizer import normalize_decision

        decision_raw = str(data.get("decision", "")).strip()
        try:
            decision_val = normalize_decision(decision_raw)
            data["decision"] = decision_val
        except ValueError:
            raise ValueError(f"Geçersiz karar değeri (decision): {decision_raw}")

        confidence = data.get("confidence")
        if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Geçersiz güven değeri (confidence): {confidence}. 0.0 ile 1.0 arasında olmalıdır.")

        birincil_profil_kodu = str(data.get("birincil_profil_kodu", "")).strip().upper()
        if birincil_profil_kodu and primary_profile_code:
            expected_code = primary_profile_code.strip().upper()
            if birincil_profil_kodu != expected_code:
                raise ValueError(f"Geçersiz profil kodu: {birincil_profil_kodu}. İstenen profil ({expected_code}) ile eşleşmiyor.")

        kullanilan_chunk_idleri = data.get("kullanilan_chunk_idleri", [])
        if not isinstance(kullanilan_chunk_idleri, list):
            kullanilan_chunk_idleri = []
        else:
            kullanilan_chunk_idleri = [str(x) for x in kullanilan_chunk_idleri]

        if valid_chunk_ids:
            for cid in kullanilan_chunk_idleri:
                if cid not in valid_chunk_ids:
                    raise ValueError(f"Uydurma veya geçersiz chunk_id kullanıldı: {cid}")

        criteria = []
        raw_criteria = data.get("zorunlu_kriter_sonuclari", [])
        if not isinstance(raw_criteria, list):
            raise ValueError("zorunlu_kriter_sonuclari liste olmalıdır")

        for crit in raw_criteria:
            # We assume it has already passed _validate_single_criterion.
            # But just to be safe, we extract fields safely.
            if not isinstance(crit, dict):
                continue
            status = crit.get("status")
            evidence = crit.get("evidence_chunk_ids", [])
            if not isinstance(evidence, list):
                evidence = []
            evidence = [str(e) for e in evidence]

            if valid_chunk_ids:
                for cid in evidence:
                    if cid not in valid_chunk_ids:
                        raise ValueError(f"Kriter ({crit.get('criterion_id')}) içinde uydurma chunk_id: {cid}")

            criteria.append(
                CriterionResult(
                    criterion_id=str(crit.get("criterion_id", "")),
                    description=str(crit.get("description", "")),
                    status=status,
                    evidence_chunk_ids=evidence,
                    explanation=str(crit.get("explanation", "")),
                )
            )

        suitable_parts: list[SuitableTenderPart] = []
        raw_parts = data.get("uygun_kisimlar", [])
        if not isinstance(raw_parts, list):
            raise ValueError("uygun_kisimlar liste olmalıdır")
        for part in raw_parts:
            if not isinstance(part, dict):
                continue
            evidence = part.get("evidence_chunk_ids", [])
            if not isinstance(evidence, list):
                evidence = []
            evidence = [str(chunk_id).strip() for chunk_id in evidence if str(chunk_id).strip()]
            if valid_chunk_ids:
                for chunk_id in evidence:
                    if chunk_id not in valid_chunk_ids:
                        raise ValueError(
                            "Uygun kısım içinde uydurma chunk_id kullanıldı: "
                            f"{chunk_id}"
                        )
            suitable_parts.append(
                SuitableTenderPart(
                    part_number=str(part.get("kisim_no") or "").strip(),
                    part_name=str(part.get("kisim_adi") or "").strip(),
                    evidence_chunk_ids=evidence,
                    reason=str(part.get("gerekce") or "").strip(),
                )
            )

        return ModelDecision(
            model_name=self.name,
            decision=decision_val,
            confidence=float(confidence),
            birincil_profil_kodu=birincil_profil_kodu,
            ikincil_profil_kodlari=data.get("ikincil_profil_kodlari", []),
            uygunluk_gerekceleri=data.get("uygunluk_gerekceleri", []),
            uygunsuzluk_gerekceleri=data.get("uygunsuzluk_gerekceleri", []),
            zorunlu_kriter_sonuclari=criteria,
            faaliyet_eslesmesi=data.get("faaliyet_eslesmesi", "belirsiz"),
            negatif_kapsam_cakismasi=bool(data.get("negatif_kapsam_cakismasi", False)),
            katilim_yeterliligi_durumu=data.get("katilim_yeterliligi_durumu", "dogrulanmadi"),
            kritik_faaliyet_belirsizlikleri=data.get("kritik_faaliyet_belirsizlikleri", []),
            dogrulanamayan_katilim_sartlari=data.get("dogrulanamayan_katilim_sartlari", []),
            uygun_kisimlar=suitable_parts,
            eksik_kanitlar=data.get("eksik_kanitlar", []),
            kritik_belirsizlikler=data.get("kritik_belirsizlikler", []),
            kullanilan_chunk_idleri=kullanilan_chunk_idleri,
            kaynak_disinda_bilgi_var_mi=bool(data.get("kaynak_disinda_bilgi_var_mi", False)),
            insan_incelemesi_gerekcesi=str(data.get("insan_incelemesi_gerekcesi", "")),
            raw_response=data,
        )
