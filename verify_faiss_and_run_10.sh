#!/usr/bin/env bash

set -o pipefail

PYTHON_BIN=".venv/bin/python3"
VERIFY_DIR="reports/faiss_post_embedding_verify_$(date +%Y%m%d_%H%M%S)"

mkdir -p "$VERIFY_DIR"

echo "============================================================"
echo "FAISS + HASH DOGRULAMA"
echo "============================================================"
echo "Rapor: $VERIFY_DIR"
echo

# ============================================================
# 1. POSTGRESQL BAGLANTI KONTROLU
# ============================================================

echo "[1/5] PostgreSQL baglanti kontrolu..."

PYTHONPATH=. "$PYTHON_BIN" scripts/check_database.py \
    2>&1 | tee "$VERIFY_DIR/database_check.log"

DB_EXIT=${PIPESTATUS[0]}

if [ "$DB_EXIT" -ne 0 ]; then
    echo
    echo "HATA: PostgreSQL baglantisi basarisiz."
    echo "10 ihale testi baslatilmadi."
    echo "Terminal acik kalacak."
    return 0 2>/dev/null || true
fi

echo
echo "PostgreSQL baglantisi OK."
echo


# ============================================================
# 2. FAISS KAPSAMA KONTROLU
# ============================================================

echo "[2/5] FAISS kapsama kontrolu..."
echo

PYTHONPATH=. "$PYTHON_BIN" scripts/build_active_tenders_faiss.py \
    --missing-from-faiss-only \
    --dry-run \
    2>&1 | tee "$VERIFY_DIR/faiss_coverage_check.log"

FAISS_EXIT=${PIPESTATUS[0]}

if [ "$FAISS_EXIT" -ne 0 ]; then
    echo
    echo "HATA: FAISS kapsama kontrolu basarisiz."
    echo "10 ihale testi baslatilmadi."
    return 0 2>/dev/null || true
fi

ACTIVE_DB=$(
    grep -m1 'Active DB tenders:' "$VERIFY_DIR/faiss_coverage_check.log" |
    awk -F': ' '{print $2}' |
    tr -d '[:space:]'
)

FAISS_IDS=$(
    grep -m1 'FAISS unique tender IDs:' "$VERIFY_DIR/faiss_coverage_check.log" |
    awk -F': ' '{print $2}' |
    tr -d '[:space:]'
)

OVERLAP=$(
    grep -m1 '^Overlap:' "$VERIFY_DIR/faiss_coverage_check.log" |
    awk -F': ' '{print $2}' |
    tr -d '[:space:]'
)

MISSING=$(
    grep -m1 'Missing from FAISS:' "$VERIFY_DIR/faiss_coverage_check.log" |
    awk -F': ' '{print $2}' |
    tr -d '[:space:]'
)

DEVICE=$(
    grep -m1 'Embedding device:' "$VERIFY_DIR/faiss_coverage_check.log" |
    awk -F': ' '{print $2}' |
    tr -d '[:space:]'
)

echo
echo "---------- FAISS KAPSAMA ----------"
echo "Active DB       : ${ACTIVE_DB:-BULUNAMADI}"
echo "FAISS tender ID : ${FAISS_IDS:-BULUNAMADI}"
echo "Overlap         : ${OVERLAP:-BULUNAMADI}"
echo "Missing         : ${MISSING:-BULUNAMADI}"
echo "Embedding device: ${DEVICE:-BULUNAMADI}"
echo

# Beklenti:
# Once 8981 idi.
# 317 yeni uygun ihale eklendiyse:
# 8981 + 317 = 9298
#
# 10 ISBAK ihalesi bilincli olarak FAISS disinda kalacak.
# Dolayisiyla:
# Active DB = 9308
# Overlap   = 9298
# Missing   = 10


# ============================================================
# 3. TENDER_INDEX_STATE + SOURCE_HASH KONTROLU
# ============================================================

echo "[3/5] tender_index_state / source_hash kontrolu..."
echo

