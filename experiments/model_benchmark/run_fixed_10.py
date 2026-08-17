from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

FIXED_IKNS = [
    "2026/1362363",
    "2026/1445546",
    "2026/1402874",
    "2026/1305224",
    "2026/1415912",
    "2026/1304745",
    "2026/1312787",
    "2026/1422720",
    "2026/1260175",
    "2026/1327340",
]

SCRIPT = Path("scripts/run_tender_decision_chain.py").resolve()

spec = importlib.util.spec_from_file_location(
    "fixed_tender_runner",
    SCRIPT,
)

if spec is None or spec.loader is None:
    raise RuntimeError("Ana karar betiği yüklenemedi.")

runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

original_get_active_tenders = (
    runner.TenderRepository.get_active_tenders
)


def fixed_get_active_tenders(self, limit=None):
    records = self.get_by_ikns(FIXED_IKNS)

    by_ikn = {
        str(record.ikn).strip(): record
        for record in records
    }

    missing = [
        ikn
        for ikn in FIXED_IKNS
        if ikn not in by_ikn
    ]

    if missing:
        raise RuntimeError(
            "PostgreSQL'de bulunamayan sabit IKN'ler: "
            + ", ".join(missing)
        )

    ordered = [
        by_ikn[ikn]
        for ikn in FIXED_IKNS
    ]

    if limit is not None:
        ordered = ordered[:limit]

    return ordered


runner.TenderRepository.get_active_tenders = (
    fixed_get_active_tenders
)

sys.argv = [
    str(SCRIPT),
    "--source-mode",
    "database",
    "--selection-mode",
    "database-sequential",
    "--max-decisions",
    "10",
    "--random-seed",
    "42",
    "--allow-inactive-database-records",
    "--report-dir",
    sys.argv[1],
    "--log-level",
    "INFO",
]

runner.main()
