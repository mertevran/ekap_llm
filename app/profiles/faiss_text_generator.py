from typing import Any


class FaissTextGenerator:
    @staticmethod
    def generate_main_query(profile_data: dict[str, Any]) -> str:
        """
        Ana profil metnini, BGE-M3 modelinin en iyi anlayacağı formatta,
        belirtilen sıralamaya ve başlıklara sadık kalarak oluşturur.
        """
        lines = []

        # Üst bilgiler
        lines.append(profile_data.get("profil_kodu", ""))
        lines.append(profile_data.get("profil_adi", ""))
        lines.append(profile_data.get("profil_ailesi", ""))
        lines.append("")

        # [AÇIKLAMA]
        desc = profile_data.get("description_expanded", "")
        if desc:
            lines.append("[AÇIKLAMA]")
            lines.append(desc)
            lines.append("")

        # [BİRİNCİL YETKİNLİKLER]
        yetkinlikler = profile_data.get("birincil_yetkinlikler", [])
        if yetkinlikler:
            lines.append("[BİRİNCİL YETKİNLİKLER]")
            for y in yetkinlikler:
                lines.append(f"- {y}")
            lines.append("")

        # [GÜÇLÜ İHALE SİNYALLERİ]
        sinyaller = profile_data.get("ihale_kategori_sinyalleri", {})
        guclu = sinyaller.get("guclu_terimler", [])
        if guclu:
            lines.append("[GÜÇLÜ İHALE SİNYALLERİ]")
            for g in guclu:
                lines.append(f"- {g}")
            lines.append("")

        # [DESTEKLEYİCİ SİNYALLER]
        destekleyici = sinyaller.get("destekleyici_terimler", [])
        if destekleyici:
            lines.append("[DESTEKLEYİCİ SİNYALLER]")
            for d in destekleyici:
                lines.append(f"- {d}")
            lines.append("")

        # [TEKNİK EKİPMAN]
        equipments = profile_data.get("technical_equipment", [])
        if equipments:
            lines.append("[TEKNİK EKİPMAN]")
            for eq in equipments:
                name = eq.get("name", "")
                cat = eq.get("category", "")
                aliases = eq.get("aliases", [])

                parts = [name]
                if cat:
                    parts.append(f"Kategori: {cat}")
                if aliases:
                    parts.append(f"Diğer adları: {', '.join(aliases)}")
                lines.append("- " + " | ".join(parts))
            lines.append("")

        # [TEKNOLOJİLER]
        teknolojiler = profile_data.get("teknolojiler", [])
        if teknolojiler:
            lines.append("[TEKNOLOJİLER]")
            for t in teknolojiler:
                lines.append(f"- {t}")
            lines.append("")

        # [EYLEM FİİLLERİ]
        action_verbs = profile_data.get("action_verbs", [])
        if action_verbs:
            lines.append("[EYLEM FİİLLERİ]")
            for av in action_verbs:
                lines.append(f"- {av}")
            lines.append("")

        # [KISALTMALAR VE AÇILIMLAR]
        abbreviations = profile_data.get("abbreviations_and_jargon", [])
        if abbreviations:
            lines.append("[KISALTMALAR VE AÇILIMLAR]")
            for ab in abbreviations:
                term = ab.get("term", "")
                expanded = ab.get("expanded_form", "")
                aliases = ab.get("aliases", [])

                parts = [term]
                if expanded:
                    parts.append(f"Açılım: {expanded}")
                if aliases:
                    parts.append(f"Diğer: {', '.join(aliases)}")
                lines.append("- " + " | ".join(parts))
            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def generate_strong_signals_query(profile_data: dict[str, Any]) -> str:
        code = profile_data.get("profil_kodu", "")
        name = profile_data.get("profil_adi", "")

        guclu = profile_data.get("ihale_kategori_sinyalleri", {}).get("guclu_terimler", [])
        if not guclu:
            return ""

        lines = [f"{code} - {name} Güçlü İhale Sinyalleri:"]
        for g in guclu:
            lines.append(f"- {g}")

        return "\n".join(lines)

    @staticmethod
    def generate_equipment_query(profile_data: dict[str, Any]) -> str:
        code = profile_data.get("profil_kodu", "")
        name = profile_data.get("profil_adi", "")

        equipments = profile_data.get("technical_equipment", [])
        if not equipments:
            return ""

        lines = [f"{code} - {name} Teknik Ekipmanları:"]
        for eq in equipments:
            name_val = eq.get("name", "")
            lines.append(f"- {name_val}")

        return "\n".join(lines)

    @classmethod
    def generate_all_queries(cls, profile_data: dict[str, Any]) -> list[dict[str, str]]:
        queries = []
        code = profile_data.get("profil_kodu", "")

        main_query = cls.generate_main_query(profile_data)
        if main_query:
            queries.append({"profil_kodu": code, "query_type": "main", "text": main_query})

        strong_query = cls.generate_strong_signals_query(profile_data)
        if strong_query:
            queries.append(
                {"profil_kodu": code, "query_type": "strong_signals", "text": strong_query}
            )

        equip_query = cls.generate_equipment_query(profile_data)
        if equip_query:
            queries.append({"profil_kodu": code, "query_type": "equipment", "text": equip_query})

        return queries
