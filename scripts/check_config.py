from __future__ import annotations

from app.config import get_settings


def mask_secret(value: str) -> str:
    if not value:
        return "(boş)"

    if len(value) <= 4:
        return "*" * len(value)

    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def main() -> int:
    settings = get_settings()

    print("EKAP Üç Modelli RAG - Yapılandırma Kontrolü")
    print("-" * 55)
    print(f"Uygulama       : {settings.app_name}")
    print(f"Ortam           : {settings.app_env}")
    print(f"Kayıt seviyesi  : {settings.log_level}")
    print()
    print(f"Veritabanı sunucusu : {settings.database_host or '(boş)'}")
    print(f"Veritabanı portu    : {settings.database_port}")
    print(f"Veritabanı adı      : {settings.database_name or '(boş)'}")
    print(f"Veritabanı kullanıcı: {settings.database_user or '(boş)'}")
    print(f"Veritabanı parola   : {mask_secret(settings.database_password)}")
    print()
    print(f"Qdrant yolu      : {settings.qdrant_path}")
    print(f"Model önbelleği  : {settings.model_cache_path}")
    print(f"Çıktı klasörü    : {settings.output_dir}")
    print(f"Rapor klasörü    : {settings.report_dir}")
    print(f"Kayıt klasörü    : {settings.log_dir}")
    print()
    print(f"Gömme modeli     : {settings.embedding_model}")
    print(f"Qwen modeli      : {settings.qwen_model}")
    print(f"Gemma modeli     : {settings.gemma_model}")
    print(f"Phi modeli       : {settings.phi_model}")
    print(f"Ollama adresi    : {settings.ollama_base_url}")
    print()
    print("Yapılandırma başarıyla yüklendi.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
