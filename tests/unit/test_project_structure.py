from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_required_directories_exist() -> None:
    required_directories = [
        "app/config",
        "app/database",
        "app/domain",
        "app/documents",
        "app/rag",
        "app/models/prompts",
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

    missing = [path for path in required_directories if not (PROJECT_ROOT / path).is_dir()]

    assert not missing, f"Eksik klasörler: {missing}"


def test_required_root_files_exist() -> None:
    required_files = [
        ".env.example",
        ".gitignore",
        "README.md",
        "CHANGELOG.md",
        "requirements.txt",
        "pytest.ini",
    ]

    missing = [path for path in required_files if not (PROJECT_ROOT / path).is_file()]

    assert not missing, f"Eksik dosyalar: {missing}"
