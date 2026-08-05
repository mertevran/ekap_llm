from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

HASH_VERSION = "tender_source_hash_v1"


def _normalize_scalar(value: Any) -> Any:
    """Değeri kararlı JSON çıktısına uygun hâle getirir."""
    if value is None:
        return None

    if isinstance(value, str):
        return " ".join(value.split())

    if isinstance(value, (bool, int)):
        return value

    if isinstance(value, float):
        return format(value, ".15g")

    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Enum):
        return _normalize_scalar(value.value)

    if isinstance(value, Path):
        return str(value)

    return str(value)


def _canonicalize(value: Any) -> Any:
    """Nesneyi sırası kararlı, JSON uyumlu bir yapıya dönüştürür."""
    if is_dataclass(value):
        return _canonicalize(asdict(value))

    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }

    if isinstance(value, set):
        normalized_items = [_canonicalize(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_canonicalize(item) for item in value]

    if hasattr(value, "model_dump"):
        return _canonicalize(
            value.model_dump(
                mode="json",
                exclude_none=False,
            )
        )

    if hasattr(value, "dict") and callable(value.dict):
        return _canonicalize(value.dict())

    if hasattr(value, "__dict__"):
        public_values = {key: item for key, item in vars(value).items() if not key.startswith("_")}
        return _canonicalize(public_values)

    return _normalize_scalar(value)


def build_tender_source_payload(
    tender: Any,
    *,
    classifier_version: str,
) -> dict[str, Any]:
    """İhale içeriğinden özet üretiminde kullanılacak kanonik yapıyı kurar."""
    return {
        "hash_version": HASH_VERSION,
        "classifier_version": classifier_version,
        "tender": _canonicalize(tender),
    }


def calculate_tender_source_hash(
    tender: Any,
    *,
    classifier_version: str,
) -> str:
    """İhalenin bütün içerik ve ilişkili kayıtları için SHA-256 özeti üretir."""
    payload = build_tender_source_payload(
        tender,
        classifier_version=classifier_version,
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(serialized).hexdigest()


__all__ = [
    "HASH_VERSION",
    "build_tender_source_payload",
    "calculate_tender_source_hash",
]
