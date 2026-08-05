import json
from pathlib import Path

# Yeni profil sözlükleri
SEED_DATA = {
    "AUS-01": {
        "description_expanded": "Kavşak sinyalizasyonu, trafik kontrol merkezleri ve yaya güvenliğini artırmaya yönelik akıllı sistemlerin tasarımı, kurulumu ve yönetimidir. Dinamik, uyarlamalı ve yeşil dalga gibi trafik iyileştirici yönetim stratejilerini destekler. Sensör ve kameralardan alınan anlık verilerle trafiğin merkezden yönetilmesini sağlayarak standart sinyalizasyondan ayrışır.",
        "abbreviations_and_jargon": [
            {"term": "TKM", "expanded_form": "Trafik Kontrol Merkezi", "aliases": [], "ambiguous": False, "usage_context": "Kavşakların ve trafik akışının merkezden izlenip yönetildiği tesis veya yazılım tanımlarında."},
            {"term": "Dinamik Kavşak", "expanded_form": "Uyarlamalı Kavşak Yönetim Sistemi", "aliases": ["Akıllı Kavşak"], "ambiguous": False, "usage_context": "Trafik yoğunluğuna göre sinyal sürelerinin anlık olarak değiştirildiği akıllı sistem ihalelerinde."}
        ],
        "technical_equipment": [
            {"name": "Sinyal Denetleyici", "category": "donanım", "aliases": ["Kavşak Kontrol Cihaz"]},
            {"name": "Sinyalizasyon Direği", "category": "altyapı", "aliases": ["Oto Direk", "Yaya Direk"]},
            {"name": "Sinyal Optiği", "category": "donanım", "aliases": ["Sinyal Lambası"]},
            {"name": "Trafik Yönetim Yazılımı", "category": "yazılım", "aliases": ["Kavşak Yönetim Sistemi"]},
            {"name": "Kavşak Kontrol Modülü", "category": "donanım", "aliases": ["Sinyal Modülü"]},
            {"name": "Araç Algılama Sensörü", "category": "sensör", "aliases": ["Manyetik Dedektör"]},
            {"name": "Trafik Veri Toplama", "category": "metodoloji", "aliases": ["Trafik Akım Analizi"]},
            {"name": "Merkezi Kontrol Entegrasyonu", "category": "metodoloji", "aliases": ["TKM Entegrasyonu"]},
            {"name": "Yeşil Dalga Tasarımı", "category": "metodoloji", "aliases": ["Koordineli Kavşak"]},
            {"name": "Sinyalizasyon Projesi", "category": "teslim_ciktisi", "aliases": ["Kavşak Projesi"]}
        ],
        "action_verbs": ["sinyalizasyon sistemi kurulumu", "kavşak kontrol cihazı montajı", "trafik yönetim yazılımı entegrasyonu", "kablolama ve altyapı tesisi", "yeşil dalga optimizasyonu", "akıllı kavşak devreye alınması", "sistem bakım ve onarımı", "merkezi kontrol entegrasyonu"]
    },
    "AUS-02": {
        "description_expanded": "Trafik kural ihlallerini tespit eden, yol güvenliğini sağlayan ve elektronik denetim amaçlı kamera ile hız radar sistemlerinin kurulumunu içerir. Elektronik Denetleme Sistemleri (EDS) standartlarına uygun olarak tasarlanan bu çözümler, salt kamera izlemesinden farklı olarak yasal ceza işlemlerine zemin hazırlayan kesin delil üreten yapıdadır.",
        "abbreviations_and_jargon": [
            {"term": "EDS", "expanded_form": "Elektronik Denetleme Sistemi", "aliases": ["Elektronik Denetim Sistemi"], "ambiguous": False, "usage_context": "Hız, kırmızı ışık veya park ihlallerini tespit eden ve yasal kanıt üreten sistem şartnamelerinde."},
            {"term": "PTS", "expanded_form": "Plaka Tanıma Sistemi", "aliases": ["LPR", "ALPR"], "ambiguous": False, "usage_context": "Araç plakalarının kameralar aracılığıyla okunup kaydedildiği donanım veya yazılım isterlerinde."}
        ],
        "technical_equipment": [
            {"name": "Kırmızı Işık İhlal Tespit Sistemi", "category": "donanım", "aliases": ["Kırmızı Işık Kamerası"]},
            {"name": "Hız İhlal Tespit Sistemi", "category": "donanım", "aliases": ["Radar", "Ortalama Hız Sistemi"]},
            {"name": "PTS Kamerası", "category": "donanım", "aliases": ["Plaka Okuma Kamerası"]},
            {"name": "İhlal Analiz Yazılımı", "category": "yazılım", "aliases": ["Ceza Yazılımı"]},
            {"name": "Plaka Okuma Yazılımı", "category": "yazılım", "aliases": ["ALPR Yazılımı"]},
            {"name": "Kalibrasyon Hizmeti", "category": "hizmet", "aliases": ["Sistem Kalibrasyonu"]},
            {"name": "Radar Sensörü", "category": "sensör", "aliases": ["Hız Sensörü"]},
            {"name": "EDS Direği", "category": "altyapı", "aliases": ["Kamera Direği", "Tag"]},
            {"name": "Polnet Entegrasyonu", "category": "metodoloji", "aliases": ["EGM Entegrasyonu"]}
        ],
        "action_verbs": ["elektronik denetleme sistemi kurulumu", "plaka tanıma sistemi montajı", "hız ihlal tespit sistemi kurulumu", "kırmızı ışık kamerası kalibrasyonu", "ihlal kayıt sistemi devreye alınması", "merkezi sunucu entegrasyonu", "sistem periyodik bakımı"]
    },
    "AUS-03": {
        "description_expanded": "Yol ağındaki trafik yoğunluğunu ve meteorolojik verileri ölçen, sürücüleri bilgilendirme panelleriyle yönlendiren sistemlerdir. Otopark doluluk durumlarının tespiti ve gösterimi de bu kapsamdadır. Salt trafik ışığı yönetiminden farklı olarak sürücü davranışını dolaylı yoldan (bilgi vererek) iyileştirmeyi hedefler.",
        "abbreviations_and_jargon": [
            {"term": "DMS", "expanded_form": "Değişken Mesaj İşareti", "aliases": ["VMS"], "ambiguous": False, "usage_context": "Sürücüleri metin veya grafik ile bilgilendiren otoyol üzeri elektronik LED panoların şartnamelerinde."},
            {"term": "TTS", "expanded_form": "Trafik Ölçüm Sistemi", "aliases": [], "ambiguous": False, "usage_context": "Yoldaki araç sayısını, hızını ve sınıfını sensörlerle tespit eden veri toplama sistemlerinde."}
        ],
        "technical_equipment": [
            {"name": "Araç Sayım Sensörü", "category": "sensör", "aliases": ["Trafik Sensörü"]},
            {"name": "DMS Paneli", "category": "donanım", "aliases": ["VMS Ekran", "Bilgilendirme Panosu"]},
            {"name": "Otopark Doluluk Sensörü", "category": "sensör", "aliases": ["Park Sensörü", "Yer Sensörü"]},
            {"name": "Bilgilendirme Ekranı Yazılımı", "category": "yazılım", "aliases": ["DMS Yönetim Yazılımı"]},
            {"name": "Meteorolojik Sensör", "category": "sensör", "aliases": ["Hava Durumu Sensörü"]},
            {"name": "Bluetooth Trafik Analizörü", "category": "sensör", "aliases": ["MAC Sensörü"]},
            {"name": "Görüntü Tabanlı Sayım", "category": "metodoloji", "aliases": ["Video Sayım"]},
            {"name": "Otopark Yönlendirme Panosu", "category": "donanım", "aliases": ["Otopark Ekranı"]},
            {"name": "Trafik Verisi Raporlama", "category": "teslim_ciktisi", "aliases": ["Veri Analiz Raporu"]}
        ],
        "action_verbs": ["değişken mesaj işareti kurulumu", "trafik ölçüm sistemi tesisi", "otopark yönlendirme sistemi montajı", "sensör veri toplama altyapısı", "bilgilendirme ekranı devreye alınması", "trafik veri analizi yapılması"]
    },
    "AUS-04": {
        "description_expanded": "Toplu taşıma hatlarının, otobüs duraklarının ve raylı sistemlerin yönetimini sağlayan, yolcu bilgilendirme ve araç içi izleme bileşenlerini barındıran çözümlerdir. Bireysel araç trafiğinden ziyade filo, durak ve yolcu odaklı hizmetlerin dijitalleştirilmesini temsil eder.",
        "abbreviations_and_jargon": [
            {"term": "YBS", "expanded_form": "Yolcu Bilgilendirme Sistemi", "aliases": ["Yolcu Ekranı"], "ambiguous": False, "usage_context": "Duraklarda veya araç içinde güzergah, varış zamanı gibi bilgileri gösteren ekran tanımlarında."},
            {"term": "Akıllı Durak", "expanded_form": "Gerçek zamanlı yolcu bilgilendirmesi yapabilen durak", "aliases": [], "ambiguous": False, "usage_context": "Güneş enerjili veya şebeke bağlantılı, ekranlı toplu taşıma durağı projelerinde."}
        ],
        "technical_equipment": [
            {"name": "Yolcu Bilgilendirme Ekranı", "category": "donanım", "aliases": ["Durak Ekranı", "YBS Ekranı"]},
            {"name": "Araç İçi Bilgilendirme Ekranı", "category": "donanım", "aliases": ["Otobüs Ekranı"]},
            {"name": "Durak Yönetim Yazılımı", "category": "yazılım", "aliases": ["Akıllı Durak Yazılımı"]},
            {"name": "Araç İçi Bilgisayar", "category": "donanım", "aliases": ["Validatör Bağlantılı PC"]},
            {"name": "Hat ve Güzergah Planlama", "category": "metodoloji", "aliases": ["Rota Optimizasyonu"]},
            {"name": "Yolcu Sayım Sensörü", "category": "sensör", "aliases": ["Kişi Sayım Kamerası"]},
            {"name": "Anons Sistemi", "category": "donanım", "aliases": ["Sesli Bilgilendirme"]},
            {"name": "Raylı Sistem YBS Entegrasyonu", "category": "metodoloji", "aliases": ["Metro Ekran Entegrasyonu"]},
            {"name": "Durak Altyapı İmalatı", "category": "altyapı", "aliases": ["Ankraj ve Direk"]}
        ],
        "action_verbs": ["yolcu bilgilendirme ekranı montajı", "akıllı durak sistemi kurulumu", "araç içi yolcu bilgilendirme devreye alınması", "hat güzergah planlaması yapılması", "durak altyapısı tesisi", "sesli anons sistemi entegrasyonu"]
    },
    "ENT-01": {
        "description_expanded": "Kritik tesislerin, kampüslerin veya şehirlerin tek bir merkezden izlenip yönetildiği, donanımların ve yazılımların entegre çalıştığı operasyon merkezlerinin tasarımıdır. Alt sistemlerin (güvenlik, trafik, acil durum) üst çatıda (VMS, PSIM) toplanması ve büyük görüntü duvarlarıyla sunulması bu profilin ayırt edici özelliğidir.",
        "abbreviations_and_jargon": [
            {"term": "Video Wall", "expanded_form": "Görüntü Duvarı", "aliases": ["Ekran Duvarı"], "ambiguous": False, "usage_context": "Operasyon merkezlerinde kullanılan çoklu ekranlardan oluşan büyük izleme panellerinde."}
        ],
        "technical_equipment": [
            {"name": "Görüntü Duvarı", "category": "donanım", "aliases": ["Video Wall"]},
            {"name": "Operatör Konsolu", "category": "donanım", "aliases": ["Masa ve Kontrol Ünitesi"]},
            {"name": "Komuta Kontrol Yazılımı", "category": "yazılım", "aliases": ["PSIM", "Şemsiye Yazılım"]},
            {"name": "VMS Yazılımı", "category": "yazılım", "aliases": ["Video Yönetim Sistemi"]},
            {"name": "Merkezi Sunucu Sistemi", "category": "donanım", "aliases": ["Kayıt Sunucusu"]},
            {"name": "Ergonomik Operasyon Odası", "category": "altyapı", "aliases": ["Kontrol Odası Tasarımı"]},
            {"name": "Sistemler Arası Entegrasyon", "category": "metodoloji", "aliases": ["Alt Sistem Entegrasyonu"]},
            {"name": "Operatör Eğitimi", "category": "hizmet", "aliases": ["Kullanıcı Eğitimi"]},
            {"name": "Kontrol Merkezi Projesi", "category": "teslim_ciktisi", "aliases": ["Mimari Tasarım"]}
        ],
        "action_verbs": ["komuta kontrol merkezi kurulumu", "görüntü duvarı montajı", "operatör konsolu tesisi", "video yönetim sistemi entegrasyonu", "merkezi izleme yazılımı yapılandırması", "kontrol odası tasarımı yapılması"]
    },
    "ENT-02": {
        "description_expanded": "Bina veya çevre güvenliğini sağlamak amacıyla IP kamera sistemleri, video analiz algoritmaları ve ağ üzerinden video kayıt çözümlerinin projelendirilmesini kapsar. Yalnızca kamerasel izleme değil, akıllı tespit (yüz tanıma, sınır ihlali) gibi video analitik yeteneklerini de içermesiyle basit güvenlik işlerinden ayrılır.",
        "abbreviations_and_jargon": [
            {"term": "CCTV", "expanded_form": "Kapalı Devre Televizyon", "aliases": ["Güvenlik Kamerası Sistemi"], "ambiguous": False, "usage_context": "Bina içi veya dışı genel alan güvenlik amaçlı izleme sistemi şartnamelerinde."},
            {"term": "NVR", "expanded_form": "Ağ Video Kaydedici", "aliases": [], "ambiguous": False, "usage_context": "IP kameralardan gelen görüntüleri ağ üzerinden kaydeden sunucu/cihaz tanımlarında."}
        ],
        "technical_equipment": [
            {"name": "IP Güvenlik Kamerası", "category": "donanım", "aliases": ["Dome Kamera", "Bullet Kamera", "PTZ"]},
            {"name": "Video Kayıt Cihazı", "category": "donanım", "aliases": ["NVR", "Kayıt Sunucusu"]},
            {"name": "Video Analitik Yazılımı", "category": "yazılım", "aliases": ["Akıllı Video Analiz"]},
            {"name": "Ağ İletişim Altyapısı", "category": "altyapı", "aliases": ["Kamera Ağ Altyapısı"]},
            {"name": "Kayıt Depolama Ünitesi", "category": "donanım", "aliases": ["Storage", "NAS", "SAN"]},
            {"name": "Yüz Tanıma Lisansı", "category": "yazılım", "aliases": []},
            {"name": "Çevre Güvenlik Analitiği", "category": "metodoloji", "aliases": ["Sınır İhlali Tespiti"]},
            {"name": "Görüntü İzleme Yazılımı", "category": "yazılım", "aliases": ["Client Yazılımı"]},
            {"name": "CCTV Sistem Testi", "category": "metodoloji", "aliases": ["Görüntü Kalibrasyonu"]}
        ],
        "action_verbs": ["kapalı devre kamera sistemi kurulumu", "ip kamera montajı", "video kayıt cihazı yapılandırması", "video analitik yazılımı entegrasyonu", "kamera ağ altyapısı tesisi", "görüntü izleme sistemi devreye alınması"]
    },
    "ENT-03": {
        "description_expanded": "Araç filolarının GPS tabanlı uzaktan takibini sağlayan, saha personelinin operasyonel iş akışlarını dijitalleştiren araç takip ve telemetri çözümleridir. Araçların anlık konumu, yakıt tüketimi veya görev durumu gibi verilerin merkezi yazılıma aktarılmasına odaklanır.",
        "abbreviations_and_jargon": [
            {"term": "ATS", "expanded_form": "Araç Takip Sistemi", "aliases": ["Araç Takip Cihazı"], "ambiguous": False, "usage_context": "Filo araçlarına takılarak konum ve sensör verilerini merkeze ileten sistemlerde."}
        ],
        "technical_equipment": [
            {"name": "Araç Takip Cihazı", "category": "donanım", "aliases": ["GPS Cihazı"]},
            {"name": "Telemetri Sensörü", "category": "sensör", "aliases": ["Araç Beyin Sensörü", "CANbus Modülü"]},
            {"name": "Filo Yönetim Yazılımı", "category": "yazılım", "aliases": ["Araç Takip Yazılımı"]},
            {"name": "Mobil Takip Uygulaması", "category": "yazılım", "aliases": ["Personel Mobil Uygulaması"]},
            {"name": "Yakıt Seviye Sensörü", "category": "sensör", "aliases": ["Depo Sensörü"]},
            {"name": "Sıcaklık ve Nem Sensörü", "category": "sensör", "aliases": ["Araç İçi Sensör"]},
            {"name": "Haberleşme Sim Kartı", "category": "haberleşme", "aliases": ["M2M Hat"]},
            {"name": "Rota Optimizasyonu", "category": "metodoloji", "aliases": ["Güzergah Planlama"]},
            {"name": "Takip Verisi Raporu", "category": "teslim_ciktisi", "aliases": ["Filo Performans Raporu"]}
        ],
        "action_verbs": ["araç takip cihazı montajı", "filo yönetim yazılımı kurulumu", "telemetri sensörü entegrasyonu", "araç konumlandırma sistemi devreye alınması", "yakıt ve ısı takibi yapılması", "mobil saha uygulaması geliştirilmesi"]
    },
    "ENT-04": {
        "description_expanded": "Kurum binalarında personel veya ziyaretçilerin geçiş yetkilerini elektronik donanımlarla kontrol eden kartlı, biyometrik veya turnike tabanlı fiziksel erişim sistemleridir. Yalnızca kapı kilidinden ziyade, yetkilendirme ağaçları ve IK (İnsan Kaynakları) yazılımlarıyla entegrasyon öne çıkar.",
        "abbreviations_and_jargon": [
            {"term": "PDKS", "expanded_form": "Personel Devam Kontrol Sistemi", "aliases": ["Geçiş Kontrol"], "ambiguous": False, "usage_context": "Personelin mesai saatlerini, giriş çıkışlarını takip eden ve raporlayan yazılım ve donanım bütününde."}
        ],
        "technical_equipment": [
            {"name": "Geçiş Turnikesi", "category": "donanım", "aliases": ["Bel Tipi Turnike", "Hızlı Geçiş Turnikesi", "Boy Turnikesi"]},
            {"name": "Kart Okuyucu", "category": "donanım", "aliases": ["Mifare Okuyucu", "Proximity"]},
            {"name": "Biyometrik Okuyucu", "category": "donanım", "aliases": ["Parmak İzi", "Yüz Tanıma", "Damar Tanıma"]},
            {"name": "PDKS Yazılımı", "category": "yazılım", "aliases": ["Geçiş Kontrol Yazılımı"]},
            {"name": "Manyetik Kilit", "category": "donanım", "aliases": ["Kapı Kilit Karşılığı"]},
            {"name": "Erişim Denetim Paneli", "category": "donanım", "aliases": ["Kontrol Paneli"]},
            {"name": "Geçiş Kontrol Altyapısı", "category": "altyapı", "aliases": ["Turnike Altyapısı"]},
            {"name": "İK Sistem Entegrasyonu", "category": "metodoloji", "aliases": ["Bordro Entegrasyonu"]},
            {"name": "Ziyaretçi Yönetim Modülü", "category": "yazılım", "aliases": ["Ziyaretçi Takip"]}
        ],
        "action_verbs": ["geçiş kontrol sistemi kurulumu", "turnike montajı", "personel devam kontrol yazılımı entegrasyonu", "biyometrik okuyucu devreye alınması", "manyetik kilit altyapısı tesisi", "ziyaretçi yönetim sistemi yapılandırması"]
    },
    "ENT-05": {
        "description_expanded": "Şehir içi ve tesis aydınlatmalarının uzaktan kontrol edilerek enerji tüketiminin azaltılmasını, arızaların anlık izlenmesini ve bakım sürelerinin kısaltılmasını sağlayan projelerdir. Geleneksel aydınlatmadan farklı olarak haberleşme ağları üzerinden merkezle konuşan sensör/düğüm (node) odaklı akıllı sistemlerdir.",
        "abbreviations_and_jargon": [],
        "technical_equipment": [
            {"name": "Akıllı Aydınlatma Armatürü", "category": "donanım", "aliases": ["LED Armatür"]},
            {"name": "Aydınlatma Kontrol Düğümü", "category": "donanım", "aliases": ["Node", "NEMA Soket Uyumlu Modül"]},
            {"name": "Aydınlatma Otomasyon Yazılımı", "category": "yazılım", "aliases": ["Merkezi İzleme Yazılımı"]},
            {"name": "Ortam Işığı Sensörü", "category": "sensör", "aliases": ["Aydınlık Sensörü"]},
            {"name": "Akıllı Elektrik Panosu", "category": "donanım", "aliases": ["Kontrol Panosu"]},
            {"name": "IoT Haberleşme Ağı", "category": "haberleşme", "aliases": ["LoRa", "NB-IoT", "ZigBee"]},
            {"name": "Enerji Tüketim Analizi", "category": "metodoloji", "aliases": ["Tasarruf Ölçümü"]},
            {"name": "Karartma Modülü", "category": "donanım", "aliases": ["Dimmer"]},
            {"name": "Tasarruf Doğrulama Raporu", "category": "teslim_ciktisi", "aliases": ["Enerji Raporu"]}
        ],
        "action_verbs": ["akıllı aydınlatma armatürü montajı", "aydınlatma kontrol modülü kurulumu", "enerji tüketim izleme sistemi entegrasyonu", "otomasyon panosu tesisi", "aydınlatma senaryosu yapılandırması", "iot haberleşme altyapısı kurulumu"]
    },
    "ENT-06": {
        "description_expanded": "Akıllı şehir konsepti içerisinde hava kalitesi, gürültü, nem gibi çevresel verileri toplayıp merkeze ileten çevre donatılarını ve sensör ağlarını içerir. Şehrin farklı noktalarındaki küçük nesnelerin (IoT) internet ağı üzerinden veri akışı sağlaması temel mantığıdır.",
        "abbreviations_and_jargon": [
            {"term": "IoT", "expanded_form": "Nesnelerin İnterneti", "aliases": [], "ambiguous": False, "usage_context": "Sensörlerin, akıllı direklerin veya çevresel ölçüm cihazlarının uzaktan merkeze veri aktardığı ağ tanımlarında."}
        ],
        "technical_equipment": [
            {"name": "Hava Kalitesi Sensörü", "category": "sensör", "aliases": ["Gaz Sensörü", "PM Ölçüm"]},
            {"name": "Gürültü Sensörü", "category": "sensör", "aliases": ["Akustik Sensör"]},
            {"name": "Akıllı Direk", "category": "donanım", "aliases": ["Smart Pole"]},
            {"name": "Çevresel Veri İzleme Yazılımı", "category": "yazılım", "aliases": ["Sensör Yönetim Platformu"]},
            {"name": "Meteoroloji İstasyonu", "category": "donanım", "aliases": ["Mini İstasyon"]},
            {"name": "Kablosuz Sensör Ağı", "category": "haberleşme", "aliases": ["WSN"]},
            {"name": "Atık Doluluk Sensörü", "category": "sensör", "aliases": ["Çöp Sensörü"]},
            {"name": "Çevre Verisi Analitiği", "category": "metodoloji", "aliases": ["Veri Anlamlandırma"]},
            {"name": "IoT Ağ Geçidi", "category": "donanım", "aliases": ["Gateway"]}
        ],
        "action_verbs": ["çevresel sensör kurulumu", "akıllı direk montajı", "iot ağ geçidi entegrasyonu", "hava kalitesi ölçüm sistemi devreye alınması", "kablosuz sensör ağı tesisi", "merkezi veri izleme platformu yapılandırması"]
    },
    "PLN-01": {
        "description_expanded": "Şehir ve ulaşım ağlarının gelecekteki gereksinimlerini belirlemek amacıyla ulaşım master planları (UMP) hazırlanması, makro seviyede talep tahmin modellerinin oluşturulmasıdır. Donanım kurulumu yerine etüt, analiz, modelleme ve raporlama gibi müşavirlik hizmetleri ağırlıklıdır.",
        "abbreviations_and_jargon": [
            {"term": "UMP", "expanded_form": "Ulaşım Master Planı", "aliases": [], "ambiguous": False, "usage_context": "Bir şehrin yıllara sari olarak ulaşım stratejilerini ve yatırım planlarını belirleyen büyük ölçekli etüt projelerinde."}
        ],
        "technical_equipment": [
            {"name": "Ulaşım Talep Tahmin Modeli", "category": "metodoloji", "aliases": ["Makro Simülasyon", "Dört Aşamalı Model"]},
            {"name": "Talep Modelleme Yazılımı", "category": "yazılım", "aliases": ["Visum", "TransCAD", "Cube"]},
            {"name": "Hanehalkı Anketi", "category": "metodoloji", "aliases": ["Saha Anketi"]},
            {"name": "Trafik Sayım Etüdü", "category": "metodoloji", "aliases": ["Kesit Sayımı"]},
            {"name": "Ulaşım Master Planı Raporu", "category": "teslim_ciktisi", "aliases": ["UMP Çıktısı"]},
            {"name": "Otopark Master Planı", "category": "teslim_ciktisi", "aliases": ["Otopark Raporu"]},
            {"name": "Lojistik Master Planı", "category": "teslim_ciktisi", "aliases": ["Lojistik Raporu"]},
            {"name": "Veri Toplama Hizmeti", "category": "hizmet", "aliases": ["Saha Etüdü"]}
        ],
        "action_verbs": ["ulaşım master planı hazırlanması", "talep tahmin modeli oluşturulması", "hanehalkı anketi yapılması", "trafik etüt ve sayım hizmeti alınması", "lojistik planlama yapılması", "ulaşım strateji raporu sunulması"]
    },
    "PLN-02": {
        "description_expanded": "Belirli bir kavşak, arter veya koridor ölçeğinde mikro seviyede trafik kapasite analizlerinin, geometrik düzenleme tasarımlarının ve bilgisayar destekli trafik simülasyonlarının yapılmasıdır. Fiziksel cihaz kurulumundan ziyade mevcut kavşaklardaki darboğazların mühendislik yöntemleriyle çözümlenmesine dayanır.",
        "abbreviations_and_jargon": [
            {"term": "Mikrosimülasyon", "expanded_form": "Araç bazlı detaylı trafik akım simülasyonu", "aliases": [], "ambiguous": False, "usage_context": "Bireysel araç davranışlarının, hızlarının ve sinyal gecikmelerinin model programlarında analiz edildiği trafik mühendisliği işlerinde."}
        ],
        "technical_equipment": [
            {"name": "Trafik Simülasyon Çıktısı", "category": "teslim_ciktisi", "aliases": ["VISSIM Çıktısı", "AIMSUN Analizi"]},
            {"name": "Kapasite Analiz Raporu", "category": "teslim_ciktisi", "aliases": ["Gecikme Analizi"]},
            {"name": "Mikrosimülasyon Yazılımı", "category": "yazılım", "aliases": ["Vissim", "Aimsun"]},
            {"name": "Geometrik Düzenleme Projesi", "category": "teslim_ciktisi", "aliases": ["Kavşak Çizimi"]},
            {"name": "Kavşak Kapasite Analizi", "category": "metodoloji", "aliases": ["Seviye Analizi", "LOS"]},
            {"name": "Sinyal Süre Optimizasyonu", "category": "metodoloji", "aliases": ["Sinyal Planlama"]},
            {"name": "Trafik Sayım Analizi", "category": "metodoloji", "aliases": ["Zirve Saat Analizi"]},
            {"name": "Trafik Sirkülasyon Planı", "category": "teslim_ciktisi", "aliases": ["Dolaşım Planı"]}
        ],
        "action_verbs": ["trafik mikrosimülasyon modeli kurulması", "kavşak geometrik tasarımı yapılması", "kapasite analiz raporu hazırlanması", "sinyal optimizasyonu yapılması", "trafik sirkülasyon projesi çizilmesi", "araç gecikme etüdü yapılması"]
    },
    "PLN-03": {
        "description_expanded": "Toplanan ulaşım, altyapı veya donanım verilerinin harita üzerinde sayısallaştırılması, coğrafi mekânsal veri tabanlarının kurulması ve CBS tabanlı harita analizlerinin yapılması süreçleridir. Konum tabanlı verilerin işlenerek dijital harita katmanlarına dönüştürülmesi ön plandadır.",
        "abbreviations_and_jargon": [
            {"term": "CBS", "expanded_form": "Coğrafi Bilgi Sistemleri", "aliases": ["GIS"], "ambiguous": False, "usage_context": "Mekânsal veya konumsal verilerin (koordinatlı objelerin) harita tabanlı yazılımlarda depolanıp analiz edildiği projelerde."}
        ],
        "technical_equipment": [
            {"name": "Mekânsal Veritabanı", "category": "teslim_ciktisi", "aliases": ["Geodatabase"]},
            {"name": "Sayısal Harita Üretimi", "category": "teslim_ciktisi", "aliases": ["Harita Çıktısı"]},
            {"name": "CBS Masaüstü Yazılımı", "category": "yazılım", "aliases": ["ArcGIS", "QGIS"]},
            {"name": "CBS Sunucu Yazılımı", "category": "yazılım", "aliases": ["GIS Server", "GeoServer"]},
            {"name": "Veri Sayısallaştırma", "category": "metodoloji", "aliases": ["Digitization"]},
            {"name": "Mekânsal Analiz", "category": "metodoloji", "aliases": ["Konumsal Analiz"]},
            {"name": "Ağ (Network) Analizi", "category": "metodoloji", "aliases": ["Rota Analizi"]},
            {"name": "Web CBS Portalı", "category": "teslim_ciktisi", "aliases": ["WebGIS Uygulaması"]},
            {"name": "Mobil CBS Uygulaması", "category": "teslim_ciktisi", "aliases": ["Saha Veri Toplama Uygulaması"]}
        ],
        "action_verbs": ["coğrafi bilgi sistemi yazılımı geliştirilmesi", "mekânsal veritabanı tasarımı yapılması", "sayısal harita verisi üretilmesi", "cbs portalı devreye alınması", "saha verisi sayısallaştırılması", "mekânsal analiz raporu hazırlanması"]
    },
    "TEK-01": {
        "description_expanded": "Mevcut sistemleri birbirine bağlayan, farklı kurum veritabanlarını konuşturan ve yeni web/mobil tabanlı yazılım arayüzleri oluşturan saf yazılım geliştirme yetkinliklerini kapsar. Fiziksel donanım montajından bağımsız olarak, kodlama, mimari tasarım ve arka plan veri tabanı optimizasyonu gerektiren yazılım işleridir.",
        "abbreviations_and_jargon": [
            {"term": "API", "expanded_form": "Uygulama Programlama Arayüzü", "aliases": ["Web Servis"], "ambiguous": False, "usage_context": "Farklı yazılım sistemlerinin, sunucuların birbirleriyle standart protokollerle veri alışverişi yapmasını sağlayan entegrasyon arayüzlerinde."}
        ],
        "technical_equipment": [
            {"name": "Özel Yazılım Kaynak Kodu", "category": "teslim_ciktisi", "aliases": ["Source Code"]},
            {"name": "Sistem Entegrasyonu", "category": "metodoloji", "aliases": ["Bütünleştirme", "API Geliştirme"]},
            {"name": "Veritabanı Yönetim Sistemi", "category": "yazılım", "aliases": ["DBMS", "SQL Sunucusu"]},
            {"name": "Mobil Uygulama Projesi", "category": "teslim_ciktisi", "aliases": ["iOS/Android Uygulama"]},
            {"name": "Web Tabanlı Uygulama", "category": "teslim_ciktisi", "aliases": ["Web Portalı"]},
            {"name": "Yazılım Mimari Tasarımı", "category": "metodoloji", "aliases": ["Sistem Mimarisi"]},
            {"name": "Veri Ambarı Modeli", "category": "metodoloji", "aliases": ["Data Warehouse"]},
            {"name": "Büyük Veri (Big Data) Platformu", "category": "yazılım", "aliases": ["Hadoop", "Spark"]},
            {"name": "Yazılım Test Dokümanı", "category": "teslim_ciktisi", "aliases": ["UAT Raporu"]}
        ],
        "action_verbs": ["özel yazılım geliştirilmesi", "sistem entegrasyonu sağlanması", "web tabanlı portal oluşturulması", "mobil uygulama kodlanması", "veritabanı mimarisi tasarlanması", "yazılım test ve kabul işlemleri", "api web servis entegrasyonu"]
    },
    "TEK-02": {
        "description_expanded": "Sıfırdan elektronik kart (PCB) tasarımı yapılması, bu kartlara özel gömülü sistem yazılımlarının kodlanması ve endüstriyel standartlarda donanım üretimi süreçleridir. Hazır (COTS) bir ürünün alınıp takılmasından farklı olarak ürünün bizzat Ar-Ge ve imalat aşamalarını içerir.",
        "abbreviations_and_jargon": [
            {"term": "Gömülü Sistem", "expanded_form": "Sadece belirli bir işlevi yerine getirmek üzere donanıma gömülü yazılımla çalışan sistem", "aliases": ["Embedded System"], "ambiguous": False, "usage_context": "Mikrodenetleyici programlanmasını veya özel elektronik kart üretimini içeren, donanım-yazılım bütünleşik Ar-Ge işlerinde."}
        ],
        "technical_equipment": [
            {"name": "Özel Tasarım Elektronik Kart", "category": "teslim_ciktisi", "aliases": ["PCB", "Devre Kartı"]},
            {"name": "Gömülü Sistem Yazılımı", "category": "teslim_ciktisi", "aliases": ["Firmware"]},
            {"name": "Mikrodenetleyici/Mikroişlemci", "category": "donanım", "aliases": ["MCU", "SoC"]},
            {"name": "Elektronik Devre Tasarımı", "category": "metodoloji", "aliases": ["Donanım Tasarımı"]},
            {"name": "Endüstriyel Kasa ve Mekanik", "category": "donanım", "aliases": ["Cihaz Kutusu", "IP65 Kasa"]},
            {"name": "Donanım Çevresel Testleri", "category": "metodoloji", "aliases": ["EMI/EMC Testi", "Sıcaklık Testi"]},
            {"name": "Seri Üretim", "category": "metodoloji", "aliases": ["Donanım İmalatı"]},
            {"name": "Prototip Cihaz", "category": "teslim_ciktisi", "aliases": ["Ar-Ge Prototipi"]}
        ],
        "action_verbs": ["elektronik devre kartı tasarımı yapılması", "gömülü sistem yazılımı kodlanması", "donanım prototipi üretilmesi", "endüstriyel cihaz imalatı", "emi/emc çevresel donanım testleri yapılması", "mekanik tasarım ve montaj yapılması"]
    },
    "TEK-03": {
        "description_expanded": "Saha donanımlarının ve sunucuların kesintisiz iletişim kurmasını sağlamak amacıyla kurulan yerel/geniş alan ağ (LAN/WAN) mimarilerini ve kablolama altyapı işlerini içerir. Fiber optik çekimi, endüstriyel ağ anahtarlarının yapılandırması ve yüksek bant genişlikli haberleşme bu profilde toplanır.",
        "abbreviations_and_jargon": [
            {"term": "Switch", "expanded_form": "Ağ Anahtarı", "aliases": ["Kenar Anahtar", "Merkez Anahtar", "Omurga Anahtar"], "ambiguous": False, "usage_context": "Cihazları ağa bağlayan ve veri paketlerini yönlendiren iletişim donanımı listelerinde."}
        ],
        "technical_equipment": [
            {"name": "Ağ Anahtarı (Switch)", "category": "donanım", "aliases": ["Router", "Yönlendirici"]},
            {"name": "Fiber Optik Altyapı", "category": "altyapı", "aliases": ["Fiber Kablolama"]},
            {"name": "Kablosuz Haberleşme Linki", "category": "haberleşme", "aliases": ["Radyo Link", "Wi-Fi Access Point"]},
            {"name": "Ağ Yönetim Yazılımı", "category": "yazılım", "aliases": ["NMS"]},
            {"name": "Endüstriyel Tip Switch", "category": "donanım", "aliases": ["Sahra Tipi Anahtar"]},
            {"name": "Ağ Topolojisi Tasarımı", "category": "metodoloji", "aliases": ["Network Mimarisi"]},
            {"name": "Yapısal Kablolama", "category": "altyapı", "aliases": ["Data Kablolaması"]},
            {"name": "Fiber Sonlandırma İşlemi", "category": "metodoloji", "aliases": ["Ek ve Sonlandırma"]},
            {"name": "Ağ Performans Testi", "category": "teslim_ciktisi", "aliases": ["OTDR Test Raporu"]}
        ],
        "action_verbs": ["ağ altyapısı kurulumu", "fiber optik kablo tesisi", "endüstriyel ağ anahtarı yapılandırması", "kablosuz haberleşme sistemi montajı", "yapısal kablolama yapılması", "ağ topolojisi tasarımı"]
    },
    "TEK-04": {
        "description_expanded": "Bilişim sistemlerinin siber saldırılardan korunmasını sağlayan güvenlik duvarlarının (firewall), tehdit algılama yazılımlarının ve siber güvenlik politikalarının devreye alınması hizmetleridir. Temel ağ iletişiminden ziyade, veri gizliliğini ve dışarıdan gelecek saldırıları önlemeyi hedefler.",
        "abbreviations_and_jargon": [
            {"term": "Firewall", "expanded_form": "Güvenlik Duvarı", "aliases": ["Yeni Nesil Güvenlik Duvarı", "NGFW"], "ambiguous": False, "usage_context": "Ağ trafiğini kurallara göre filtreleyen siber güvenlik donanımı veya yazılımı isterlerinde."}
        ],
        "technical_equipment": [
            {"name": "Güvenlik Duvarı", "category": "donanım", "aliases": ["Firewall", "UTM"]},
            {"name": "Ağ Güvenlik İzleme Yazılımı", "category": "yazılım", "aliases": ["SIEM", "Log Yönetimi"]},
            {"name": "Saldırı Tespit Sistemi", "category": "yazılım", "aliases": ["IDS/IPS"]},
            {"name": "Sızma Testi Hizmeti", "category": "hizmet", "aliases": ["Penetration Test"]},
            {"name": "Uç Nokta Güvenliği", "category": "yazılım", "aliases": ["Antivirüs", "EDR"]},
            {"name": "Siber Güvenlik Sıkılaştırma", "category": "metodoloji", "aliases": ["Hardening"]},
            {"name": "Veri Sızıntısı Önleme", "category": "yazılım", "aliases": ["DLP"]},
            {"name": "Güvenlik Politikası Raporu", "category": "teslim_ciktisi", "aliases": ["BGYS Belgesi"]},
            {"name": "Zafiyet Analizi", "category": "metodoloji", "aliases": ["Vulnerability Scan"]}
        ],
        "action_verbs": ["siber güvenlik sistemi kurulumu", "güvenlik duvarı yapılandırması", "sızma testi hizmeti alınması", "saldırı tespit sistemi entegrasyonu", "siber güvenlik sıkılaştırma yapılması", "ağ izleme ve loglama yazılımı kurulumu"]
    },
    "OPS-01": {
        "description_expanded": "İhale kapsamında tedarik edilen donanım, direk ve yazılımların sahada fiziksel montajının yapılması, elektrik-veri kablo altyapılarının çekilmesi, fiziksel kalibrasyonların tamamlanıp sistemin çalışır vaziyette teslim edilmesidir. Cihaz üretiminden ziyade şantiye ortamındaki entegrasyon ve işçiliğe odaklanır.",
        "abbreviations_and_jargon": [],
        "technical_equipment": [
            {"name": "Saha Montaj İşlemi", "category": "metodoloji", "aliases": ["Fiziksel Montaj", "Ekipman Kurulumu"]},
            {"name": "Sistem Devreye Alma", "category": "metodoloji", "aliases": ["Test ve Kabul"]},
            {"name": "Elektrik Altyapı Tesisi", "category": "altyapı", "aliases": ["Enerji Kablolaması"]},
            {"name": "Direk ve Ankraj İmalatı", "category": "altyapı", "aliases": ["Beton Temel", "Ankraj İşleri"]},
            {"name": "Kablo Kanalı ve Kazı", "category": "altyapı", "aliases": ["Yeraltı Altyapı Kazısı"]},
            {"name": "Fiziksel Cihaz Kalibrasyonu", "category": "metodoloji", "aliases": ["Donanım Ayarı"]},
            {"name": "Kurulum Test Raporu", "category": "teslim_ciktisi", "aliases": ["Devreye Alma Tutanağı"]},
            {"name": "As-Built Projesi", "category": "teslim_ciktisi", "aliases": ["Uygulama Projesi", "Son Durum Projesi"]}
        ],
        "action_verbs": ["saha ekipmanı montajı", "sistem devreye alma hizmeti", "elektrik ve haberleşme kablo tesisi", "direk ve ankraj altyapısı yapılması", "as-built proje çizimi", "saha kurulum testleri yapılması"]
    },
    "OPS-02": {
        "description_expanded": "Daha önce kurulmuş ve çalışmakta olan sistemlerin sorunsuz işlemeye devam etmesi için arıza müdahale, parça değişimi, önleyici periyodik bakım ve sürekli teknik destek sağlanması hizmetleridir. Garanti kapsamı, hizmet seviyesi garantileri (SLA) ve sahadaki arızalara belirli sürelerde müdahale şartları içerir.",
        "abbreviations_and_jargon": [
            {"term": "SLA", "expanded_form": "Hizmet Seviyesi Sözleşmesi", "aliases": ["Müdahale Süresi Garantisi"], "ambiguous": False, "usage_context": "Arızalara çözüm bulma ve sisteme müdahale sürelerinin taahhüt edildiği teknik destek sözleşmelerinde."}
        ],
        "technical_equipment": [
            {"name": "Periyodik Bakım Hizmeti", "category": "hizmet", "aliases": ["Önleyici Bakım"]},
            {"name": "Arıza Onarım Hizmeti", "category": "hizmet", "aliases": ["Çağrı Bazlı Müdahale"]},
            {"name": "Yedek Parça Tedariki", "category": "donanım", "aliases": ["Parça Değişimi"]},
            {"name": "Çağrı Merkezi Desteği", "category": "hizmet", "aliases": ["Helpdesk", "Uzaktan Destek"]},
            {"name": "Yerinde Teknik Servis", "category": "hizmet", "aliases": ["Saha Servisi"]},
            {"name": "SLA Takip Raporu", "category": "teslim_ciktisi", "aliases": ["Servis Seviye Raporu"]},
            {"name": "Yazılım Güncelleme", "category": "metodoloji", "aliases": ["Versiyon Yükseltme", "Patch Yönetimi"]},
            {"name": "Bakım Envanter Yönetimi", "category": "metodoloji", "aliases": ["Depo Yedek Parça Yönetimi"]}
        ],
        "action_verbs": ["periyodik bakım hizmeti yapılması", "arıza onarım ve teknik destek sağlanması", "yedek parça tedarik edilmesi", "çağrı bazlı müdahale hizmeti", "yazılım güncelleme ve destek verilmesi", "sla servis raporu hazırlanması"]
    },
    "OPS-03": {
        "description_expanded": "Bir projenin standartlara uygun yönetilmesi, fizibilite etütlerinin ve ihale dokümanlarının hazırlanması veya yüklenicinin yaptığı işlerin idare adına denetlenmesini içeren danışmanlık hizmetleridir. Fiziksel bir kurulum veya mal alımı içermez; entelektüel mühendislik, müşavirlik ve denetim faaliyetidir.",
        "abbreviations_and_jargon": [],
        "technical_equipment": [
            {"name": "Fizibilite Raporu", "category": "teslim_ciktisi", "aliases": ["Etüt Raporu"]},
            {"name": "İhale Dokümanı Hazırlama", "category": "metodoloji", "aliases": ["Teknik Şartname Yazımı"]},
            {"name": "Proje Yönetim Hizmeti", "category": "hizmet", "aliases": ["Müşavirlik"]},
            {"name": "Kontrollük ve Denetim", "category": "hizmet", "aliases": ["Saha Kontrollüğü"]},
            {"name": "Proje Risk Analizi", "category": "metodoloji", "aliases": ["Risk Yönetimi"]},
            {"name": "Kalite Güvence Denetimi", "category": "metodoloji", "aliases": ["QA/QC Hizmeti"]},
            {"name": "Maliyet Keşif Çıkarımı", "category": "teslim_ciktisi", "aliases": ["Bütçe Analizi"]},
            {"name": "PMI Metodolojisi Uygulaması", "category": "metodoloji", "aliases": ["PMP Süreçleri"]}
        ],
        "action_verbs": ["proje danışmanlık hizmeti alınması", "fizibilite raporu hazırlanması", "teknik şartname oluşturulması", "saha kontrollük hizmeti yapılması", "proje yönetim ve denetimi", "maliyet keşif analizi çıkarılması"]
    }
}

if __name__ == "__main__":
    out_path = Path("data/profiles_seed_13_2.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(SEED_DATA, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated rich seed file: {out_path}")
