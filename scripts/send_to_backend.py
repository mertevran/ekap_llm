#!/usr/bin/env python3
"""LLM V2 backend-sözleşme JSONL çıktısını mevcut backend API'ye gönderir."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from app.integration.backend_contract import BackendContractError, validate_backend_item

DEFAULT_BACKEND_API_URL = "http://127.0.0.1:5080/api/ai-evaluations/import-batch"
DEFAULT_TIMEOUT_SECONDS = 60.0


def _load_items(path: Path) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    skipped = 0

    with path.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise BackendContractError("JSONL satırı nesne olmalıdır.")
                items.append(validate_backend_item(value))
            except (json.JSONDecodeError, BackendContractError) as exc:
                skipped += 1
                print(
                    f"UYARI: satır {line_number} gönderilmeyecek: {exc}",
                    file=sys.stderr,
                )

    return items, skipped


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    token = os.getenv("BACKEND_API_BEARER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def send_evaluations(
    jsonl_path: str | Path,
    *,
    backend_url: str | None = None,
    timeout_seconds: float | None = None,
    dry_run: bool = False,
) -> bool:
    path = Path(jsonl_path)
    if not path.is_file():
        print(f"HATA: dosya bulunamadı: {path}", file=sys.stderr)
        return False

    items, skipped = _load_items(path)
    if not items:
        print("HATA: gönderilecek geçerli kayıt bulunamadı.", file=sys.stderr)
        return False

    url = (
        backend_url
        or os.getenv("BACKEND_API_URL", "").strip()
        or DEFAULT_BACKEND_API_URL
    )
    timeout = timeout_seconds
    if timeout is None:
        timeout = float(
            os.getenv("BACKEND_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        )

    print(f"Backend sözleşme dosyası: {path}")
    print(f"Geçerli kayıt: {len(items)} | Atlanan kayıt: {skipped}")
    print(f"Hedef endpoint: {url}")

    if dry_run:
        print("DRY-RUN: HTTP isteği gönderilmedi; sözleşme doğrulaması başarılı.")
        return True

    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(url, json=items, headers=_headers())
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:1000]
        print(
            f"HTTP HATASI ({exc.response.status_code}): {body}",
            file=sys.stderr,
        )
        return False
    except httpx.HTTPError as exc:
        print(f"BAĞLANTI HATASI: {exc}", file=sys.stderr)
        return False

    print(f"SUCCESS ({response.status_code}): {response.text[:1000]}")
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "backend_ai_evaluations.jsonl dosyasını mevcut backend "
            "import-batch endpoint'ine gönderir."
        )
    )
    parser.add_argument("jsonl_path", help="Backend sözleşme biçimindeki JSONL dosyası")
    parser.add_argument(
        "--backend-url",
        default=None,
        help=(
            "Endpoint URL. Verilmezse BACKEND_API_URL, o da yoksa "
            f"{DEFAULT_BACKEND_API_URL} kullanılır."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout (saniye). Varsayılan BACKEND_TIMEOUT_SECONDS veya 60.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dosyayı doğrular fakat backend'e HTTP isteği göndermez.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    ok = send_evaluations(
        args.jsonl_path,
        backend_url=args.backend_url,
        timeout_seconds=args.timeout,
        dry_run=args.dry_run,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
