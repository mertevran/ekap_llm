import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
from app.database.tender_repository import TenderNotFoundError, TenderRepository
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.indexing.document_builder import TenderDocumentBuilder
from app.pipeline.exceptions import DatabaseAccessError, DecisionServiceError, TenderAnalysisError
from app.pipeline.models import AnalysisResult, DiagnosticStep, EvidenceRecord
from app.retrieval.isbak_query_profile_router import IsbakQueryProfileRouter
from app.retrieval.isbak_tender_retriever import IsbakTenderRetriever

logger = logging.getLogger(__name__)


def format_company_context(context_dict: dict[str, Any]) -> str:
    """
    Şirket bağlamını LLM için okunabilir formata dönüştürür.
    Teknik alanları filtreler.
    """
    lines = ["## ŞİRKET PROFİLİ VE YETERLİLİKLERİ\n"]

    primary_code = context_dict.get("birincil_profil_kodu", "BİLİNMİYOR")
    lines.append(f"- **Birincil Profil**: {primary_code}")

    loaded_codes = context_dict.get("yuklenen_profil_kodlari", [])
    secondary = [c for c in loaded_codes if c != primary_code]
    if secondary:
        lines.append(f"- **İkincil Profiller**: {', '.join(secondary)}")

    kurum = context_dict.get("kurum", {})

    yetkinlikler = kurum.get("dogrulanmis_yetkinlikler", [])
    if isinstance(yetkinlikler, list) and yetkinlikler:
        lines.append("\n### Doğrulanmış Yetkinlikler")
        for y in yetkinlikler:
            lines.append(f"- {y}")

    belgeler = kurum.get("dogrulanmis_belgeler", [])
    if isinstance(belgeler, list) and belgeler:
        lines.append("\n### Doğrulanmış Belgeler")
        for b in belgeler:
            lines.append(f"- {b}")

    eksikler = kurum.get("eksik_bilgiler", [])
    # Düzeltme: Eğer anahtarlar farklı isimlendirilmişse diye alternatif kontrol
    if not eksikler:
        eksikler = kurum.get("eksik_veya_dogrulanamayan_bilgiler", [])

    if isinstance(eksikler, list) and eksikler:
        lines.append("\n### Eksik veya Doğrulanamayan Bilgiler")
        for e in eksikler:
            lines.append(f"- {e}")

    return "\n".join(lines)


