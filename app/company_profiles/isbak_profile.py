from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileContextPolicy(BaseModel):
    usage: str = Field(default="profil_yonlendirme_ve_bilgi_getirme")
    is_company_evidence: bool = Field(default=False)


class ProfileJargonEntry(BaseModel):
    term: str
    expanded_form: str | None = None
    aliases: list[str] = Field(default_factory=list)
    usage_context: str | None = None
    ambiguous: bool = Field(default=False)


class ProfileEquipmentEntry(BaseModel):
    name: str
    category: str
    aliases: list[str] = Field(default_factory=list)


class TenderCategorySignals(BaseModel):
    guclu_terimler: list[str] = Field(default_factory=list)
    destekleyici_terimler: list[str] = Field(default_factory=list)
    negatif_terimler: list[str] = Field(default_factory=list)
    okas_kodlari: list[str] = Field(default_factory=list)
    okas_kod_on_ekleri: list[str] = Field(default_factory=list)
    genel_terimler: list[str] = Field(default_factory=list)
    okas_metin_destegi_zorunlu: bool = Field(default=True)


class IsbakProfile(BaseModel):
    profil_kodu: str
    profil_adi: str
    profil_ailesi: str
    profil_surumu: str = Field(default="1.0.0")
    durum: str = Field(default="taslak")

    # Mevcut 1.0.0 Alanları
    birincil_yetkinlikler: list[str] = Field(default_factory=list)
    destekleyici_profiller: list[str] = Field(default_factory=list)
    urunler_ve_hizmetler: list[str] = Field(default_factory=list)
    personel_kapasitesi: list[str] = Field(default_factory=list)
    belgeler: list[str] = Field(default_factory=list)
    tamamlanan_projeler: list[str] = Field(default_factory=list)
    is_deneyim_belgeleri: list[str] = Field(default_factory=list)
    ekipman_ve_altyapi: list[str] = Field(default_factory=list)
    teknolojiler: list[str] = Field(default_factory=list)
    is_ortaklari: list[str] = Field(default_factory=list)
    hizmet_bolgeleri: list[str] = Field(default_factory=list)
    kapasite_sinirlari: list[str] = Field(default_factory=list)
    veri_kaynaklari: list[str] = Field(default_factory=list)
    veri_durumu: str = Field(default="kurum_ici_dogrulama_gerekli")
    son_guncelleme: str | None = None
    ihale_kategori_sinyalleri: TenderCategorySignals = Field(default_factory=TenderCategorySignals)

    # Yeni 1.1.0 Çok Katmanlı Bağlam Alanları
    description_expanded: str = Field(default="")
    abbreviations_and_jargon: list[ProfileJargonEntry] = Field(default_factory=list)
    technical_equipment: list[ProfileEquipmentEntry] = Field(default_factory=list)
    action_verbs: list[str] = Field(default_factory=list)
    context_policy: ProfileContextPolicy = Field(default_factory=ProfileContextPolicy)


def build_profile_semantic_text(profile: IsbakProfile) -> str:
    """
    Profil bilgisini deterministik olarak semantik gömme metnine dönüştürür.
    Negatif terimleri içermez. Eylem ağırlıkları (action_verbs) en düşük olacak
    şekilde metnin sonuna eklenir.
    """
    lines = []
    lines.append(f"Profil Kodu: {profile.profil_kodu}")
    lines.append(f"Profil Adı: {profile.profil_adi}")
    lines.append(f"Profil Ailesi: {profile.profil_ailesi}")

    if profile.description_expanded:
        lines.append(f"\nAçıklama: {profile.description_expanded}")

    if profile.birincil_yetkinlikler:
        lines.append("\nBirincil Yetkinlikler:")
        for y in profile.birincil_yetkinlikler:
            lines.append(f"- {y}")

    if profile.technical_equipment:
        lines.append("\nTeknik Ekipman, Metodoloji ve Çıktılar:")
        for eq in profile.technical_equipment:
            aliases = f" ({', '.join(eq.aliases)})" if eq.aliases else ""
            lines.append(f"- [{eq.category}] {eq.name}{aliases}")

    if profile.abbreviations_and_jargon:
        lines.append("\nKısaltmalar ve Jargon:")
        for j in profile.abbreviations_and_jargon:
            expanded = f": {j.expanded_form}" if j.expanded_form else ""
            lines.append(f"- {j.term}{expanded}")

    signals = profile.ihale_kategori_sinyalleri
    if signals.guclu_terimler or signals.destekleyici_terimler or signals.genel_terimler:
        lines.append("\nSinyal Terimleri:")
        for t in signals.guclu_terimler:
            lines.append(f"- {t} (Güçlü)")
        for t in signals.destekleyici_terimler:
            lines.append(f"- {t} (Destekleyici)")
        for t in signals.genel_terimler:
            lines.append(f"- {t} (Genel)")

    if profile.action_verbs:
        lines.append("\nEylem İfadeleri:")
        lines.append(", ".join(profile.action_verbs))

    return "\n".join(lines).strip()
