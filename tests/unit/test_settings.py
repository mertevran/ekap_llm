from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.config.settings import Settings


def test_settings_load_successfully() -> None:
    settings = get_settings()

    assert settings.app_name
    assert isinstance(settings.database_port, int)
    assert 1 <= settings.database_port <= 65535
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.qwen_model == "qwen3.5:4b-q4_K_M"
    assert settings.gemma_model == "gemma4:e2b-it-q4_K_M"
    assert settings.phi_model == "phi4-mini:latest"


def test_project_paths_are_absolute() -> None:
    settings = get_settings()

    paths = [
        settings.qdrant_path,
        settings.model_cache_path,
        settings.output_dir,
        settings.report_dir,
        settings.log_dir,
    ]

    assert all(isinstance(path, Path) for path in paths)
    assert all(path.is_absolute() for path in paths)


def test_required_directories_exist() -> None:
    settings = get_settings()

    directories = [
        settings.qdrant_path,
        settings.model_cache_path,
        settings.output_dir,
        settings.report_dir,
        settings.log_dir,
    ]

    assert all(directory.is_dir() for directory in directories)


def test_settings_reject_qwen2() -> None:
    with pytest.raises(
        ValidationError, match="Eski model adları \\(Qwen2, qwen3:4b vb.\\) reddedildi"
    ):
        Settings(qwen_model="qwen2.5:7b")

    with pytest.raises(
        ValidationError, match="Eski model adları \\(Qwen2, qwen3:4b vb.\\) reddedildi"
    ):
        Settings(qwen_model="qwen3:4b")


def test_settings_reject_gemma2() -> None:
    with pytest.raises(
        ValidationError, match="Eski model adları \\(Gemma2, gemma3:4b vb.\\) reddedildi"
    ):
        Settings(gemma_model="gemma2:9b")

    with pytest.raises(
        ValidationError, match="Eski model adları \\(Gemma2, gemma3:4b vb.\\) reddedildi"
    ):
        Settings(gemma_model="gemma3:4b")


def test_settings_reject_unknown_model() -> None:
    with pytest.raises(ValidationError, match="İzin verilmeyen Qwen modeli"):
        Settings(qwen_model="unknown_qwen:1b")

    with pytest.raises(ValidationError, match="İzin verilmeyen Gemma modeli"):
        Settings(gemma_model="unknown_gemma:1b")