def format_company_context_compact(context_dict: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Şirket bağlamını LLM için KESİN YİNELENENLERİ (exact duplicate) temizleyerek kompakt formata dönüştürür.
    Markdown etiketlerini azaltır. Boş bölümleri eklemez.
    """
    lines = []

    primary_code = context_dict.get("birincil_profil_kodu", "BİLİNMİYOR")
    lines.append(f"PROFİL: {primary_code}")

    birincil_profil = context_dict.get("birincil_profil", {})
    profil_adi = birincil_profil.get("profil_adi")
    if profil_adi:
        lines.append(f"PROFİL ADI: {profil_adi}")

    loaded_codes = context_dict.get("yuklenen_profil_kodlari", [])
    secondary = [c for c in loaded_codes if c != primary_code]
    if secondary:
        lines.append(f"EK PROFİLLER: {', '.join(secondary)}")

    kurum = context_dict.get("kurum", {})

    stats = {
        "verified_capabilities_original_count": 0,
        "verified_capabilities_compact_count": 0,
        "verified_documents_original_count": 0,
        "verified_documents_compact_count": 0,
        "exact_duplicate_capabilities_removed": 0,
        "exact_duplicate_documents_removed": 0,
        "missing_info_count": 0,
    }

    def add_section(items: list | dict | str, header: str, stat_prefix: str = "") -> None:
        if not items:
            return

        if isinstance(items, str):
            lines.append(f"\n{header}")
            lines.append(items)
            return

        if isinstance(items, dict):
            # Dict values, e.g. capacity limits
            lines.append(f"\n{header}")
            for k, v in items.items():
                if v:
                    lines.append(f"- {k}: {v}")
            return

        if stat_prefix:
            stats[f"{stat_prefix}_original_count"] = len(items)

        seen = set()
        unique_items = []
        for item in items:
            item_str = str(item)
            if item_str not in seen:
                seen.add(item_str)
                unique_items.append(item_str)

        if stat_prefix:
            stats[f"{stat_prefix}_compact_count"] = len(unique_items)
            mid = stat_prefix.split('_')[1] if '_' in stat_prefix else stat_prefix
            stats[f"exact_duplicate_{mid}_removed"] = len(items) - len(unique_items)

        if unique_items:
            lines.append(f"\n{header}")
            for item in unique_items:
                lines.append(f"- {item}")

    # Primary Profile Fields
    desc = birincil_profil.get("description_expanded")
    if desc:
        add_section(desc, "FAALİYET AÇIKLAMASI:")

    add_section(birincil_profil.get("birincil_yetkinlikler", []), "BİRİNCİL YETKİNLİKLER:")

    signals = birincil_profil.get("ihale_kategori_sinyalleri", {})
    if isinstance(signals, dict):
        add_section(signals.get("guclu_terimler", []), "GÜÇLÜ TERİMLER:")
        add_section(signals.get("destekleyici_terimler", []), "DESTEKLEYİCİ TERİMLER:")
        add_section(signals.get("negatif_terimler", []), "NEGATİF TERİMLER:")

        okas = signals.get("okas_kodlari", [])
        if okas:
            add_section(okas, "OKAS KODLARI:")

        on_ekler = signals.get("okas_kod_on_ekleri", [])
        if on_ekler:
            add_section(on_ekler, "OKAS KOD ÖN EKLERİ:")

        zorunlu = signals.get("okas_metin_destegi_zorunlu")
        if zorunlu:
            lines.append(f"\nOKAS METİN DESTEĞİ ZORUNLU: {zorunlu}")

    add_section(birincil_profil.get("urunler_ve_hizmetler", []), "ÜRÜNLER VE HİZMETLER:")
    add_section(birincil_profil.get("teknolojiler", []), "TEKNOLOJİLER:")
    add_section(birincil_profil.get("technical_equipment", []), "TEKNİK EKİPMAN:")
    add_section(birincil_profil.get("abbreviations_and_jargon", []), "KISALTMALAR VE JARGON:")
    add_section(birincil_profil.get("action_verbs", []), "EYLEM FİİLLERİ:")

    kapasite = birincil_profil.get("kapasite_sinirlari", {})
    if kapasite and isinstance(kapasite, dict):
        add_section(kapasite, "KAPASİTE SINIRLARI:")

    # Company Master Fields
    yetkinlikler = kurum.get("dogrulanmis_yetkinlikler", [])
    if isinstance(yetkinlikler, list):
        add_section(yetkinlikler, "DOĞRULANMIŞ YETKİNLİKLER:", "verified_capabilities")

    belgeler = kurum.get("dogrulanmis_belgeler", [])
    if isinstance(belgeler, list):
        add_section(belgeler, "DOĞRULANMIŞ BELGELER:", "verified_documents")

    eksikler = kurum.get("eksik_bilgiler", [])
    if not eksikler:
        eksikler = kurum.get("eksik_veya_dogrulanamayan_bilgiler", [])

    if isinstance(eksikler, list) and eksikler:
        stats["missing_info_count"] = len(eksikler)
        seen = set()
        unique_eksikler = []
        for e in eksikler:
            if e not in seen:
                seen.add(e)
                unique_eksikler.append(e)

        if unique_eksikler:
            lines.append("\nEKSİKLER:")
            for e in unique_eksikler:
                lines.append(f"- {e}")

    compact_str = "\n".join(lines)
    stats["compact_chars"] = len(compact_str)
    return compact_str, stats


class IsbakTenderAnalysisService:
    """
    Uçtan uca İSBAK İhale Analiz Servisi.
    Girdi: İKN
    Çıktı: AnalysisResult (Rapor, karar ve tanı bilgileri)
    """

    def __init__(
        self,
        *,
        tender_repository: TenderRepository,
        document_builder: TenderDocumentBuilder,
        retriever: IsbakTenderRetriever,
        decision_pipeline: IsbakDecisionPipeline,
        profile_loader: IsbakProfileLoader,
        query_router: IsbakQueryProfileRouter,
        max_evidence_chunks: int = 5,
        max_chunks_per_tender: int = 1,
    ) -> None:
        self.tender_repository = tender_repository
        self.document_builder = document_builder
        self.retriever = retriever
        self.decision_pipeline = decision_pipeline
        self.profile_loader = profile_loader
        self.query_router = query_router
        self.max_evidence_chunks = max_evidence_chunks
        self.max_chunks_per_tender = max_chunks_per_tender

    def analyze_tender(self, ikn: str) -> AnalysisResult:
        analysis_id = str(uuid.uuid4())
        logger.info(f"Analysis started: analysis_id={analysis_id}, ikn={ikn}")

        diagnostics: list[DiagnosticStep] = []
        result = AnalysisResult(
            analysis_id=analysis_id, ikn=ikn, tender_title="", tender_category=None
        )

        def _track_step(name: str):
            step = DiagnosticStep(name=name, start_time=datetime.now(UTC))
            diagnostics.append(step)
            return step

        def _end_step(
            step: DiagnosticStep,
            error: Exception | None = None,
            error_type: str | None = None,
            error_desc: str | None = None,
        ):
            step.end_time = datetime.now(UTC)
            step.duration_seconds = (step.end_time - step.start_time).total_seconds()
            if error or error_type:
                step.success = False
                step.error_type = error_type or type(error).__name__
                step.error_desc = error_desc or str(error)

        # 1-2. İKN Doğrulama ve Veritabanı Okuma
        step_db = _track_step("Veritabanı Okuma")
        try:
            if not ikn or not ikn.strip():
                raise ValueError("İKN boş olamaz.")

            tender_record = self.tender_repository.get_by_ikn(ikn)
            result.tender_title = tender_record.adi or ""
            result.tender_category = getattr(tender_record, "ihale_turu", "Bilinmiyor")
            _end_step(step_db)
        except (ValueError, TenderNotFoundError) as e:
            _end_step(step_db, e, "kullanıcı girdisi hatası", str(e))
            result.diagnostics = diagnostics
            raise
        except Exception as e:
            # PostgreSQL hatası varsayımı
            error_msg = f"PostgreSQL hatası: {str(e)}"
            _end_step(step_db, e, "analiz başarısız", error_msg)
            result.diagnostics = diagnostics
            logger.info(
                f"Step '{step_db.name}' failed for ikn={ikn}, analysis_id={analysis_id}, duration={step_db.duration_seconds:.2f}s"
            )
            raise DatabaseAccessError(error_msg) from e

        # 3. İhale Metni (Document) Hazırlama
        step_doc = _track_step("İhale Metni Hazırlama")
        try:
            document = self.document_builder.build(tender_record)
            tender_context_lines = []

            sections = (
                document.sections if hasattr(document, "sections") else document.get("sections", [])
            )
            for sec in sections:
                if hasattr(sec, "text"):
                    tender_context_lines.append(sec.text)
                else:
                    tender_context_lines.append(sec.get("text", ""))

            raw_tender_context = "\n\n".join(tender_context_lines)

            if not raw_tender_context.strip():
                # Eksik ihale bilgisi
                _end_step(step_doc, None, "eksik_bilgi", "İhale metni boş")
                raise TenderAnalysisError("İhale metni boş veya oluşturulamadı.")
            else:
                _end_step(step_doc)

            logger.info(
                f"Step '{step_doc.name}' success for ikn={ikn}, analysis_id={analysis_id}, duration={step_doc.duration_seconds:.2f}s"
            )
        except TenderAnalysisError:
            raise
        except Exception as e:
            _end_step(step_doc, e, "analiz başarısız", "Doküman oluşturma hatası")
            result.diagnostics = diagnostics
            logger.info(
                f"Step '{step_doc.name}' failed for ikn={ikn}, analysis_id={analysis_id}, duration={step_doc.duration_seconds:.2f}s"
            )
            raise TenderAnalysisError("Doküman oluşturma hatası") from e

        # 4. Sorgu Oluşturma
        step_query = _track_step("Sorgu Oluşturma")
        try:
            kapsam = getattr(tender_record, "kapsam", "") or ""
            okas_codes = getattr(tender_record, "okas_codes", [])
            okas_text = " ".join(c.ad for c in okas_codes if c.ad)

            query_text = f"{tender_record.adi} {kapsam} {okas_text}".strip()
            _end_step(step_query)
            logger.info(
                f"Step '{step_query.name}' success for ikn={ikn}, analysis_id={analysis_id}, duration={step_query.duration_seconds:.2f}s"
            )
        except Exception as e:
            result.diagnostics = diagnostics
            logger.info(f"Step '{step_query.name}' failed for ikn={ikn}, analysis_id={analysis_id}")
            raise TenderAnalysisError("Sorgu metni hatası") from e
        finally:
            if step_query.end_time is None:
                _end_step(
                    step_query,
                    Exception("Bilinmeyen Hata"),
                    "analiz başarısız",
                    "Sorgu metni hatası",
                )

        # 5. Profil Yönlendirme
        step_route = _track_step("Profil Belirleme")
        primary_profile = None
        secondary_profiles = []
        try:
            route_signal = self.query_router.analyze(query_text)
            if route_signal.primary_groups:
                primary_profile = route_signal.primary_groups[0]
                secondary_profiles = route_signal.primary_groups[1:]
            else:
                primary_profile = "BELIRLENEMEDI"

            _end_step(step_route)
            logger.info(
                f"Step '{step_route.name}' success for ikn={ikn}, analysis_id={analysis_id}, duration={step_route.duration_seconds:.2f}s"
            )
        except Exception as e:
            _end_step(step_route, e, "ProfileRoutingError", str(e))
            result.diagnostics = diagnostics
            logger.error(
                f"Step '{step_route.name}' failed for ikn={ikn}, analysis_id={analysis_id}: {e}"
            )
            from app.pipeline.exceptions import ProfileRoutingError

            raise ProfileRoutingError(f"Profil yönlendirme servisinde teknik hata: {e}") from e

        # 6-10. Geçmiş İhaleleri Getirme ve Tekilleştirme
        step_rag = _track_step("Geçmiş İhaleleri Arama (RAG)")
        evidence_records: list[EvidenceRecord] = []
        try:
            search_results = self.retriever.retrieve(query=query_text, limit=20)

            seen_ikns = set()
            tender_chunk_counts = {}

            for res in search_results:
                if len(seen_ikns) >= self.max_evidence_chunks:
                    break

                res_ikn = getattr(res, "ikn", None)
                if not res_ikn or res_ikn == ikn:
                    continue

                if tender_chunk_counts.get(res_ikn, 0) >= self.max_chunks_per_tender:
                    continue

                seen_ikns.add(res_ikn)
                tender_chunk_counts[res_ikn] = tender_chunk_counts.get(res_ikn, 0) + 1

                # Pick the best chunk deterministically based on combined score
                chunks = getattr(res, "evidence_chunks", [])
                res_scores = getattr(res, "scores", None)
                res_final = getattr(res_scores, "final", 0.0) if res_scores else 0.0

                def _safe_float(val):
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        return None

                if chunks:

                    def _get_chunk_score_tuple(c):
                        score_val = 0.0
                        source = ""

                        f_score = _safe_float(getattr(c, "final_score", None))
                        c_score = _safe_float(getattr(c, "combined_score", None))
                        r_score = _safe_float(getattr(c, "retrieval_score", None))

                        if f_score is not None:
                            score_val = f_score
                            source = "final_score"
                        elif c_score is not None:
                            score_val = c_score
                            source = "combined_score"
                        elif r_score is not None:
                            score_val = r_score
                            source = "retrieval_score"
                        elif res_final > 0:
                            score_val = _safe_float(res_final) or 0.0
                            source = "res_final"
                        else:
                            score_val = _safe_float(getattr(c, "semantic_score", 0.0)) or 0.0
                            source = "semantic_score"

                        c_id_str = str(getattr(c, "chunk_id", "") or "")
                        s_id_str = str(getattr(c, "section_id", "") or "")
                        match = re.search(r"\d+", c_id_str)
                        c_num = int(match.group()) if match else float("inf")

                        return (-score_val, c_num, c_id_str, s_id_str, score_val, source)

                    best_c = min(chunks, key=_get_chunk_score_tuple)
                    _, _, _, _, best_score_val, best_source = _get_chunk_score_tuple(best_c)

                    text_excerpt = getattr(best_c, "text", "")[:500]
                    if type(text_excerpt).__name__ == "Mock":
                        text_excerpt = ""
                    chunk_id = getattr(best_c, "chunk_id", "")
                    if type(chunk_id).__name__ == "Mock":
                        chunk_id = ""
                    section_id = getattr(best_c, "section_id", "")
                    if type(section_id).__name__ == "Mock":
                        section_id = ""
                    semantic = _safe_float(getattr(best_c, "semantic_score", 0.0)) or 0.0
                else:
                    text_excerpt = getattr(res, "chunk_title", "")[:500]
                    if type(text_excerpt).__name__ == "Mock":
                        text_excerpt = ""
                    chunk_id = ""
                    section_id = ""
                    semantic = 0.0
                    best_score_val = _safe_float(res_final) or 0.0
                    best_source = "res_final"

                evidence_records.append(
                    EvidenceRecord(
                        ikn=res_ikn,
                        chunk_id=chunk_id,
                        section_id=section_id,
                        source_table="tenders",
                        source_record_ids=[],
                        profile_code=getattr(res, "primary_profile_code", "Bilinmiyor"),
                        retrieval_score=best_score_val,
                        text_excerpt=text_excerpt,
                        semantic_score=semantic,
                        lexical_score=_safe_float(getattr(res_scores, "lexical", 0.0))
                        if res_scores
                        else 0.0,
                        profile_score=_safe_float(getattr(res_scores, "profile", 0.0))
                        if res_scores
                        else 0.0,
                        metadata_score=_safe_float(getattr(res_scores, "metadata", 0.0))
                        if res_scores
                        else 0.0,
                        selection_score_source=best_source,
                    )
                )

            # Sort overall evidence by score descending to ensure deterministic insertion later
            evidence_records.sort(key=lambda x: x.retrieval_score, reverse=True)
            result.evidence = evidence_records
            _end_step(step_rag)
            logger.info(
                f"Step '{step_rag.name}' success for ikn={ikn}, analysis_id={analysis_id}, duration={step_rag.duration_seconds:.2f}s"
            )
        except Exception as e:
            _end_step(step_rag, e, "analiz başarısız", f"Qdrant hatası: {str(e)}")
            result.diagnostics = diagnostics
            logger.info(f"Step '{step_rag.name}' failed for ikn={ikn}, analysis_id={analysis_id}")
            raise DatabaseAccessError(f"Qdrant hatası: {e}") from e

        # 11. Dinamik Şirket Bağlamı (IsbakProfileLoader)
        step_company = _track_step("Şirket Bağlamı Oluşturma")
        try:
            if primary_profile == "BELIRLENEMEDI":
                company_context = (
                    "Şirket profili eşleştirilemedi. Profil bazlı değerlendirme atlandı."
                )
                result.primary_profile_code = "BELIRLENEMEDI"
                result.secondary_profile_codes = []
                result.matched_capabilities = []
                evaluation_rules = {}
            else:
                context_dict = self.profile_loader.build_evaluation_context(
                    primary_code=primary_profile, secondary_codes=secondary_profiles
                )
                from app.config import get_settings
                settings = get_settings()

                company_context_legacy = format_company_context(context_dict)
                company_context_compact, compact_stats = format_company_context_compact(context_dict)

                company_context_mode = "compact" if settings.qwen_compact_company_context else "legacy"
                legacy_chars = len(company_context_legacy)
                compact_chars = compact_stats["compact_chars"]
                saved_chars = legacy_chars - compact_chars
                saved_percent = round((saved_chars / legacy_chars * 100), 2) if legacy_chars else 0

                logger.info(
                    f"[COMPANY_CONTEXT_COMPRESSION] "
                    f"company_context_mode={company_context_mode} "
                    f"legacy_company_context_chars={legacy_chars} "
                    f"compact_company_context_chars={compact_chars} "
                    f"company_context_saved_chars={saved_chars} "
                    f"company_context_saved_percent={saved_percent} "
                    f"verified_capabilities_original_count={compact_stats['verified_capabilities_original_count']} "
                    f"verified_capabilities_compact_count={compact_stats['verified_capabilities_compact_count']} "
                    f"verified_documents_original_count={compact_stats['verified_documents_original_count']} "
                    f"verified_documents_compact_count={compact_stats['verified_documents_compact_count']} "
                    f"exact_duplicate_capabilities_removed={compact_stats['exact_duplicate_capabilities_removed']} "
                    f"exact_duplicate_documents_removed={compact_stats['exact_duplicate_documents_removed']} "
                    f"missing_info_count={compact_stats['missing_info_count']}"
                )

                company_context = company_context_compact if settings.qwen_compact_company_context else company_context_legacy

                result.primary_profile_code = primary_profile
                result.secondary_profile_codes = secondary_profiles
                result.matched_capabilities = []
                evaluation_rules = context_dict.get("evaluation_rules", {})

            _end_step(step_company)
            logger.info(
                f"Step '{step_company.name}' success for ikn={ikn}, analysis_id={analysis_id}, duration={step_company.duration_seconds:.2f}s"
            )
        except Exception as e:
            _end_step(step_company, e, type(e).__name__, str(e))
            result.diagnostics = diagnostics
            logger.error(
                f"Step '{step_company.name}' failed for ikn={ikn}, analysis_id={analysis_id}: {e}"
            )
            raise TenderAnalysisError("Şirket profili yükleme hatası") from e

        # 12. İhale Bağlamı Birleştirme ve Sınırlandırma
        step_context = _track_step("Bağlam Sınırlandırma")
        try:
            from app.config import get_settings

            settings = get_settings()
            max_chars = settings.max_tender_context_chars

            sections_by_type = {
                "tender_main": [],
                "announcement": [],
                "characteristics": [],
                "missing_info": [],
                "okas_codes": [],
                "other": [],
            }

            doc_sections = (
                document.sections if hasattr(document, "sections") else document.get("sections", [])
            )
            for sec in doc_sections:
                meta = sec.metadata if hasattr(sec, "metadata") else sec.get("metadata", {})
                text = sec.text if hasattr(sec, "text") else sec.get("text", "")
                stype = meta.get("section_type")
                if stype in sections_by_type:
                    sections_by_type[stype].append(text)
                else:
                    sections_by_type["other"].append(text)

            def get_text(stype: str) -> str:
                return "\n\n".join(t for t in sections_by_type[stype] if t.strip())

            prio_blocks = [
                ("tender_main", get_text("tender_main")),
                ("announcement", get_text("announcement")),
                ("characteristics", get_text("characteristics")),
                ("missing_info", get_text("missing_info")),
                ("okas_codes", get_text("okas_codes")),
                ("other", get_text("other")),
            ]

            full_tender_context = ""
            current_chars = 0

            omitted_evidence_count = 0
            omitted_tender_section_count = 0
            critical_context_omitted = False

            prefix_new = "YENİ İHALE ŞARTLARI:\n"
            full_tender_context += prefix_new
            current_chars += len(prefix_new)

            original_chars_total = len(prefix_new)

            def safe_truncate(text: str, max_len: int) -> str:
                if len(text) <= max_len:
                    return text
                truncated = text[:max_len]
                for sep in ("\n\n", "\n", ". "):
                    pos = truncated.rfind(sep)
                    if pos > max_len * 0.5:
                        return truncated[: pos + len(sep)].strip()
                pos = truncated.rfind(" ")
                return truncated[:pos].strip() if pos > 0 else truncated

            for name, text in prio_blocks:
                if not text:
                    continue
                original_chars_total += len(text) + 2
                space_left = max_chars - current_chars
                if space_left <= 0:
                    omitted_tender_section_count += 1
                    if name in ("tender_main", "characteristics"):
                        critical_context_omitted = True
                    continue

                text_to_add = text + "\n\n"
                if len(text_to_add) <= space_left:
                    full_tender_context += text_to_add
                    current_chars += len(text_to_add)
                else:
                    if name == "tender_main":
                        safe_text = safe_truncate(text, space_left)
                        if safe_text:
                            full_tender_context += safe_text + "\n\n"
                            current_chars += len(safe_text) + 2
                        critical_context_omitted = True
                    else:
                        omitted_tender_section_count += 1
                        if name == "characteristics":
                            critical_context_omitted = True

            if evidence_records:
                prefix_ev = "GEÇMİŞ İHALE KANITLARI:\n(Geçmiş ihale benzerliğinin şirket yeterliliğini tek başına kanıtlamadığı açıkça unutulmamalıdır.)\n"
                original_chars_total += len(prefix_ev)

                space_left = max_chars - current_chars
                if space_left > len(prefix_ev):
                    full_tender_context += prefix_ev
                    current_chars += len(prefix_ev)

                    for i, ev in enumerate(evidence_records, 1):
                        ev_text = f"[{i}] İKN: {ev.ikn} - {ev.text_excerpt}"
                        original_chars_total += len(ev_text) + 1

                        space_left = max_chars - current_chars
                        if space_left <= 0:
                            omitted_evidence_count += 1
                            continue

                        text_to_add = ev_text + "\n"
                        if len(text_to_add) <= space_left:
                            full_tender_context += text_to_add
                            current_chars += len(text_to_add)
                        else:
                            header = f"[{i}] İKN: {ev.ikn} - "
                            avail = space_left - len(header) - 1
                            if avail > 50:
                                trunc_excerpt = safe_truncate(ev.text_excerpt, avail)
                                if trunc_excerpt:
                                    final_text = header + trunc_excerpt + "\n"
                                    full_tender_context += final_text
                                    current_chars += len(final_text)
                                else:
                                    omitted_evidence_count += 1
                            else:
                                omitted_evidence_count += 1
                else:
                    omitted_evidence_count += len(evidence_records)

            is_truncated = (
                omitted_evidence_count > 0
                or omitted_tender_section_count > 0
                or critical_context_omitted
            )

            if is_truncated:
                logger.warning(
                    f"Context truncated for analysis_id={analysis_id}. Evidences omitted: {omitted_evidence_count}, Sections omitted: {omitted_tender_section_count}"
                )

            final_context_chars = len(full_tender_context)

            assert final_context_chars <= max_chars, (
                f"Bağlam sınırı aşıldı: {final_context_chars} > {max_chars}"
            )

            result.metadata.update(
                {
                    "original_context_chars": original_chars_total,
                    "context_limit_chars": max_chars,
                    "final_context_chars": final_context_chars,
                    "context_truncated": is_truncated,
                    "omitted_evidence_count": omitted_evidence_count,
                    "omitted_tender_section_count": omitted_tender_section_count,
                    "critical_context_omitted": critical_context_omitted,
                }
            )

            _end_step(step_context)
            logger.info(
                f"Step '{step_context.name}' success for ikn={ikn}, analysis_id={analysis_id}, duration={step_context.duration_seconds:.2f}s"
            )
        except Exception as e:
            _end_step(step_context, e, "ContextLimitError", str(e))
            raise TenderAnalysisError(f"Bağlam sınırı ayarlanırken hata: {e}") from e

        # 13-14. IsbakDecisionPipeline Çağrımı
        step_decision = _track_step("Karar Modeli Çağrımı")
        try:
            if primary_profile == "BELIRLENEMEDI":
                from app.decision.models import FinalTenderDecision, ModelDecision, ValidationResult

                final_decision = FinalTenderDecision(
                    tender_id=str(tender_record.id),
                    ikn=ikn,
                    category_code=result.tender_category or "GENEL",
                    final_decision="inceleme_gerekli",
                    final_confidence=0.0,
                    primary_model=ModelDecision(
                        model_name="sistem_bypassi",
                        decision="inceleme_gerekli",
                        confidence=0.0,
                        birincil_profil_kodu="BELIRLENEMEDI",
                        ikincil_profil_kodlari=[],
                        uygunluk_gerekceleri=[],
                        uygunsuzluk_gerekceleri=[],
                        zorunlu_kriter_sonuclari=[],
                        raw_response={
                            "_metadata": {
                                "prompt_version": "bypass",
                                "attempt_count": 0,
                                "timeout_seconds": 0,
                            }
                        },
                    ),
                    validation=ValidationResult(
                        passed=False,
                        contradictions=["Profil eşleşmesi bulunamadı"],
                        missing_required_evidence=[],
                        forced_decision="inceleme_gerekli",
                    ),
                    human_review_required=True,
                    reasons=["Hiçbir kurum profili ihale ile eşleşmedi."],
                )
                result.decision = final_decision
                _end_step(step_decision)

                result.metadata["primary_model"] = "sistem_bypassi"
                result.metadata["primary_prompt_version"] = "bypass"
                result.metadata["primary_attempt_count"] = 0
                result.metadata["primary_timeout_seconds"] = 0

                logger.info(
                    f"Step '{step_decision.name}' bypassed due to no profile match for ikn={ikn}"
                )
            else:
                final_decision = self.decision_pipeline.run(
                    tender_id=str(tender_record.id),
                    ikn=ikn,
                    tender_name=tender_record.adi,
                    authority_name=tender_record.idare_adi,
                    category_code=result.tender_category or "GENEL",
                    primary_profile_code=primary_profile,
                    secondary_profile_codes=secondary_profiles,
                    tender_context=full_tender_context,
                    company_context=company_context,
                    evidence_count=len(evidence_records) - omitted_evidence_count,
                    evaluation_rules=evaluation_rules,
                )
                result.decision = final_decision
                _end_step(step_decision)

                # Extract metadata from raw_response
                primary_meta = final_decision.primary_model.raw_response.get("_metadata", {})
                result.metadata["primary_model"] = final_decision.primary_model.model_name
                result.metadata["primary_prompt_version"] = primary_meta.get("prompt_version")
                result.metadata["primary_attempt_count"] = primary_meta.get("attempt_count")
                result.metadata["primary_timeout_seconds"] = primary_meta.get("timeout_seconds")

                logger.info(
                    f"Step '{step_decision.name}' success for ikn={ikn}, analysis_id={analysis_id}, duration={step_decision.duration_seconds:.2f}s"
                )
        except DecisionServiceError as e:
            _end_step(step_decision, e, "analiz başarısız", f"Ollama hatası: {str(e)}")
            result.diagnostics = diagnostics
            logger.info(
                f"Step '{step_decision.name}' failed for ikn={ikn}, analysis_id={analysis_id}"
            )
            raise e
        except Exception as e:
            _end_step(step_decision, e, "analiz başarısız", f"Bilinmeyen hata: {str(e)}")
            result.diagnostics = diagnostics
            logger.info(
                f"Step '{step_decision.name}' failed for ikn={ikn}, analysis_id={analysis_id}"
            )
            raise DecisionServiceError(f"Beklenmeyen model hatası: {e}") from e

        result.diagnostics = diagnostics
        logger.info(
            f"Analysis completed successfully: analysis_id={analysis_id}, ikn={ikn}, final_decision={final_decision.final_decision}"
        )
        return result