PYTHONPATH=. "$PYTHON_BIN" - <<'PY' \
    2>&1 | tee "$VERIFY_DIR/hash_state_check.log"

from __future__ import annotations

from app.config.settings import get_settings

try:
    import psycopg
except ImportError as exc:
    print(f"HATA: psycopg import edilemedi: {exc}")
    raise SystemExit(2)


settings = get_settings()


def first_attr(obj, *names):
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    raise AttributeError(
        "Ayar bulunamadi. Denenen alanlar: " + ", ".join(names)
    )


host = first_attr(
    settings,
    "database_host",
    "db_host",
    "postgres_host",
)

port = first_attr(
    settings,
    "database_port",
    "db_port",
    "postgres_port",
)

dbname = first_attr(
    settings,
    "database_name",
    "database_db",
    "db_name",
    "postgres_db",
)

user = first_attr(
    settings,
    "database_user",
    "db_user",
    "postgres_user",
)

password = first_attr(
    settings,
    "database_password",
    "db_password",
    "postgres_password",
)


conn = psycopg.connect(
    host=host,
    port=port,
    dbname=dbname,
    user=user,
    password=password,
)

try:
    with conn.cursor() as cur:

        print("=== GENEL INDEXED / HASH DURUMU ===")

        cur.execute(
            """
            SELECT
                COUNT(*) AS indexed_toplam,
                COUNT(*) FILTER (
                    WHERE source_hash IS NOT NULL
                      AND BTRIM(source_hash) <> ''
                ) AS hash_dolu,
                COUNT(*) FILTER (
                    WHERE source_hash IS NULL
                       OR BTRIM(source_hash) = ''
                ) AS hash_bos
            FROM llm_rag.tender_index_state
            WHERE index_status = 'indexed'
            """
        )

        indexed_total, hash_full, hash_empty = cur.fetchone()

        print(f"indexed_toplam={indexed_total}")
        print(f"hash_dolu={hash_full}")
        print(f"hash_bos={hash_empty}")
        print()

        print("=== BUGUN INDEXED YAPILANLAR ===")

        cur.execute(
            """
            SELECT
                COUNT(*) AS bugun_indexed,
                COUNT(*) FILTER (
                    WHERE source_hash IS NOT NULL
                      AND BTRIM(source_hash) <> ''
                ) AS bugun_hash_dolu,
                COUNT(*) FILTER (
                    WHERE source_hash IS NULL
                       OR BTRIM(source_hash) = ''
                ) AS bugun_hash_bos
            FROM llm_rag.tender_index_state
            WHERE index_status = 'indexed'
              AND indexed_at IS NOT NULL
              AND indexed_at::date = CURRENT_DATE
            """
        )

        today_total, today_hash_full, today_hash_empty = cur.fetchone()

        print(f"bugun_indexed={today_total}")
        print(f"bugun_hash_dolu={today_hash_full}")
        print(f"bugun_hash_bos={today_hash_empty}")
        print()

        print("=== SON 20 INDEXED KAYIT ===")

        cur.execute(
            """
            SELECT
                tender_id,
                index_status,
                CASE
                    WHEN source_hash IS NULL
                      OR BTRIM(source_hash) = ''
                    THEN 'HASH_YOK'
                    ELSE 'HASH_VAR'
                END AS hash_status,
                indexed_at
            FROM llm_rag.tender_index_state
            WHERE index_status = 'indexed'
            ORDER BY indexed_at DESC NULLS LAST
            LIMIT 20
            """
        )

        for row in cur.fetchall():
            print(
                f"{row[0]} | "
                f"{row[1]} | "
                f"{row[2]} | "
                f"{row[3]}"
            )

        print()
        print("=== SONUC ===")

        if today_hash_empty == 0:
            print("HASH_CHECK=PASS")
            print(
                "Bugun indexed olarak kaydedilen kayitlarda "
                "bos source_hash bulunmuyor."
            )
        else:
            print("HASH_CHECK=FAIL")
            print(
                f"UYARI: Bugun indexed kayitlar icinde "
                f"{today_hash_empty} adet bos source_hash var."
            )

