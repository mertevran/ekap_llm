import logging

from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import FinalTenderDecision
from app.reporting.decision_reporter import DecisionReporter
from app.retrieval.isbak_tender_retriever import IsbakTenderRetriever

logger = logging.getLogger(__name__)


class IsbakCandidateEvaluator:
    """
    Tüm aktif ihaleler içerisinden İSBAK profillerine uygun olanları
    FAISS üzerinden bulur ve model zincirinden geçirerek değerlendirir.
    Değerlendirilmeyenler 'not_evaluated' statüsünde kalır (zaten FAISS'te uygun/uygun_değil tutulmaz).
    """

    def __init__(
        self,
        retriever: IsbakTenderRetriever,
        decision_pipeline: IsbakDecisionPipeline,
        profile_loader: IsbakProfileLoader,
        reporter: DecisionReporter | None = None,
    ) -> None:
        self.retriever = retriever
        self.decision_pipeline = decision_pipeline
        self.profile_loader = profile_loader
        self.reporter = reporter or DecisionReporter()

    def run_pipeline(self) -> list[FinalTenderDecision]:
        logger.info("Aday ihale değerlendirme boru hattı başlatılıyor...")

        try:
            registry = self.profile_loader.registry
            if not registry:
                logger.error("Profil listesi yüklenemedi.")
                return []
        except Exception as e:
            logger.error(f"Profiller okunurken hata: {e}")
            return []

        evaluated_ikns: set[str] = set()
        final_decisions: list[FinalTenderDecision] = []

        # Profil başına sorgular (Ana Sorgu, Ekipman, Yeterlik)
        for profile_key, summary in registry.profiles.items():
            if not summary.is_active:
                continue

            logger.info(f"Profil değerlendiriliyor: {summary.name} ({profile_key})")

            # Profil dosyasını yükle
            profile_data = self.profile_loader.load_profile(profile_key)
            if not profile_data:
                continue

            search_queries = profile_data.search_queries

            for query in search_queries:
                logger.info(f"Sorgu çalıştırılıyor: '{query}'")

                try:
                    # FAISS_MAX_TENDERS_PER_PROFILE retriever içerisinde ayarlanmıştır.
                    search_results = self.retriever.retrieve(query=query)
                except Exception as e:
                    logger.error(f"Arama sırasında hata: {e}")
                    continue

                for candidate in search_results:
                    ikn = candidate.ikn
                    if not ikn or ikn in evaluated_ikns:
                        continue

                    evaluated_ikns.add(ikn)

                    llm_context = self.retriever.build_llm_context(candidate)
                    tender_context = (
                        f"Ana Bilgiler:\n{llm_context.main_information}\n\n"
                        f"Yeterlik:\n{llm_context.qualification_requirements}\n\n"
                        f"Teknik Şartlar:\n{llm_context.technical_requirements}\n\n"
                        f"Tarihler:\n{llm_context.dates_and_deadlines}"
                    )

                    company_context_dict = self.profile_loader.build_evaluation_context(
                        primary_code=profile_key, secondary_codes=candidate.profile_codes
                    )
                    company_context = str(company_context_dict)

                    valid_chunk_ids = [c.chunk_id for c in candidate.evidence_chunks]

                    try:
                        decision = self.decision_pipeline.run(
                            tender_id=candidate.tender_id,
                            ikn=ikn,
                            tender_name=candidate.tender_name,
                            authority_name=candidate.idare_adi,
                            category_code=candidate.ihale_turu,
                            primary_profile_code=profile_key,
                            secondary_profile_codes=candidate.profile_codes,
                            tender_context=tender_context,
                            company_context=company_context,
                            evaluation_rules=profile_data.evaluation_rules,
                            evidence_count=len(candidate.evidence_chunks),
                            valid_chunk_ids=valid_chunk_ids,
                        )

                        final_decisions.append(decision)
                    except Exception as e:
                        logger.error(f"İKN {ikn} değerlendirmesinde kritik hata: {e}")

        logger.info(
            f"Toplam {len(final_decisions)} ihale değerlendirildi. Raporlar yazdırılıyor..."
        )
        self.reporter.write_reports(final_decisions)

        return final_decisions
