import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.tender import TenderRecord
from app.pipeline.exceptions import ReportExistsError
from app.pipeline.models import AnalysisResult


class IsbakDecisionReporter:
    """
    İSBAK İhale Kararları için rapor üretici.
    JSON ve Markdown formatlarında yapılandırılmış, güvenli raporlar oluşturur.
    """

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_version = "v1.1.0"

    def generate_reports(
        self,
        report: AnalysisResult,
        tender_record: TenderRecord | None = None,
        overwrite: bool = False,
        json_only: bool = False,
    ) -> tuple[Path, Path | None]:
        """
        Her analiz için iki ayrı çıktı üretir (JSON ve Markdown). json_only True ise sadece JSON üretir.
        """
        timestamp = datetime.now(UTC)
        safe_ikn = report.ikn.replace("/", "_").replace("\\", "_")

        base_name = f"analysis_{safe_ikn}"
        json_path = self.output_dir / f"{base_name}.json"
        md_path = self.output_dir / f"{base_name}.md"

        if json_path.exists():
            if not overwrite:
                raise ReportExistsError(
                    f"İKN '{report.ikn}' için rapor zaten mevcut. Zorlamak için --force kullanın."
                )
            else:
                # Geçmiş analizleri korumak için eski dosyayı taşı
                try:
                    with open(json_path, encoding="utf-8") as old_f:
                        old_data = json.load(old_f)
                        old_id = old_data.get("analysis_id", "unknown")
                        old_time = (
                            old_data.get("olusturulma_zamani", "unknown")
                            .replace(":", "")
                            .replace("-", "")
                        )
                except Exception:
                    old_id = "unknown"
                    old_time = "unknown"

                backup_json = self.output_dir / f"{base_name}_{old_time}_{old_id}.json"
                backup_md = self.output_dir / f"{base_name}_{old_time}_{old_id}.md"

                json_path.rename(backup_json)
                if md_path.exists():
                    md_path.rename(backup_md)

        report_data = self._build_report_data(report, tender_record, timestamp)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        if json_only:
            return json_path, None

        md_content = self._build_markdown_content(report_data)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return json_path, md_path

    def render_json(self, report: AnalysisResult, tender_record: TenderRecord | None = None) -> str:
        timestamp = datetime.now(UTC)
        data = self._build_report_data(report, tender_record, timestamp)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def render_markdown(
        self, report: AnalysisResult, tender_record: TenderRecord | None = None
    ) -> str:
        timestamp = datetime.now(UTC)
        data = self._build_report_data(report, tender_record, timestamp)
        return self._build_markdown_content(data)

    def write_json(
        self,
        report: AnalysisResult,
        tender_record: TenderRecord | None = None,
        overwrite: bool = False,
    ) -> Path:
        json_path, _ = self.generate_reports(
            report, tender_record, overwrite=overwrite, json_only=True
        )
        return json_path

    def write_markdown(
        self,
        report: AnalysisResult,
        tender_record: TenderRecord | None = None,
        overwrite: bool = False,
    ) -> Path:
        timestamp = datetime.now(UTC)
        safe_ikn = report.ikn.replace("/", "_").replace("\\", "_")
        base_name = f"analysis_{safe_ikn}"
        md_path = self.output_dir / f"{base_name}.md"
        data = self._build_report_data(report, tender_record, timestamp)
        md_content = self._build_markdown_content(data)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        return md_path

    def _build_report_data(
        self, report: AnalysisResult, tender_record: TenderRecord | None, timestamp: datetime
    ) -> dict[str, Any]:
        decision = report.decision

        qwen_karari = "Bulunamadı"
        python_dogrulamasi = None

        kriter_sonuclari = []
        riskler = []
        eksik_bilgiler = []

        if decision:
            if decision.primary_model:
                qwen_karari = decision.primary_model.decision
                kriter_sonuclari = getattr(decision.primary_model, "zorunlu_kriter_sonuclari", [])
                riskler = getattr(decision.primary_model, "risks", [])

            if decision.validation:
                python_dogrulamasi = {
                    "gecti": decision.validation.passed,
                    "celiskiler": decision.validation.contradictions,
                    "zorunlu_eksikler": decision.validation.missing_required_evidence,
                    "negatif_kapsam_dogrulandi": (
                        decision.validation.negative_scope.verified
                    ),
                    "eslesen_negatif_terimler": (
                        decision.validation.negative_scope.matched_terms
                    ),
                }
                if decision.validation.missing_required_evidence:
                    eksik_bilgiler.extend(decision.validation.missing_required_evidence)

        # Stage durations
        asama_sureleri = {d.name: round(d.duration_seconds, 3) for d in report.diagnostics}

        # Format evidence
        gecmis_ihale_kanitlari = [
            {
                "ikn": ev.ikn,
                "skor": round(ev.retrieval_score, 3),
                "profil": ev.profile_code,
                "kanit": ev.text_excerpt,
            }
            for ev in report.evidence
        ]

        return {
            "analysis_id": getattr(report, "analysis_id", "Bilinmiyor"),
            "ikn": report.ikn,
            "ihale_kimligi": getattr(tender_record, "id", "Bilinmiyor")
            if tender_record
            else "Bilinmiyor",
            "ihale_basligi": report.tender_title,
            "idare_adi": (
                getattr(tender_record, "idare_adi", "")
                if tender_record
                else ""
            )
            or (decision.authority_name if decision else "")
            or str(report.metadata.get("authority_name") or "")
            or "Bilinmiyor",
            "profil_kodu": getattr(report, "primary_profile_code", None) or "Bilinmiyor",
            "nihai_karar": decision.final_decision if decision else "Hata",
            "guven_duzeyi": decision.final_confidence if decision else 0.0,
            "ham_model_guveni": (
                decision.confidence_calibration.raw_confidence
                if decision
                else 0.0
            ),
            "guven_kalibrasyon_nedenleri": (
                decision.confidence_calibration.reasons
                if decision
                else []
            ),
            "insan_incelemesi": decision.human_review_required if decision else True,
            "katilim_incelemesi": (
                decision.participation_review_required
                if decision
                else True
            ),
            "katilim_yeterliligi_durumu": (
                decision.katilim_yeterliligi_durumu
                if decision
                else "dogrulanmadi"
            ),
            "dogrulanamayan_katilim_sartlari": (
                decision.dogrulanamayan_katilim_sartlari
                if decision
                else []
            ),
            "qwen_karari": qwen_karari,
            "python_dogrulamasi": python_dogrulamasi,
            "kriter_sonuclari": kriter_sonuclari,
            "riskler": riskler,
            "eksik_bilgiler": list(set(eksik_bilgiler)),
            "gecmis_ihale_kanitlari": gecmis_ihale_kanitlari,
            "sirket_yetkinlik_kanitlari": getattr(report, "matched_capabilities", []),
            "asama_sureleri": asama_sureleri,
            "baglam_siniri_bilgileri": {
                "limit_chars": report.metadata.get("context_limit_chars"),
                "final_chars": report.metadata.get("final_context_chars"),
                "truncated": report.metadata.get("context_truncated"),
                "omitted_evidence": report.metadata.get("omitted_evidence_count"),
            },
            "model_kullanim_bilgileri": {
                "primary_model": report.metadata.get("primary_model"),
                "secondary_model": report.metadata.get("secondary_model"),
                "primary_prompt": report.metadata.get("primary_prompt_version"),
                "secondary_prompt": report.metadata.get("secondary_prompt_version"),
                "primary_attempt": report.metadata.get("primary_attempt_count"),
                "primary_timeout": report.metadata.get("primary_timeout_seconds"),
            },
            "analiz_surumu": self.analysis_version,
            "olusturulma_zamani": timestamp.isoformat(),
        }

    def _build_markdown_content(self, data: dict[str, Any]) -> str:
        lines = []

        lines.append(f"# İSBAK İhale Analiz Raporu: {data['ikn']}")
        lines.append(
            "\n> **UYARI**: Bu çıktı otomatik bir ön değerlendirmedir. Nihai teklif kararı, ihale dokümanlarının yetkili birimlerce incelenmesi sonrasında verilmelidir."
        )

        lines.append("\n## İhale bilgisi")
        lines.append(f"- **İKN:** {data['ikn']}")
        lines.append(f"- **İhale Kimliği:** {data['ihale_kimligi']}")
        lines.append(f"- **Başlık:** {data['ihale_basligi']}")
        lines.append(f"- **İdare Adı:** {data['idare_adi']}")
        lines.append(f"- **Tarih:** {data['olusturulma_zamani']}")

        lines.append("\n## Yönetici özeti")
        lines.append(
            f"Otomatik değerlendirme sistemi bu ihaleyi **{data['nihai_karar']}** olarak etiketlemiştir."
        )

        lines.append("\n## Nihai karar")
        lines.append(f"**Karar:** `{data['nihai_karar']}`")

        lines.append("\n## Güven düzeyi")
        lines.append(f"**Skor:** {data['guven_duzeyi']:.2f}")
        lines.append(f"**Ham model skoru:** {data['ham_model_guveni']:.2f}")
        for reason in data["guven_kalibrasyon_nedenleri"]:
            lines.append(f"- {reason}")

        lines.append("\n## İnsan incelemesi gereği")
        review = "Evet" if data["insan_incelemesi"] else "Hayır"
        lines.append(f"**Gerekli mi:** {review}")

        lines.append("\n## Katılım yeterliliği")
        lines.append(
            f"**Durum:** {data['katilim_yeterliligi_durumu']}"
        )
        participation_review = "Evet" if data["katilim_incelemesi"] else "Hayır"
        lines.append(f"**Ayrı katılım incelemesi:** {participation_review}")
        for requirement in data["dogrulanamayan_katilim_sartlari"]:
            lines.append(f"- {requirement}")

        lines.append("\n## İhale şartları")
        lines.append("Belirtilen ana şartlar model tarafından çözümlenmiştir.")
        if data["kriter_sonuclari"]:
            for k in data["kriter_sonuclari"]:
                lines.append(f"- {k}")

        lines.append("\n## İSBAK ile uyumlu yetkinlikler")
        if data["sirket_yetkinlik_kanitlari"]:
            for cap in data["sirket_yetkinlik_kanitlari"]:
                lines.append(f"- {cap}")
        else:
            lines.append("- _Belirtilmedi / Eşleşmedi_")

        lines.append("\n## Karşılanmayan şartlar")
        if data["python_dogrulamasi"] and data["python_dogrulamasi"]["celiskiler"]:
            for cond in data["python_dogrulamasi"]["celiskiler"]:
                lines.append(f"- ❌ {cond}")
        else:
            lines.append("- _Tespit edilmedi_")

        lines.append("\n## Doğrulanamayan bilgiler")
        if data["eksik_bilgiler"]:
            for missing in data["eksik_bilgiler"]:
                lines.append(f"- ⚠️ {missing}")
        else:
            lines.append("- _Tespit edilmedi_")

        lines.append("\n## Qwen değerlendirmesi")
        lines.append(f"**Karar:** {data['qwen_karari']}")

        lines.append("\n## Python doğrulaması")
        if data["python_dogrulamasi"]:
            status = "Geçti ✅" if data["python_dogrulamasi"]["gecti"] else "Kaldı ❌"
            lines.append(f"**Durum:** {status}")
        else:
            lines.append("**Durum:** Yapılmadı")

        lines.append("\n## Geçmiş ihale kanıtları")
        if data["gecmis_ihale_kanitlari"]:
            for i, ev in enumerate(data["gecmis_ihale_kanitlari"], 1):
                lines.append(f"### Kanıt {i}: İKN {ev['ikn']} (Skor: {ev['skor']})")
                lines.append(f"**Profil:** {ev['profil']}")
                lines.append(f"```text\n{ev['kanit']}\n```\n")
        else:
            lines.append("_RAG aramasından herhangi bir geçmiş benzer ihale kanıtı bulunamadı._")

        lines.append("\n## Şirket bilgi tabanı kanıtları")
        lines.append("Şirket profili doğrultusunda analiz edilmiştir.")

        lines.append("\n## Teknik tanı ve süreler")
        for stage, duration in data["asama_sureleri"].items():
            lines.append(f"- **{stage}:** {duration} sn")

        lines.append("\n## Sınırlamalar")
        lines.append(
            "- Sistem yalnızca yapılandırılmış RAG kanıtlarına ve profil dosyasına dayanır."
        )
        lines.append("- Kaynaksız olumlu gerekçeler nihai karara etki etmez.")

        return "\n".join(lines)
