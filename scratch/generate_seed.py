import json
from pathlib import Path

# We define the new 1.1.0 data for all 20 profiles.
SEED_DATA = {
    "AUS-01": {
        "description_expanded": "Kavşak sinyalizasyonu, trafik kontrol merkezleri ve yaya güvenliğini artırmaya yönelik akıllı sistemlerin tasarımı, kurulumu ve yönetimidir. Dinamik ve yeşil dalga gibi trafik iyileştirici yönetim stratejilerini destekler.",
        "abbreviations_and_jargon": [
            {"term": "TKM", "expanded_form": "Trafik Kontrol Merkezi", "aliases": [], "ambiguous": False},
            {"term": "Dinamik Kavşak", "expanded_form": "Gerçek zamanlı trafik hacmine göre süreleri ayarlayan kavşak yönetim sistemi", "aliases": ["Akıllı Kavşak"], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Sinyal Denetleyici", "category": "donanım", "aliases": ["Kavşak Kontrol Cihazı"]},
            {"name": "Sinyalizasyon Direği", "category": "altyapi", "aliases": ["Oto Direk", "Yaya Direk"]},
            {"name": "Sinyal Optiği", "category": "donanım", "aliases": ["Sinyal Lambası"]},
            {"name": "Trafik Yönetim Yazılımı", "category": "yazılım", "aliases": ["Kavşak Yönetim Sistemi"]}
        ],
        "action_verbs": ["kurulum", "montaj", "entegrasyon", "sinyalizasyon", "kablolama", "devreye alma"]
    },
    "AUS-02": {
        "description_expanded": "Trafik kural ihlallerini tespit eden, yol güvenliğini sağlayan ve elektronik denetim amaçlı kamera ve hız radar sistemlerinin kurulumunu ve işletimini içerir.",
        "abbreviations_and_jargon": [
            {"term": "EDS", "expanded_form": "Elektronik Denetleme Sistemi", "aliases": ["Elektronik Denetim Sistemi"], "ambiguous": False},
            {"term": "PTS", "expanded_form": "Plaka Tanıma Sistemi", "aliases": ["LPR", "ALPR"], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Kırmızı Işık İhlal Tespit Sistemi", "category": "donanım", "aliases": ["Kırmızı Işık Kamerası"]},
            {"name": "Hız İhlal Tespit Sistemi", "category": "donanım", "aliases": ["Radar", "Ortalama Hız Sistemi"]},
            {"name": "PTS Kamerası", "category": "donanım", "aliases": ["Plaka Okuma Kamerası"]}
        ],
        "action_verbs": ["tespit", "denetim", "okuma", "kurulum", "kalibrasyon"]
    },
    "AUS-03": {
        "description_expanded": "Yol ağındaki trafik yoğunluğunu ölçen, sürücüleri VMS/DMS panelleriyle bilgilendiren ve otopark doluluk durumlarını yöneten sistemleri kapsar.",
        "abbreviations_and_jargon": [
            {"term": "DMS", "expanded_form": "Değişken Mesaj İşareti", "aliases": ["VMS"], "ambiguous": False},
            {"term": "TTS", "expanded_form": "Trafik Ölçüm Sistemi", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Araç Sayım Kamerası", "category": "donanım", "aliases": ["Trafik Sensörü"]},
            {"name": "DMS Paneli", "category": "donanım", "aliases": ["VMS Ekran", "Bilgilendirme Ekranı"]},
            {"name": "Otopark Doluluk Sensörü", "category": "donanım", "aliases": ["Park Sensörü"]}
        ],
        "action_verbs": ["bilgilendirme", "ölçüm", "sayım", "görüntüleme"]
    },
    "AUS-04": {
        "description_expanded": "Toplu taşıma hatlarının ve raylı sistemlerin yönetimini sağlayan, yolcu bilgilendirme ekranları ve filo yönetim bileşenlerini barındıran elektronik sistemlerdir.",
        "abbreviations_and_jargon": [
            {"term": "YBS", "expanded_form": "Yolcu Bilgilendirme Sistemi", "aliases": ["Yolcu Ekranı"], "ambiguous": False},
            {"term": "Akıllı Durak", "expanded_form": "Gerçek zamanlı otobüs varış bilgisini gösteren durak", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Yolcu Bilgilendirme Ekranı", "category": "donanım", "aliases": ["Durak Ekranı"]},
            {"name": "Araç İçi Ekran", "category": "donanım", "aliases": []}
        ],
        "action_verbs": ["bilgilendirme", "yönlendirme", "entegrasyon"]
    },
    "ENT-01": {
        "description_expanded": "Kritik tesislerin, kampüslerin veya şehirlerin tek merkezden izlenip yönetildiği, birden fazla donanımın ve yazılımın entegre çalıştığı operasyon merkezlerinin tasarımıdır.",
        "abbreviations_and_jargon": [
            {"term": "Video Wall", "expanded_form": "Görüntü Duvarı", "aliases": ["Ekran Duvarı"], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Görüntü Duvarı", "category": "donanım", "aliases": ["Video Wall"]},
            {"name": "Operatör Konsolu", "category": "donanım", "aliases": []},
            {"name": "Komuta Kontrol Yazılımı", "category": "yazılım", "aliases": ["VMS", "PSIM"]}
        ],
        "action_verbs": ["izleme", "yönetim", "komuta", "entegrasyon"]
    },
    "ENT-02": {
        "description_expanded": "Tesis, bina veya çevre güvenliğini sağlamak için IP kamera sistemleri ve video analitik çözümlerinin entegre edildiği güvenlik projelerini kapsar.",
        "abbreviations_and_jargon": [
            {"term": "CCTV", "expanded_form": "Kapalı Devre Televizyon", "aliases": ["Güvenlik Kamerası Sistemi"], "ambiguous": False},
            {"term": "NVR", "expanded_form": "Ağ Video Kaydedici", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "IP Güvenlik Kamerası", "category": "donanım", "aliases": ["Dome Kamera", "Bullet Kamera", "PTZ"]},
            {"name": "Video Kayıt Cihazı", "category": "donanım", "aliases": ["NVR", "Sunucu"]},
            {"name": "Video Analitik Yazılımı", "category": "yazılım", "aliases": []}
        ],
        "action_verbs": ["izleme", "kayıt", "tespit", "kurulum"]
    },
    "ENT-03": {
        "description_expanded": "Araç filolarının GPS tabanlı takibini sağlayan, saha personelinin operasyonel iş akışlarını dijitalleştiren araç takip ve telemetri çözümleridir.",
        "abbreviations_and_jargon": [
            {"term": "ATS", "expanded_form": "Araç Takip Sistemi", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Araç Takip Cihazı", "category": "donanım", "aliases": ["GPS Cihazı", "Telemetri Cihazı"]},
            {"name": "Filo Yönetim Yazılımı", "category": "yazılım", "aliases": []}
        ],
        "action_verbs": ["takip", "izleme", "konumlandırma", "telemetri"]
    },
    "ENT-04": {
        "description_expanded": "Kurum binalarında personel veya ziyaretçilerin geçiş yetkilerini kontrol eden; kartlı, biyometrik veya turnike tabanlı fiziksel erişim sistemleridir.",
        "abbreviations_and_jargon": [
            {"term": "PDKS", "expanded_form": "Personel Devam Kontrol Sistemi", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Turnike", "category": "donanım", "aliases": ["Bel Tipi Turnike", "VIP Turnike"]},
            {"name": "Kart Okuyucu", "category": "donanım", "aliases": ["Mifare Okuyucu", "Proximity"]},
            {"name": "Biyometrik Okuyucu", "category": "donanım", "aliases": ["Parmak İzi", "Yüz Tanıma"]}
        ],
        "action_verbs": ["geçiş", "kontrol", "doğrulama", "entegrasyon"]
    },
    "ENT-05": {
        "description_expanded": "Şehir veya tesis aydınlatmalarının uzaktan kontrol edilerek enerji verimliliğinin artırılmasını ve enerji tüketim verilerinin izlenmesini sağlayan çözümlerdir.",
        "abbreviations_and_jargon": [],
        "technical_equipment": [
            {"name": "Akıllı Aydınlatma Armatürü", "category": "donanım", "aliases": ["LED Armatür"]},
            {"name": "Aydınlatma Kontrol Modülü", "category": "donanım", "aliases": ["Sensör", "Node"]}
        ],
        "action_verbs": ["aydınlatma", "karartma", "tasarruf", "izleme"]
    },
    "ENT-06": {
        "description_expanded": "Akıllı şehir konseptine uygun olarak meteoroloji, hava kalitesi ölçümü veya atık yönetimi gibi alanlarda sensör verilerini toplayıp merkeze ileten çevre donatılarıdır.",
        "abbreviations_and_jargon": [
            {"term": "IoT", "expanded_form": "Nesnelerin İnterneti", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Hava Kalitesi Sensörü", "category": "donanım", "aliases": []},
            {"name": "Akıllı Direk", "category": "donanım", "aliases": ["Smart Pole"]}
        ],
        "action_verbs": ["ölçüm", "sensör veri toplama", "izleme"]
    },
    "PLN-01": {
        "description_expanded": "Şehir ve ulaşım ağları için ulaşım master planları hazırlanması, talep tahmin modellerinin oluşturulması ve mevcut durum etütlerinin yapılmasıdır.",
        "abbreviations_and_jargon": [
            {"term": "UMP", "expanded_form": "Ulaşım Master Planı", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Ulaşım Talep Tahmin Modeli", "category": "metodoloji", "aliases": []},
            {"name": "Etüt Raporu", "category": "teslim_ciktisi", "aliases": ["Mevcut Durum Analizi"]}
        ],
        "action_verbs": ["planlama", "modelleme", "analiz", "etüt"]
    },
    "PLN-02": {
        "description_expanded": "Kavşak ve arter ölçeğinde trafik kapasite analizleri, geometrik düzenleme tasarımları ve mikro seviyede trafik simülasyonları yapılması süreçleridir.",
        "abbreviations_and_jargon": [
            {"term": "Mikrosimülasyon", "expanded_form": "Araç bazlı detaylı trafik akım simülasyonu", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Trafik Simülasyon Çıktısı", "category": "teslim_ciktisi", "aliases": ["VISSIM", "AIMSUN Analizi"]},
            {"name": "Kapasite Analiz Raporu", "category": "teslim_ciktisi", "aliases": []}
        ],
        "action_verbs": ["simülasyon", "analiz", "tasarım", "sayım"]
    },
    "PLN-03": {
        "description_expanded": "Toplanan ulaşım, altyapı veya donanım verilerinin harita üzerinde sayısallaştırılması, mekânsal veri tabanlarının kurulması ve CBS tabanlı harita analizlerinin yapılmasıdır.",
        "abbreviations_and_jargon": [
            {"term": "CBS", "expanded_form": "Coğrafi Bilgi Sistemleri", "aliases": ["GIS"], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Mekânsal Veritabanı", "category": "teslim_ciktisi", "aliases": ["Geodatabase"]},
            {"name": "Harita Üretimi", "category": "teslim_ciktisi", "aliases": ["Sayısal Harita"]}
        ],
        "action_verbs": ["sayısallaştırma", "haritalama", "mekânsal analiz"]
    },
    "TEK-01": {
        "description_expanded": "Mevcut sistemleri veya donanımları birbirine bağlayan; sunucu, veritabanı, arka plan yazılımları ve API tabanlı yazılım geliştirme yetkinliklerini kapsar.",
        "abbreviations_and_jargon": [
            {"term": "API", "expanded_form": "Uygulama Programlama Arayüzü", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Yazılım Kaynak Kodu", "category": "teslim_ciktisi", "aliases": []},
            {"name": "Sistem Entegrasyonu", "category": "metodoloji", "aliases": ["Bütünleştirme"]}
        ],
        "action_verbs": ["geliştirme", "yazılım", "entegrasyon", "programlama"]
    },
    "TEK-02": {
        "description_expanded": "Elektronik kart (PCB) tasarımı, gömülü sistem yazılımları geliştirme, endüstriyel standartlarda donanım üretimi veya özelleştirilmiş cihaz çözümleridir.",
        "abbreviations_and_jargon": [
            {"term": "Gömülü Sistem", "expanded_form": "Sadece belirli bir işlevi yerine getiren donanım-yazılım bütünü", "aliases": ["Embedded System"], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Özel Tasarım Elektronik Kart", "category": "teslim_ciktisi", "aliases": ["PCB"]},
            {"name": "Mikrodenetleyici Yazılımı", "category": "teslim_ciktisi", "aliases": ["Gömülü Yazılım"]}
        ],
        "action_verbs": ["tasarım", "üretim", "gömülü geliştirme"]
    },
    "TEK-03": {
        "description_expanded": "Veri aktarımını sağlamak için yerel ağ (LAN), geniş ağ (WAN) veya fiber optik kablolama gibi ağ (network) cihazlarının yapılandırması ve altyapı işleridir.",
        "abbreviations_and_jargon": [
            {"term": "Switch", "expanded_form": "Ağ Anahtarı", "aliases": ["Kenar Anahtar", "Merkez Anahtar"], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Ağ Anahtarı (Switch)", "category": "donanım", "aliases": ["Router"]},
            {"name": "Fiber Optik Kablo", "category": "altyapi", "aliases": []}
        ],
        "action_verbs": ["kablolama", "sonlandırma", "yapılandırma", "ağ kurulumu"]
    },
    "TEK-04": {
        "description_expanded": "Sistemlerin siber saldırılardan korunmasını sağlayan siber güvenlik donanımlarının, güvenlik duvarlarının ve güvenlik izleme politikalarının devreye alınmasıdır.",
        "abbreviations_and_jargon": [
            {"term": "Firewall", "expanded_form": "Güvenlik Duvarı", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Güvenlik Duvarı (Firewall)", "category": "donanım", "aliases": ["UTM"]},
            {"name": "Ağ Güvenlik Yazılımı", "category": "yazılım", "aliases": ["SIEM"]}
        ],
        "action_verbs": ["sıkılaştırma", "yapılandırma", "koruma"]
    },
    "OPS-01": {
        "description_expanded": "İhale kapsamında temin edilen donanım ve yazılımların sahada fiziksel montajı, elektrik-veri kablolaması, kalibrasyonu ve işletmeye alma hizmetleridir.",
        "abbreviations_and_jargon": [],
        "technical_equipment": [
            {"name": "Saha Kurulum İşlemleri", "category": "metodoloji", "aliases": ["Montaj"]},
            {"name": "Devreye Alma", "category": "metodoloji", "aliases": ["Test ve Kabul"]}
        ],
        "action_verbs": ["kurulum", "montaj", "kablolama", "devreye alma", "test"]
    },
    "OPS-02": {
        "description_expanded": "Kurulu sistemlerin düzgün çalışmaya devam etmesi için sağlanan arıza giderim, periyodik önleyici bakım, parça değişimi ve teknik destek servisleridir.",
        "abbreviations_and_jargon": [
            {"term": "SLA", "expanded_form": "Hizmet Seviyesi Sözleşmesi", "aliases": [], "ambiguous": False}
        ],
        "technical_equipment": [
            {"name": "Bakım Onarım Hizmeti", "category": "metodoloji", "aliases": ["Periyodik Bakım", "Arıza Giderim"]},
            {"name": "SLA Raporu", "category": "teslim_ciktisi", "aliases": []}
        ],
        "action_verbs": ["bakım", "onarım", "destek", "müdahale", "servis"]
    },
    "OPS-03": {
        "description_expanded": "Projenin şartnamelere uygun yönetilmesi, ihale ve fizibilite dokümanlarının hazırlanması, kontrollük veya teknik danışmanlık hizmetlerinin verilmesidir.",
        "abbreviations_and_jargon": [],
        "technical_equipment": [
            {"name": "Fizibilite Raporu", "category": "teslim_ciktisi", "aliases": []},
            {"name": "Proje Yönetim Planı", "category": "metodoloji", "aliases": ["PMI"]}
        ],
        "action_verbs": ["danışmanlık", "proje yönetimi", "müşavirlik", "denetim"]
    }
}

if __name__ == "__main__":
    out_path = Path("data/profiles_seed_13_2.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(SEED_DATA, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated seed file: {out_path}")
