from __future__ import annotations

import platform
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]

    required_directories = [
        "app",
        "app/config",
        "app/database",
        "app/domain",
        "app/documents",
        "app/rag",
        "app/models",
        "app/validation",
        "app/pipeline",
        "app/reporting",
        "scripts",
        "data/company",
        "storage/qdrant",
        "storage/model_cache",
        "outputs",
        "reports",
        "logs",
        "tests/unit",
        "tests/integration",
        "tests/end_to_end",
        "docs",
    ]

    print("EKAP Üç Modelli RAG - Kurulum Kontrolü")
    print("-" * 50)
    print(f"Proje dizini : {project_root}")
    print(f"Python       : {sys.version.split()[0]}")
    print(f"İşletim sistemi: {platform.platform()}")
    print()

    missing_directories: list[str] = []

    for relative_path in required_directories:
        full_path = project_root / relative_path

        if full_path.is_dir():
            print(f"[OK]   {relative_path}")
        else:
            print(f"[EKSİK] {relative_path}")
            missing_directories.append(relative_path)

    print()

    if missing_directories:
        print(f"Kurulum eksik: {len(missing_directories)} klasör bulunamadı.")
        return 1

    print("Temel proje yapısı hazır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
