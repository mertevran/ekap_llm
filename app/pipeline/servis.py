"""
Uçtan uca analiz servisi — iki aşamayı ve ön-filtreleri orkestre eder.

AKIŞ:
  0) Deterministik ön-filtre       (LLM yok)
  1) Veri okuma + okuma anında temizleme
  2) Retrieval (profiller + kontrastif örnekler)
  3) AŞAMA 1 — kapsam kararı
     └─ "uygun_degil" ise DUR. Aşama 2 hiç çalışmaz (LLM çağrısı tasarrufu).
  4) AŞAMA 2 — yeterlilik kararı + deterministik doğrulama + koşullu ikinci görüş
  5) Birleşik sonuç

TASARIM İLKESİ (kaçırma en pahalı hata): Aşama 2, Aşama 1'in "uygun" kararını
sessizce "ilgisiz"e çeviremez — en fazla "inceleme_gerekli"ye düşürür ve insan
incelemesi bayrağını kaldırır. Tek istisna modelin faaliyet alanı uyumsuzluğunu
açıkça işaretlemesidir (doğrulayıcı K6).

NOT 1: `kritik_baglam_atlandi` bayrağı doğrulayıcıya gerçekten iletilir. Önceki
sürümde bu bayrak hesaplanıp yolda kayboluyor ve KURAL 13 hiç tetiklenmiyordu.

NOT 2: Profil yönlendirmesi başarısız olsa bile boru hattı DURMAZ. Yönlendirme bir
yardımcı sinyaldir, zorunlu bir adım değil; ilgili profilleri benzerlik araması
zaten kendi başına buluyor.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.decision.dogrulayici import dogrula
from app.decision.llm_client import LlmHatasi, OllamaIstemcisi
from app.decision.on_filtre import on_filtrele
from app.decision.schemas import AnalizSonucu, KapsamSonucu, ModelKarari
from app.decision.stage1_kapsam import kapsam_karari_uret
from app.decision.stage2_yeterlilik import yeterlilik_karari_uret
from app.domain.models import Ihale, kapsam_metni
from app.profiles.loader import profil_getir
from app.retrieval.profil_retriever import ProfilRetriever, sorgu_metni

logger = logging.getLogger(__name__)


@dataclass
class AnalizAyarlari:
    paket_k: int = 5
    ornek_k: int = 3
    asama2_calissin: bool = True
    ikincil_guven_esigi: float = 0.75
    sadece_aktif: bool = False
    # Hepsi VARSAYILAN KAPALI — eski davranış korunur, tek tek açılıp ölçülür.
    retrieval_min_skor: float = 0.0
    paket_dogrula: bool = False
    destekleyici_paket_kurali: bool = False

    # OKAS vetosu: ihalenin TÜM OKAS kodları açıkça kapsam dışı bir bölümdeyse
    # (yolcu taşıma, gıda, tıbbi, akaryakıt...) B1 "kalem listesi yok" uyarısı
    # prompt'a eklenmez. Kararı DEĞİŞTİRMEZ, LLM'i atlamaz — yalnızca yanlış
    # tetiklenen bir uyarıyı susturur. Gerekçe: app/decision/okas_kapsam.py
    okas_vetosu: bool = False
    # Negatif terimler ilan metninde gerçekten geçiyor mu — kod doğrular, kanıtı
    # prompt'a koyar. Gerekçe: app/decision/negatif_dogrulama.py
    negatif_dogrula: bool = False
    # Şemaya kategorik `faaliyet_ortusmesi` ekseni eklenir (karardan ÖNCE üretilir).
    # Gerekçe: app/decision/schemas.py::FaaliyetOrtusmesi
    faaliyet_ortusmesi: bool = False
    # B1 kanıt yetersizliği uyarısı, 'Niteliği' alanında profil sözlüğünden bir
    # terim geçiyorsa verilmez. Gerekçe: stage1_kapsam::kalem_listesi_yok_mu
    b1_alan_terimi_istisnasi: bool = False
    # Paket/örnek skorları prompt'ta sayı yerine nitel etiketle ("yakınlık: orta")
    # gösterilir. Model kopyalayacak sayı bulamaz. Gerekçe: stage1_kapsam
    # ::_YAKINLIK_BANTLARI başlığı. ÖLÇÜLDÜ: kaçırma v7'de 30'da 18 yankı.
    nitel_yakinlik: bool = False

    # SERT ÖN FİLTRE — retrieval'ın en üst benzerliği bu değerin ALTINDAYSA LLM HİÇ
    # ÇAĞRILMAZ, doğrudan `uygun_degil` denir. `retrieval_min_skor`dan FARKLIDIR:
    # o yumuşaktır (LLM yine çağrılır, sadece prompt ilgililik iddia etmez), bu ise
    # bir kapıdır ve yanlış ayarlanırsa KAÇIRMA üretir.
    #
    # ÖLÇÜLDÜ (esik_analizi.py, 300 rastgele + 52 etiketli/onaylı ihale):
    #   insan onaylı `uygun` min      : 0.5149
    #   etiketli `belirsiz` min       : 0.481
    #   rastgele ihalelerin %26'sı    : < 0.44
    # 0.44'ün altında tek bir pozitif YOK. Bu yüzden 0.44 güvenli sınır; üstüne
    # çıkmak kaçırma riskini başlatır.
    #
    # VARSAYILAN 0.0 = KAPALI. Toplu taramada açılır (~%26 zaman tasarrufu).
    sert_on_filtre_skoru: float = 0.0
    # 'belirsiz' + düşük skor -> 'uygun_degil'. Bkz. stage1_kapsam::belirsizi_netlestir
    belirsiz_netlestirme_esigi: float = 0.0


class AnalizServisi:
    def __init__(
        self,
        *,
        depo,
        retriever: ProfilRetriever,
        birincil_istemci: OllamaIstemcisi,
        ikincil_istemci: OllamaIstemcisi | None = None,
        ayarlar: AnalizAyarlari | None = None,
    ) -> None:
        self.depo = depo
        self.retriever = retriever
        self.birincil = birincil_istemci
        self.ikincil = ikincil_istemci
        self.ayarlar = ayarlar or AnalizAyarlari()
        self._alan_sozlugu_onbellek: frozenset[str] | None = None

    def _alan_terimleri(self) -> frozenset[str] | None:
        """B1 alan terimi istisnası için profil sözlüğü. Bayrak kapalıysa None.

        Bir kez kurulup önbelleğe alınır: 20 profil dosyası her ihalede yeniden
        okunmamalı, toplu taramada 4.000 kez okumak demek.
        """
        if not self.ayarlar.b1_alan_terimi_istisnasi:
            return None
        if self._alan_sozlugu_onbellek is None:
            from app.decision.stage1_kapsam import alan_sozlugu
            from app.profiles.loader import profilleri_yukle

            self._alan_sozlugu_onbellek = alan_sozlugu(profilleri_yukle())
        return self._alan_sozlugu_onbellek

    # ------------------------------------------------------------------

    def analiz_et(self, *, ikn: str | None = None, tender_id: str | None = None) -> AnalizSonucu:
        if not ikn and not tender_id:
            raise ValueError("ikn veya tender_id verilmeli.")
        ihale = self.depo.ikn_ile_getir(ikn) if ikn else self.depo.id_ile_getir(tender_id)
        return self.ihaleyi_analiz_et(ihale)

    def ihaleyi_analiz_et(self, ihale: Ihale) -> AnalizSonucu:
        sonuc = AnalizSonucu(
            tender_id=ihale.id,
            ikn=ihale.ikn,
            adi=ihale.adi or "",
            idare_adi=ihale.idare_adi or "",
            ihale_durumu=ihale.ihale_durumu or "",
            birincil_model=self.birincil.model,
        )
        sureler: dict[str, float] = {}

        # --- 0. Deterministik ön-filtre ---
        t = time.time()
        of = on_filtrele(ihale, sadece_aktif=self.ayarlar.sadece_aktif)
        sureler["on_filtre"] = round(time.time() - t, 3)
        if not of.devam:
            sonuc.on_filtre_kurali = of.kural
            if of.karar:
                sonuc.kapsam = KapsamSonucu(
                    gerekce=of.gerekce, eslesen_paket=None, eslesen_okas=[],
                    ilgi_skoru=0.0, karar=of.karar,
                )
            sonuc.notlar.append(of.gerekce)
            sonuc.sureler_sn = sureler
            return sonuc

        # --- 2. Retrieval ---
        t = time.time()
        sorgu = sorgu_metni(ihale.adi or "", kapsam_metni(ihale))
        k_ornek = self.ayarlar.ornek_k
        paketler = self.retriever.paketleri_bul(sorgu, k=self.ayarlar.paket_k)
        uygun_ornekler = self.retriever.uygun_ornekler(sorgu, k=k_ornek, haric=ihale.id)
        belirsiz_ornekler = self.retriever.belirsiz_ornekler(sorgu, k=k_ornek, haric=ihale.id)
        red_ornekler = self.retriever.red_ornekler(sorgu, k=k_ornek, haric=ihale.id)
        sureler["retrieval"] = round(time.time() - t, 3)

        def _ozet(ornekler):
            return [{"id": o.id, "baslik": o.baslik, "benzerlik": o.benzerlik} for o in ornekler]

        sonuc.kullanilan_paketler = [
            {"kod": p.kod, "baslik": p.baslik, "benzerlik": p.benzerlik, "oncelik": p.oncelik}
            for p in paketler
        ]
        sonuc.benzer_uygun_ornekler = _ozet(uygun_ornekler)
        sonuc.benzer_belirsiz_ornekler = _ozet(belirsiz_ornekler)
        sonuc.benzer_red_ornekler = _ozet(red_ornekler)

        # --- 2b. SERT ÖN FİLTRE — LLM'e hiç gitmeden ele ---
        # Retrieval zaten çalıştı, en üst benzerlik elimizde. Eşiğin altındaysa
        # model çağrılmaz. Toplu taramada ~%26 tasarruf; ölçülmüş kaçırma riski 0
        # (bkz. AnalizAyarlari.sert_on_filtre_skoru).
        enust = paketler[0].benzerlik if paketler else 0.0
        if self.ayarlar.sert_on_filtre_skoru > 0 and enust < self.ayarlar.sert_on_filtre_skoru:
            sonuc.on_filtre_kurali = "SERT_ESIK"
            sonuc.kapsam = KapsamSonucu(
                gerekce=(
                    f"En yakın iş paketi benzerliği {enust} — sert eşik "
                    f"{self.ayarlar.sert_on_filtre_skoru}'in altında. Model çağrılmadan "
                    f"kapsam dışı sayıldı."
                ),
                eslesen_paket=None, eslesen_okas=[], ilgi_skoru=enust, karar="uygun_degil",
            )
            sonuc.notlar.append(f"sert_on_filtre: enust={enust}")
            sonuc.sureler_sn = sureler
            return sonuc

        # --- 3. AŞAMA 1 — kapsam ---
        t = time.time()
        try:
            kapsam, kapsam_uyarilari = kapsam_karari_uret(
                ihale, paketler, self.birincil, uygun_ornekler, red_ornekler, belirsiz_ornekler,
                min_skor=self.ayarlar.retrieval_min_skor,
                paket_dogrula=self.ayarlar.paket_dogrula,
                destekleyici_kurali=self.ayarlar.destekleyici_paket_kurali,
                belirsiz_netlestirme_esigi=self.ayarlar.belirsiz_netlestirme_esigi,
                okas_vetosu=self.ayarlar.okas_vetosu,
                nitel_yakinlik=self.ayarlar.nitel_yakinlik,
                negatif_dogrula=self.ayarlar.negatif_dogrula,
                faaliyet_ortusmesi=self.ayarlar.faaliyet_ortusmesi,
                alan_terimleri=self._alan_terimleri(),
            )
            sonuc.notlar.extend(kapsam_uyarilari)
        except LlmHatasi as e:
            # Model şemaya uyan çıktı üretemedi. SESSİZCE "uygun_degil" DEMEK YASAK —
            # kaçırma en pahalı hata. "belirsiz"e düşüp insana bırakıyoruz.
            logger.error("Aşama 1 başarısız (%s): %s", ihale.ikn, e)
            kapsam = KapsamSonucu(
                gerekce=f"Kapsam modeli geçerli çıktı üretemedi: {e}. İnsan incelemesi gerekli.",
                eslesen_paket=None, eslesen_okas=[], ilgi_skoru=0.0, karar="belirsiz",
            )
            sonuc.notlar.append("asama1_llm_hatasi")
            sonuc.insan_incelemesi_gerekli = True
        sureler["asama1_kapsam"] = round(time.time() - t, 3)
        sonuc.kapsam = kapsam

        # --- Aşama 1 -> Aşama 2 geçiş kapısı ---
        if kapsam.karar == "uygun_degil":
            sonuc.notlar.append("asama2_atlandi: kapsam disi")
            sonuc.sureler_sn = sureler
            return sonuc
        if not self.ayarlar.asama2_calissin:
            sonuc.notlar.append("asama2_kapali (ASAMA2_CALISSIN=false)")
            sonuc.sureler_sn = sureler
            return sonuc

        # --- 4. AŞAMA 2 — yeterlilik ---
        t = time.time()
        profiller = [p for p in (profil_getir(pk.kod) for pk in paketler) if p]
        kanitlar = [{"ikn": o.id, "baslik": o.baslik} for o in uygun_ornekler]

        try:
            yeterlilik, uyarilar = yeterlilik_karari_uret(
                ihale, kapsam, profiller, kanitlar, self.birincil
            )
        except LlmHatasi as e:
            logger.error("Aşama 2 başarısız (%s): %s", ihale.ikn, e)
            sonuc.notlar.append(f"asama2_llm_hatasi: {e}")
            sonuc.insan_incelemesi_gerekli = True
            sonuc.sebepler.append("Yeterlilik modeli geçerli çıktı üretemedi — insan incelemesi gerekli.")
            sureler["asama2_yeterlilik"] = round(time.time() - t, 3)
            sonuc.sureler_sn = sureler
            return sonuc

        sonuc.notlar.extend(uyarilar)

        # Bu bayrak aşağıda doğrulayıcıya GERÇEKTEN iletiliyor (bkz. dosya başlığı).
        kritik_baglam_atlandi = bool(
            kapsam_metni(ihale) and len(kapsam_metni(ihale)) > 6000
        )
        dogrulama = dogrula(
            yeterlilik,
            kanit_sayisi=len(kanitlar),
            kritik_baglam_atlandi=kritik_baglam_atlandi,
        )
        sureler["asama2_yeterlilik"] = round(time.time() - t, 3)

        # --- Koşullu ikinci görüş ---
        ikinci: ModelKarari | None = None
        gerek_var = self.ikincil is not None and (
            yeterlilik.guven < self.ayarlar.ikincil_guven_esigi
            or yeterlilik.karar == "inceleme_gerekli"
            or not dogrulama.gecti
            or bool(dogrulama.celiskiler)
            or bool(dogrulama.eksik_zorunlu_kanit)
        )
        if gerek_var and self.ikincil is not None:
            t = time.time()
            try:
                ikinci_sonuc, ikinci_uyari = yeterlilik_karari_uret(
                    ihale, kapsam, profiller, kanitlar, self.ikincil
                )
                ikinci = ModelKarari(model_adi=self.ikincil.model, sonuc=ikinci_sonuc)
                sonuc.notlar.extend(ikinci_uyari)
            except LlmHatasi as e:
                sonuc.notlar.append(f"ikincil_gorus_hatasi: {e}")
            sureler["ikincil_gorus"] = round(time.time() - t, 3)

        # --- Nihai karar birleştirme ---
        nihai = yeterlilik.karar
        sebepler: list[str] = []
        insan = False

        if dogrulama.zorunlu_karar is not None:
            nihai = dogrulama.zorunlu_karar
            sebepler.append(
                f"Deterministik doğrulama kararı '{yeterlilik.karar}' -> '{nihai}' olarak değiştirdi "
                f"({', '.join(dogrulama.uygulanan_kurallar)})."
            )
        elif ikinci is not None:
            if ikinci.sonuc.karar == yeterlilik.karar:
                sebepler.append("Birincil ve ikincil model aynı kararı verdi.")
            else:
                nihai = "inceleme_gerekli"
                insan = True
                sebepler.append(
                    f"Modeller çelişti: {self.birincil.model}='{yeterlilik.karar}', "
                    f"{ikinci.model_adi}='{ikinci.sonuc.karar}' — insan incelemesi gerekli."
                )

        # KAÇIRMA KORUMASI: Aşama 1 'uygun' dediyse Aşama 2 'ilgisiz'e sertleştiremez.
        if nihai == "ilgisiz" and kapsam.karar == "uygun":
            nihai = "inceleme_gerekli"
            insan = True
            sebepler.append(
                "Aşama 1 kapsamı 'uygun' bulmuşken Aşama 2 'ilgisiz' dedi — kaçırma riski, "
                "karar 'inceleme_gerekli'ye yükseltildi."
            )

        sebepler.extend(dogrulama.celiskiler)
        sebepler.extend(dogrulama.eksik_zorunlu_kanit)
        if dogrulama.celiskiler or dogrulama.eksik_zorunlu_kanit or nihai == "inceleme_gerekli":
            insan = True

        sonuc.yeterlilik = yeterlilik.model_copy(update={"karar": nihai})
        sonuc.dogrulama = dogrulama
        sonuc.ikincil_gorus = ikinci
        sonuc.sebepler = sebepler
        sonuc.insan_incelemesi_gerekli = sonuc.insan_incelemesi_gerekli or insan
        sonuc.sureler_sn = sureler
        return sonuc


def servis_olustur(ayarlar=None, *, sadece_asama1: bool = False) -> AnalizServisi:
    """.env'den okuyarak tam donanımlı bir servis kurar."""
    from app.config.settings import ayarlari_al
    from app.database.base import depo_olustur
    from app.embedding.embedders import embedder_olustur

    ayarlar = ayarlar or ayarlari_al()
    embedder = embedder_olustur(ayarlar)
    retriever = ProfilRetriever(ayarlar.qdrant_yolu, embedder)

    birincil = OllamaIstemcisi(
        model=ayarlar.birincil_model,
        host=ayarlar.ollama_host,
        timeout=ayarlar.llm_timeout_sn,
        max_deneme=ayarlar.llm_max_deneme,
        geri_cekilme_sn=ayarlar.llm_geri_cekilme_sn,
        num_ctx=ayarlar.num_ctx,
        seed=ayarlar.llm_seed,
        dusunme=ayarlar.llm_dusunme,
        num_gpu=ayarlar.llm_num_gpu,
    )
    ikincil = (
        OllamaIstemcisi(
            model=ayarlar.ikincil_model,
            host=ayarlar.ollama_host,
            timeout=ayarlar.llm_timeout_sn,
            num_ctx=ayarlar.num_ctx,
            seed=ayarlar.llm_seed,
            dusunme=ayarlar.llm_dusunme,
            num_gpu=ayarlar.llm_num_gpu,
        )
        if ayarlar.ikincil_model
        else None
    )

    return AnalizServisi(
        depo=depo_olustur(ayarlar),
        retriever=retriever,
        birincil_istemci=birincil,
        ikincil_istemci=ikincil,
        ayarlar=AnalizAyarlari(
            paket_k=ayarlar.retrieval_paket_k,
            ornek_k=ayarlar.retrieval_ornek_k,
            asama2_calissin=(False if sadece_asama1 else ayarlar.asama2_calissin),
            ikincil_guven_esigi=ayarlar.ikincil_guven_esigi,
            retrieval_min_skor=ayarlar.retrieval_min_skor,
            paket_dogrula=ayarlar.paket_dogrula,
            destekleyici_paket_kurali=ayarlar.destekleyici_paket_kurali,
            sert_on_filtre_skoru=ayarlar.sert_on_filtre_skoru,
            belirsiz_netlestirme_esigi=ayarlar.belirsiz_netlestirme_esigi,
            okas_vetosu=ayarlar.okas_vetosu,
            nitel_yakinlik=ayarlar.nitel_yakinlik,
            negatif_dogrula=ayarlar.negatif_dogrula,
            faaliyet_ortusmesi=ayarlar.faaliyet_ortusmesi,
            b1_alan_terimi_istisnasi=ayarlar.b1_alan_terimi_istisnasi,
        ),
    )
