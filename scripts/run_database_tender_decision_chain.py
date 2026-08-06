#!/usr/bin/env python
"""Karar bağlamını zorunlu olarak PostgreSQL'den kuran güvenli giriş noktası."""

from __future__ import annotations

import sys

import scripts.run_tender_decision_chain as runner


def main() -> int:
    if "--source-mode" in sys.argv[1:]:
        raise ValueError(
            "Bu giriş noktası kaynak modunu kendisi database olarak ayarlar; "
            "--source-mode vermeyin."
        )
    sys.argv = [
        "run_tender_decision_chain.py",
        *sys.argv[1:],
        "--source-mode",
        "database",
    ]
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
