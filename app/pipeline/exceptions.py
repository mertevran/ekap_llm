class TenderAnalysisError(Exception):
    """İhale analizi sırasında oluşan temel özel hata."""

    pass


class DecisionServiceError(TenderAnalysisError):
    """Karar modeli (LLM) servisleriyle iletişim veya yapılandırma hataları."""

    pass


class DatabaseAccessError(TenderAnalysisError):
    """Veritabanı veya indeksleme sunucusuna erişim hatası."""

    pass


class ReportExistsError(TenderAnalysisError):
    """Aynı raporun zaten var olması durumu."""

    pass


class ProfileRoutingError(TenderAnalysisError):
    """Profil yönlendirme (router) işlemi sırasında oluşan teknik hata."""

    pass


class TruncatedModelOutput(DecisionServiceError):
    """Model çıktısının yarıda kesildiği durumu ifade eder."""

    pass
