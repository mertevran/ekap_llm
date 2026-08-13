"""
Merkezi yapılandırma.

Projenin TÜM ayarları burada tek bir `Settings` sınıfında toplanır. Değerler
`.env` dosyasından okunur; `.env` yoksa aşağıdaki varsayılanlar geçerlidir.
Kullanım:

    from app.config.settings import ayarlari_al
    ayarlar = ayarlari_al()
    print(ayarlar.birincil_model)

TASARIM KARARI — veri kaynağı değiştirilebilir:
`DATA_BACKEND=sqlite` yerel bir `ekap.db` kopyasıyla çalışır, `DATA_BACKEND=postgres`
canlı sunucuya bağlanır. Uygulamanın geri kalanı hangisinin bağlı olduğunu BİLMEZ;
her iki arka uç da aynı depo (repository) arayüzünü uygular — bkz. app/database/base.py.

TASARIM KARARI — model adı serbest metindir:
Kod, kullanılabilecek modelleri bir "izin listesi" ile sınırlamaz. Ölçülmemiş bir
model adı yazarsanız program çalışmayı sürdürür, yalnızca log'a bir uyarı düşer.
Hangi modelin daha iyi olduğuna ölçümle karar verilir (bkz. evaluation/), kodla
dayatılmaz.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, AliasChoices, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PROJE_KOKU = Path(__file__).resolve().parents[2]

# Bu projede ölçülmüş modeller. Listede olmayan bir model de kullanılabilir;
# tek fark, log'a "bu model henüz ölçülmedi" uyarısının düşmesidir.
OLCULMUS_MODELLER = {
    "qwen3:4b": "VARSAYILAN — ~4 GB VRAM yeter, hızlı; ölçüm setlerinde 35/35 pozitif",
    "qwen3:8b": "Daha isabetli ama ~8 GB VRAM ister ve belirgin biçimde yavaştır",
    "aya-expanse:8b": "Zayıf kaldı, ayrıca uydurma (halüsinasyon) gözlendi — önerilmez",
    "gemma3:4b": "Zayıf kaldı — önerilmez",
}


class Settings(BaseSettings):
    uygulama_adi: str = "EkapUnified"
    ortam: str = "development"
    log_seviyesi: str = "INFO"

    # --- Veri kaynağı ---
    # "sqlite"   = yerel bir ekap.db dosyası (kurulum gerektirmez, denemek için ideal)
    # "postgres" = canlı veritabanı sunucusu (salt okuma)
    # Varsayılan sqlite; canlıya geçmek için .env'de tek satır değiştirmek yeter.
    data_backend: Literal["sqlite", "postgres"] = "sqlite"

    # --- sqlite arka ucu ---
    # Yerel bir ekap.db kopyasının yolu. .env içinde SQLITE_YOLU ile değiştirin.
    sqlite_yolu: Path = Field(
        default=PROJE_KOKU / "data" / "ekap.db",
        description="Yerel SQLite veritabanı dosyasının yolu.",
    )

    # --- postgres arka ucu ---
    # DİKKAT: Bu alanların varsayılanı bilerek BOŞ bırakıldı. Gerçek sunucu adresi,
    # kullanıcı adı ve parola koda değil, versiyon kontrolüne girmeyen `.env`
    # dosyasına yazılır (bkz. .env.example).
    # `DB_HOST` gibi kısa isimler de kabul edilir; iki yazım da aynı alanı doldurur.
    database_host: str = Field(default="", validation_alias=AliasChoices("DATABASE_HOST", "DB_HOST"))
    database_port: int = Field(default=5432, validation_alias=AliasChoices("DATABASE_PORT", "DB_PORT"))
    database_name: str = Field(default="", validation_alias=AliasChoices("DATABASE_NAME", "DB_NAME"))
    database_user: str = Field(default="", validation_alias=AliasChoices("DATABASE_USER", "DB_USER"))
    database_password: str = Field(default="", validation_alias=AliasChoices("DATABASE_PASSWORD", "DB_PASSWORD"))
    database_connect_timeout: float = Field(
        default=10.0,
        description=(
            "Postgres BAĞLANTI KURMA bütçesi (saniye). Sorgu süresini sınırlamaz — "
            "yalnızca el sıkışmayı. Sunucu erişilemezse script bu süre sonunda "
            "durur; sonsuza kadar donmasın diye kısa tutuluyor.\n"
            "Yavaş ya da VPN üzerinden erişilen bir ağda 30'a çıkarmak gerekebilir. "
            "Ama SÜREKLİ timeout alıyorsanız sorun bütçe değil ERİŞİMDİR: "
            "`Test-NetConnection <host> -Port 5432` ile önce onu doğrulayın."
        ),
    )
    database_max_deneme: int = Field(
        default=3,
        description=(
            "Bağlantı hatasında kaç kez denenecek (üstel geri çekilme ile). "
            "YALNIZCA bağlantı kurma hataları yeniden denenir; sorgu hataları "
            "denenmez — onlar geçici değildir.\n"
            "NEDEN GEREKLİ: `postgres_depo._baglan()` her çağrıda YENİ bir bağlantı "
            "açar (bağlantı havuzu yoktur); binlerce ihalelik bir taramada bu binlerce "
            "bağlantı demektir. Sunucudaki `max_connections` sınırına veya güvenlik "
            "duvarının hız sınırlamasına takılmak buna yol açar ve pratikte tek bir "
            "yeniden deneme bu hataların çoğunu kurtarır."
        ),
    )

    # --- Ollama ---
    ollama_host: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    embedding_backend: Literal["ollama", "sentence-transformers", "fake"] = "ollama"

    # --- Karar modelleri ---
    # Kararı veren ana model. VARSAYILAN qwen3:4b — ~4 GB VRAM'e sığar, hızlıdır
    # ve ölçüm setlerinde yeterli isabeti verir. Daha güçlü donanımda qwen3:8b
    # denenebilir (daha isabetli ama belirgin biçimde yavaş).
    birincil_model: str = "qwen3:4b"

    # OPSİYONEL ikinci görüş: ana model kararından emin değilse (güven skoru
    # `ikincil_guven_esigi` altındaysa) ikinci bir modele danışılır.
    # BOŞ BIRAKILIRSA KAPALIDIR — varsayılan budur. Tek GPU'lu makinelerde açmayın:
    # iki model sırayla belleğe yüklenip boşaltılır ve her geçiş saniyeler kaybettirir.
    ikincil_model: str = ""
    ikincil_guven_esigi: float = 0.75

    # Tek bir Ollama isteğinin CEVAP BEKLEME süresi (saniye). Bağlantı kurma süresi
    # bundan ayrıdır ve 10 sn'de sabittir (bkz. llm_client._istek) — böylece Ollama
    # kapalıyken script saatlerce donmaz.
    #
    # 180 sn bir GPU için fazlasıyla yeterlidir (ihale başına ölçülen süre 64-76 sn).
    # SADECE CPU ile çalışıyorsanız (LLM_NUM_GPU=0) aynı işlem 10-40 dakika sürebilir;
    # o durumda .env'de LLM_TIMEOUT_SN=3600 yapın.
    llm_timeout_sn: float = 180.0

    # Yalnızca BAĞLANTI hataları yeniden denenir. Zaman aşımı BİLEREK yeniden
    # denenmez: Ollama gelen istekleri sıraya alır, ikinci deneme birincinin
    # bitmesini beklemek zorunda kalır ve toplam süreyi katlar.
    llm_max_deneme: int = 2
    llm_geri_cekilme_sn: float = 1.0
    num_ctx: int = 8192
    llm_num_gpu: int | None = Field(
        default=None,
        description=(
            "Ollama'ya gönderilen `num_gpu` (GPU'ya yüklenecek katman sayısı). "
            "Boş = Ollama kendi karar verir (normal çalışma). "
            "0 = TAMAMEN CPU — GPU'suz sunucu senaryosunu ölçmek ve kararların "
            "donanıma bağlı olup olmadığını sınamak için. Çok yavaştır.\n"
            "NOT: bu ayar yalnızca KARAR modelini etkiler; embedding (bge-m3) ayrı "
            "bir Ollama çağrısıdır ve GPU'da kalmaya devam eder."
        ),
    )
    llm_dusunme: bool | None = Field(
        default=None,
        description=(
            "qwen3 modellerinin 'düşünme' (reasoning) modu.\n"
            "None = Ollama'ya `think` parametresi HİÇ gönderilmez; modelin kendi varsayılanı "
            "geçerli olur. VARSAYILAN ve önerilen budur.\n"
            "False = düşünme kapalı; ihale başına süre düşer ama isabet de düşebilir.\n"
            "True  = düşünme açık ve üretilen düşünce metni sonuç dosyasına kaydedilir."
        ),
    )
    belirsiz_netlestirme_esigi: float = Field(
        default=0.0,
        description=(
            "Model 'belirsiz' dediğinde, kendi ürettiği ilgi_skoru bu değerin altındaysa "
            "karar kod seviyesinde 'uygun_degil'e çevrilir. 0.0 = kapalı.\n"
            "NEDEN VAR: model 'hayır' demekten kaçınıp 'bilmiyorum'a sığınma eğiliminde; "
            "bu da aday listesini gereksiz şişirir.\n"
            "ÖLÇÜM (60 etiketli ihale): kapalıyken tam isabet %70; 0.48'de %92 ve hâlâ "
            "hiç kaçırma yok; 0.52'de ilk kaçırma görülüyor. Önerilen değer 0.48.\n"
            "Uygulaması: app/decision/stage1_kapsam.py::belirsizi_netlestir"
        ),
    )
    sert_on_filtre_skoru: float = Field(
        default=0.0,
        description=(
            "HIZ AYARI: en yakın iş paketiyle benzerlik bu değerin altındaysa model HİÇ "
            "çağrılmaz, ihale doğrudan 'uygun_degil' sayılır. 0.0 = kapalı.\n"
            "Binlerce ihalelik toplu taramalarda 0.44 önerilir: ölçümde 0.44'ün altında "
            "tek bir insan onaylı uygun ihale çıkmadı, buna karşılık rastgele ihalelerin "
            "%26'sı bu bandın altında kalıyor ve LLM çağrısından tasarruf ediliyor.\n"
            "DİKKAT: bu bir ELEME KAPISIDIR. Fazla yüksek ayarlanırsa uygun ihaleler "
            "hiç incelenmeden elenir."
        ),
    )
    llm_seed: int = Field(
        default=42,
        description=(
            "Ollama'ya verilen sabit rastgelelik tohumu (seed). Aynı girdiye aynı cevabı "
            "almak için gereklidir.\n"
            "NEDEN: temperature=0 TEK BAŞINA yeterli değil — ölçümde aynı girdiyle "
            "15 ihalenin 10'unun çıktısı değişmişti; seed eklendikten sonra sabitlendi.\n"
            "Karşılaştırmalı ölçüm yaparken değiştirmeyin, yoksa koşular kıyaslanamaz."
        ),
    )

    # --- Qdrant (vektör veritabanı) ---
    # Gömülü (embedded) modda çalışır; ayrı bir sunucu kurmanız gerekmez.
    qdrant_yolu: Path = Field(default=Path("storage/qdrant"))
    model_cache_yolu: Path = Field(default=Path("storage/model_cache"))

    # --- Benzer içerik arama (retrieval) ---
    retrieval_paket_k: int = 5           # prompt'a en fazla kaç iş paketi konacak
    retrieval_ornek_k: int = 3           # prompt'a en fazla kaç geçmiş örnek konacak
    retrieval_ilan_max_karakter: int = 2000  # arama için ilan metninden alınacak parça

    # --- Deneysel iyileştirme anahtarları ---
    # Aşağıdaki bayrakların HEPSİ varsayılan KAPALI. Her biri ayrı ayrı açılıp
    # ölçülmek üzere tasarlandı: aynı anda birden fazlasını açarsanız sonucun
    # hangi değişiklikten geldiğini ayırt edemezsiniz.
    retrieval_min_skor: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Bir iş paketinin 'ilgili' sayılması için gereken en düşük benzerlik. "
            "0.0 = eşik yok.\n"
            "YUMUŞAK uygulanır: eşiği geçen paket yoksa model yine de çağrılır, sadece "
            "prompt'a asılsız bir 'İLGİLİ İŞ PAKETLERİ' listesi konmaz. Sert eleme "
            "bilerek yapılmadı — o, uygun ihaleleri kaçırma riski taşır.\n"
            "ÖLÇÜM (evaluation/esik_analizi.py; 300 rastgele + 44 insan onaylı uygun ihale): "
            "uygun ihalelerin en düşük skoru 0.5149. 0.47 eşiği rastgele ihalelerin "
            "%59'unu eler ve hiçbir uygun ihaleyi kaybettirmez."
        ),
    )
    paket_dogrula: bool = Field(
        default=False,
        description=(
            "UYDURMA YAKALAMA: model, `eslesen_paket` alanına kendisine sunulan listede "
            "OLMAYAN bir paket adı yazarsa bu yakalanır ve karar 'belirsiz'e düşürülür.\n"
            "Gerçek vaka: bir inşaat ihalesinde model 'Bina İşleri' adında var olmayan bir "
            "paket uydurup ihaleye 'uygun' dedi. Tanımlı 20 paketin hiçbiri bu değildi."
        ),
    )
    okas_vetosu: bool = Field(
        default=False,
        description=(
            "İhalenin TÜM OKAS (mal/hizmet sınıflandırma) kodları açıkça kapsam dışı bir "
            "bölümdeyse (yolcu taşıma, gıda, tıbbi cihaz, akaryakıt, giyim vb.), "
            "'kalem listesi yok — kanıt yetersiz' uyarısı prompt'a EKLENMEZ.\n"
            "Kararı doğrudan değiştirmez ve modeli atlamaz; yalnızca yanlış tetiklenen bir "
            "uyarıyı susturur.\n"
            "Gerçek vaka: bir öğrenci taşıma ihalesi, ulaşım planlama paketiyle eşik değerin "
            "kıl payı üstünde eşleşti; kanıt yetersizliği uyarısı devreye girdi ve karar "
            "'belirsiz' oldu — oysa doğrusu net biçimde 'uygun_degil'di.\n"
            "Kod listesi ve testi: app/decision/okas_kapsam.py"
        ),
    )
    nitel_yakinlik: bool = Field(
        default=False,
        description=(
            "Prompt'ta benzerlik değeri sayı yerine nitel etiketle gösterilir "
            "('yakınlık: orta'). Böylece modelin kopyalayabileceği bir sayı kalmaz.\n"
            "NEDEN: bir ölçüm koşusunda 30 kararın 18'inde modelin ürettiği `ilgi_skoru`, "
            "kendisine gösterilen arama skorunun birebir kopyasıydı. İki karar kapısı bu "
            "sayıya baktığı için, kopyalama ikisini de anlamsızlaştırıyor.\n"
            "Uygulaması: app/decision/stage1_kapsam.py::_YAKINLIK_BANTLARI"
        ),
    )
    negatif_dogrula: bool = Field(
        default=False,
        description=(
            "Her iş paketinin `negatif_terimler` listesi, o pakete AİT OLMAYAN işleri "
            "tanımlar. Bu ayar açıkken bu terimlerin ilan metninde gerçekten geçip "
            "geçmediğini KOD kontrol eder ve geçenleri metinden alıntıyla prompt'a koyar.\n"
            "Kapalıyken terimler sadece bir uyarı listesi olarak gösterilir ve eşleşmeyi "
            "metinde fark etmek tamamen modele bırakılır — bunun güvenilir olmadığı "
            "ölçüldü.\n"
            "Kararı zorlamaz, yalnızca modele somut sinyal verir. "
            "Uygulaması: app/decision/negatif_dogrulama.py"
        ),
    )
    faaliyet_ortusmesi: bool = Field(
        default=False,
        description=(
            "Çıktı şemasına kategorik bir `faaliyet_ortusmesi` alanı eklenir "
            "(guclu/kismi/zayif/yok) ve karardan ÖNCE üretilir. `ilgi_skoru`nun yerine "
            "değil yanına gelir, ikisi aynı koşuda karşılaştırılabilsin diye.\n"
            "NEDEN: tek bir sayı olan `ilgi_skoru` aynı anda iki ayrı soruyu taşımak "
            "zorunda kalıyor — 'iş örtüşüyor mu' ve 'elimde yeterli kanıt var mı'. "
            "Ayrıca sayı olduğu için modelin kopyalamasına açık. Kategorik bir alan "
            "kopyalanamaz ve bu iki ekseni ayırır.\n"
            "Uygulaması: app/decision/schemas.py::FaaliyetOrtusmesi"
        ),
    )
    b1_alan_terimi_istisnasi: bool = Field(
        default=False,
        description=(
            "'Kanıt yetersiz' uyarısı, ihalenin 'Niteliği, türü ve miktarı' alanında "
            "profil sözlüğünden tanıdık bir terim geçiyorsa VERİLMEZ.\n"
            "NEDEN: bu uyarı, ilan metninde kalem listesi bulunmadığında tetiklenir. Ancak "
            "kod standart maddeleri temizledikten sonra geriye çoğu zaman yalnızca başlık "
            "kalıyor ve sistem 'hiç kanıt yok' diyordu — oysa elenmeyen o başlığın kendisi "
            "kanıttı ('40 Kalem CCTV, Kontrol ve Kent İzleme Merkezi Sistemi' belirsiz bir "
            "ifade değildir).\n"
            "ÖLÇÜM: düşünme modu kapatıldığında bu yanlış tetiklenme yüzünden ölçüm seti "
            "30/30'dan 20/30'a düştü; on hatanın hepsi 'uygun -> belirsiz' yönündeydi.\n"
            "Uygulaması: app/decision/stage1_kapsam.py::kalem_listesi_yok_mu"
        ),
    )
    destekleyici_paket_kurali: bool = Field(
        default=False,
        description=(
            "En yakın paketlerin HEPSİ yatay/destekleyici bir paketse (OPS-*, TEK-*) ve "
            "hiçbir ALAN paketi (AUS/ENT/PLN) eşiği geçmiyorsa, bu durum prompt'ta "
            "modele ayrıca bildirilir.\n"
            "NEDEN: 'Bakım, Onarım ve Teknik Destek' gibi genel paketler, konusu ne "
            "olursa olsun her bakım ihalesine yakın çıkar (ambulans bakımı, bina bakımı, "
            "gemi sörveyi...). Bu paketin gömülü metni 'bakım' kavramını yakalar, NEYİN "
            "bakımı olduğunu değil — dolayısıyla tek başına yanıltıcı bir sinyaldir."
        ),
    )

    # --- Aşama 1 -> Aşama 2 geçişi ---
    # False yaparsanız yalnızca Aşama 1 (kapsam kararı) çalışır.
    asama2_calissin: bool = True
    asama2_min_ilgi_skoru: float = Field(
        default=0.0,
        description=(
            "Aşama 1 'uygun_degil' dediğinde Aşama 2 zaten hiç çalışmaz. Bu eşik, "
            "'belirsiz' kararlarında da Aşama 2'yi atlayarak zaman kazanmak için "
            "kullanılabilir.\n"
            "VARSAYILAN 0.0 = hiçbir 'belirsiz' atlanmaz. Projenin temel ilkesi budur: "
            "uygun bir ihaleyi kaçırmak, uygun olmayanı listeye almaktan pahalıdır."
        ),
    )

    # --- Çıktı dizinleri ---
    # Göreli yazılırlarsa proje köküne göre çözülür (bkz. yollari_coz).
    cikti_dizini: Path = Field(default=Path("Sonuclar"))   # ölçüm ve tarama çıktıları
    rapor_dizini: Path = Field(default=Path("reports"))    # üretilen raporlar
    log_dizini: Path = Field(default=Path("logs"))         # log dosyaları

    model_config = SettingsConfigDict(
        env_file=PROJE_KOKU / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("llm_dusunme", mode="before")
    @classmethod
    def _bos_dusunme_none(cls, v):
        """`.env` içinde `LLM_DUSUNME=` (boş) yazmayı `None` olarak yorumlar.

        pydantic-settings boş bir satırı `''` (boş metin) olarak verir ve bunu
        `bool | None` tipine çeviremeyip hata atardı. Oysa boş bırakmak,
        dokümante edilmiş varsayılan davranıştır: "Ollama'ya `think` gönderme".
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("birincil_model", "ikincil_model")
    @classmethod
    def _model_uyari(cls, v: str) -> str:
        # Bilerek hata ATILMAZ: ölçülmemiş bir model kullanmak serbesttir,
        # kullanıcı yalnızca bilgilendirilir.
        if v and v not in OLCULMUS_MODELLER:
            logger.warning(
                "'%s' bu projede henüz ölçülmedi. Ölçülmüş modeller: %s",
                v,
                ", ".join(f"{k} ({a})" for k, a in OLCULMUS_MODELLER.items()),
            )
        return v

    def yollari_coz(self) -> None:
        """Göreli dizin yollarını proje köküne göre mutlak yola çevirir."""
        for alan in ("qdrant_yolu", "model_cache_yolu", "cikti_dizini", "rapor_dizini", "log_dizini"):
            yol = getattr(self, alan)
            if not yol.is_absolute():
                yol = PROJE_KOKU / yol
            setattr(self, alan, yol.resolve())

    def dizinleri_olustur(self) -> None:
        """Çıktı dizinleri yoksa oluşturur (varsa dokunmaz)."""
        for alan in ("qdrant_yolu", "model_cache_yolu", "cikti_dizini", "rapor_dizini", "log_dizini"):
            getattr(self, alan).mkdir(parents=True, exist_ok=True)

    def postgres_baglanti_dizesi(self) -> str:
        """psycopg'nin beklediği biçimde Postgres bağlantı dizesi üretir."""
        return (
            f"host={self.database_host} port={self.database_port} "
            f"dbname={self.database_name} user={self.database_user} "
            f"password={self.database_password} "
            f"connect_timeout={int(self.database_connect_timeout)}"
        )


@lru_cache(maxsize=1)
def ayarlari_al() -> Settings:
    """Ayarları okur ve döndürür.

    `lru_cache` sayesinde `.env` dosyası süreç başına yalnızca BİR kez okunur;
    sonraki çağrılar aynı nesneyi geri verir. Ayarlara her yerden bu fonksiyonla
    erişin, `Settings()` sınıfını doğrudan çağırmayın.
    """
    ayarlar = Settings()
    ayarlar.yollari_coz()
    ayarlar.dizinleri_olustur()
    return ayarlar