finally:
    conn.close()
PY

HASH_EXIT=${PIPESTATUS[0]}

if [ "$HASH_EXIT" -ne 0 ]; then
    echo
    echo "HATA: source_hash kontrolu calistirilamadi."
    echo "10 ihale testi baslatilmadi."
    return 0 2>/dev/null || true
fi


# ============================================================
# 4. SONUC DEGERLENDIRMESI
# ============================================================

echo
echo "[4/5] Sonuc degerlendirmesi..."
echo

FAISS_OK=false
HASH_OK=false
CPU_OK=false

if [ "$MISSING" = "10" ]; then
    FAISS_OK=true
fi

if grep -q '^HASH_CHECK=PASS$' "$VERIFY_DIR/hash_state_check.log"; then
    HASH_OK=true
fi

if [ "$DEVICE" = "cpu" ]; then
    CPU_OK=true
fi

{
    echo "Active DB: ${ACTIVE_DB:-unknown}"
    echo "FAISS unique tender IDs: ${FAISS_IDS:-unknown}"
    echo "Overlap: ${OVERLAP:-unknown}"
    echo "Missing from FAISS: ${MISSING:-unknown}"
    echo "Embedding device: ${DEVICE:-unknown}"
    echo "FAISS_CHECK: $FAISS_OK"
    echo "HASH_CHECK: $HASH_OK"
    echo "CPU_CHECK: $CPU_OK"
} | tee "$VERIFY_DIR/final_verification.txt"

echo
echo "Beklenen yeni durum:"
echo "  Active DB       : 9308 civari"
echo "  FAISS overlap   : 9298 civari"
echo "  Missing         : 10"
echo "  Missing 10 adet : ISBAK kendi ihaleleri"
echo "  Embedding       : cpu"
echo "  Hash bos        : 0"
echo


# ============================================================
# 5. KONTROLLER GECERSE 10 CANLI KARAR TESTI
# ============================================================

echo "[5/5] 10 canlı ihale testine gecis..."
echo

if [ "$FAISS_OK" = true ] && \
   [ "$HASH_OK" = true ] && \
   [ "$CPU_OK" = true ]; then

    echo "============================================================"
    echo "TUM KRITIK KONTROLLER BASARILI"
    echo "============================================================"
    echo
    echo "317 yeni ihale FAISS'e eklenmis gorunuyor."
    echo "Geriye kalan 10 ihale ISBAK filtreli kayitlar olmali."
    echo "source_hash kontrolu basarili."
    echo "CPU kullanimi dogrulandi."
    echo
    echo "10 CANLI IHALE KARAR TESTI BASLATILIYOR..."
    echo "============================================================"
    echo

    if [ -f "run_test_10.sh" ]; then
        bash run_test_10.sh
        LIVE_EXIT=$?

        echo
        echo "10 ihale test script cikis kodu: $LIVE_EXIT"
    else
        echo "HATA: run_test_10.sh bulunamadi."
        echo "10 ihale testi baslatilamadi."
    fi

else
    echo "============================================================"
    echo "10 IHALE TESTI BASLATILMADI"
    echo "============================================================"

    if [ "$FAISS_OK" != true ]; then
        echo "FAISS kontrolu beklenen sonucu vermedi."
        echo "Beklenen Missing from FAISS: 10"
        echo "Gercek: ${MISSING:-unknown}"
    fi

    if [ "$HASH_OK" != true ]; then
        echo "source_hash kontrolu basarisiz."
    fi

    if [ "$CPU_OK" != true ]; then
        echo "Embedding device CPU olarak dogrulanamadi."
        echo "Gercek: ${DEVICE:-unknown}"
    fi

    echo
    echo "Sorun giderilmeden Qwen testi baslatilmadi."
fi

echo
echo "============================================================"
echo "ISLEM TAMAMLANDI"
echo "============================================================"
echo "Dogrulama raporu:"
echo "$VERIFY_DIR"
echo
echo "WSL terminali acik kalacak."
echo "============================================================"

