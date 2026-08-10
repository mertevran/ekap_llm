#!/usr/bin/env bash

set -uo pipefail

PYTHON_BIN=".venv/bin/python3"
TS="$(date +%Y%m%d_%H%M%S)"
REPORT_DIR="reports/sequential_10_full_${TS}"

mkdir -p "$REPORT_DIR"

echo "============================================================"
echo "EKAP-ISBAK"
echo "REVIZYON DOGRULAMA + 10 EN YENI IHALE CANLI TESTI"
echo "============================================================"
echo "Rapor klasoru: $REPORT_DIR"
echo


# ============================================================
# 1. CALISMA ZAMANI AYARLARI
# ============================================================

echo "[1/10] Calisma zamani ayarlari kontrol ediliyor..."
echo

PYTHONPATH=. "$PYTHON_BIN" - <<'PY' \
    2>&1 | tee "$REPORT_DIR/runtime_settings.txt"

from app.config.settings import get_settings

s = get_settings()

print("=== RUNTIME SETTINGS ===")
print(f"qwen_decision_model={s.qwen_decision_model}")
print(
    "qwen_compact_company_context="
    f"{s.qwen_compact_company_context}"
)
print(f"embedding_device={s.embedding_device}")

if not s.qwen_compact_company_context:
    print("COMPACT_CHECK=FAIL")
else:
    print("COMPACT_CHECK=PASS")

if str(s.embedding_device).lower() == "cpu":
    print("CPU_EMBEDDING_CHECK=PASS")
else:
    print("CPU_EMBEDDING_CHECK=FAIL")
PY

SETTINGS_EXIT=${PIPESTATUS[0]}

if [ "$SETTINGS_EXIT" -ne 0 ]; then
    echo "HATA: Settings okunamadi."
    return 0 2>/dev/null || true
fi

if ! grep -q '^COMPACT_CHECK=PASS$' "$REPORT_DIR/runtime_settings.txt"; then
    echo
    echo "HATA: QWEN_COMPACT_COMPANY_CONTEXT calisma zamaninda True degil."
    echo "Canli test baslatilmadi."
    echo
    echo ".env kontrolu:"
    grep -nE 'QWEN_COMPACT_COMPANY_CONTEXT' .env 2>/dev/null || true
    return 0 2>/dev/null || true
fi

echo


# ============================================================
# 2. DATABASE-SEQUENTIAL MODU VAR MI?
# ============================================================

echo "[2/10] database-sequential modu kontrol ediliyor..."
echo

PYTHONPATH=. "$PYTHON_BIN" scripts/run_tender_decision_chain.py --help \
    2>&1 | tee "$REPORT_DIR/decision_chain_help.txt"

HELP_EXIT=${PIPESTATUS[0]}

if [ "$HELP_EXIT" -ne 0 ]; then
    echo "HATA: Karar betigi --help calismadi."
    return 0 2>/dev/null || true
fi

if ! grep -q 'database-sequential' "$REPORT_DIR/decision_chain_help.txt"; then
    echo
    echo "HATA: database-sequential modu kodda bulunamadi."
    echo "Canli test baslatilmadi."
    return 0 2>/dev/null || true
fi

echo "database-sequential: OK"
echo


# ============================================================
# 3. HEDEFLI REGRESSION / UNIT TESTLERI
# ============================================================

echo "[3/10] Hedefli testler calistiriliyor..."
echo

TEST_FILES=(
    "tests/unit/test_ops02_regression.py"
    "tests/unit/test_activity_scope_positive.py"
    "tests/unit/test_compact_company_context.py"
    "tests/unit/test_score_aggregator.py"
    "tests/unit/test_validation_override_merge.py"
    "tests/integration/test_database_random_selection.py"
)

if [ -f "tests/integration/test_database_sequential_selection.py" ]; then
    TEST_FILES+=(
        "tests/integration/test_database_sequential_selection.py"
    )
fi

PYTHONPATH=. "$PYTHON_BIN" -m pytest \
    "${TEST_FILES[@]}" \
    -v \
    2>&1 | tee "$REPORT_DIR/targeted_tests.log"

TARGET_EXIT=${PIPESTATUS[0]}
echo "$TARGET_EXIT" > "$REPORT_DIR/targeted_tests_exit_code.txt"

if [ "$TARGET_EXIT" -ne 0 ]; then
    echo
    echo "HATA: Hedefli testlerde FAILED var."
    echo "Canli test baslatilmadi."
    return 0 2>/dev/null || true
fi

echo


# ============================================================
# 4. TUM TEST PAKETI
# ============================================================

echo "[4/10] Tum pytest paketi calistiriliyor..."
echo

PYTHONPATH=. "$PYTHON_BIN" -m pytest \
    2>&1 | tee "$REPORT_DIR/full_pytest.log"

FULL_EXIT=${PIPESTATUS[0]}
echo "$FULL_EXIT" > "$REPORT_DIR/full_pytest_exit_code.txt"

if [ "$FULL_EXIT" -ne 0 ]; then
    echo
    echo "HATA: Tum test paketinde FAILED var."
    echo "Canli test baslatilmadi."
    return 0 2>/dev/null || true
fi

echo


# ============================================================
# 5. COMPILE + GIT KONTROLU
# ============================================================

echo "[5/10] Python derleme ve git diff kontrolu..."
echo

PYTHONPATH=. "$PYTHON_BIN" -m compileall app scripts tests \
    2>&1 | tee "$REPORT_DIR/compileall.log"

COMPILE_EXIT=${PIPESTATUS[0]}

git diff --check \
    2>&1 | tee "$REPORT_DIR/git_diff_check.log"

DIFF_EXIT=${PIPESTATUS[0]}

git status --short > "$REPORT_DIR/git_status.txt"

if [ "$COMPILE_EXIT" -ne 0 ] || [ "$DIFF_EXIT" -ne 0 ]; then
    echo
    echo "HATA: compileall veya git diff --check basarisiz."
    echo "Canli test baslatilmadi."
    return 0 2>/dev/null || true
fi

echo


# ============================================================
# 6. FAISS KAPSAMA KONTROLU
# ============================================================

echo "[6/10] FAISS kapsam kontrolu..."
echo

PYTHONPATH=. "$PYTHON_BIN" scripts/build_active_tenders_faiss.py \
    --missing-from-faiss-only \
    --dry-run \
    2>&1 | tee "$REPORT_DIR/faiss_coverage.log"

FAISS_EXIT=${PIPESTATUS[0]}

if [ "$FAISS_EXIT" -ne 0 ]; then
    echo
    echo "HATA: FAISS kapsam kontrolu calismadi."
    echo "Canli test baslatilmadi."
    return 0 2>/dev/null || true
fi

echo


# ============================================================
# 7. INDEX STATE + SOURCE HASH
# ============================================================

echo "[7/10] tender_index_state ve source_hash kontrolu..."
echo

PYTHONPATH=. "$PYTHON_BIN" - <<'PY' \
    2>&1 | tee "$REPORT_DIR/hash_state_check.log"

from app.config.settings import get_settings
import psycopg

s = get_settings()

conn = psycopg.connect(
    host=s.database_host,
    port=s.database_port,
    dbname=s.database_name,
    user=s.database_user,
    password=s.database_password,
)

try:
    with conn.cursor() as cur:

        cur.execute("""
            SELECT
                index_status,
                COUNT(*) AS adet,
                COUNT(*) FILTER (
                    WHERE source_hash IS NOT NULL
                      AND BTRIM(source_hash) <> ''
                ) AS hash_dolu,
                COUNT(*) FILTER (
                    WHERE source_hash IS NULL
                       OR BTRIM(source_hash) = ''
                ) AS hash_bos
            FROM llm_rag.tender_index_state
            GROUP BY index_status
            ORDER BY index_status
        """)

        print("=== INDEX STATE ===")

        for row in cur.fetchall():
            print(row)

        print()

        cur.execute("""
            SELECT COUNT(*)
            FROM llm_rag.tender_index_state
            WHERE index_status = 'indexed'
              AND (
                  source_hash IS NULL
                  OR BTRIM(source_hash) = ''
              )
        """)

        missing = cur.fetchone()[0]

        print(f"indexed_hash_bos={missing}")

        if missing == 0:
            print("HASH_CHECK=PASS")
        else:
            print("HASH_CHECK=FAIL")

finally:
    conn.close()
PY

HASH_EXIT=${PIPESTATUS[0]}

if [ "$HASH_EXIT" -ne 0 ] || \
   ! grep -q '^HASH_CHECK=PASS$' "$REPORT_DIR/hash_state_check.log"; then

    echo
    echo "HATA: source_hash kontrolu basarisiz."
    echo "Canli test baslatilmadi."
    return 0 2>/dev/null || true
fi

echo


# ============================================================
# 8. SISTEM BILGISI
# ============================================================

echo "[8/10] Sistem bilgileri kaydediliyor..."
echo

{
    echo "=== DATE ==="
    date --iso-8601=seconds
    echo

    echo "=== KERNEL ==="
    uname -a
    echo

    echo "=== CPU ==="
    lscpu
    echo

    echo "=== MEMORY ==="
    free -h
    echo

    echo "=== DISK ==="
    df -h .
    echo

    echo "=== PYTHON ==="
    "$PYTHON_BIN" --version
    echo

    echo "=== OLLAMA VERSION ==="
    ollama --version 2>&1 || true
    echo

    echo "=== OLLAMA LIST ==="
    ollama list 2>&1 || true
    echo

    echo "=== OLLAMA PS BEFORE ==="
    ollama ps 2>&1 || true
    echo

    echo "=== ALL OLLAMA PROCESSES BEFORE ==="
    ps -eo pid,comm,%cpu,%mem,rss,args |
        grep -i '[o]llama' || true

} > "$REPORT_DIR/system_info.txt"


# ============================================================
# 9. 10 EN YENI AKTIF IHALE
# ============================================================

echo "[9/10] 10 en yeni aktif ihale canli testi basliyor..."
echo

RESOURCE_LOG="$REPORT_DIR/resource_monitor.csv"
DECISION_LOG="$REPORT_DIR/decision_run.log"

echo \
"timestamp,total_cpu_percent,load1,load5,load15,mem_total_mb,mem_used_mb,mem_available_mb,mem_percent,swap_total_mb,swap_used_mb,swap_percent,python_pid,python_cpu_percent,python_mem_percent,python_rss_mb,ollama_cpu_percent,ollama_mem_percent,ollama_rss_mb" \
> "$RESOURCE_LOG"

date --iso-8601=seconds > "$REPORT_DIR/start_time.txt"
START_EPOCH="$(date +%s)"

cat > "$REPORT_DIR/test_command.txt" <<EOF
PYTHONPATH=. $PYTHON_BIN scripts/run_tender_decision_chain.py \
  --selection-mode database-sequential \
  --source-mode database \
  --max-decisions 10 \
  --report-dir $REPORT_DIR \
  --log-level INFO
EOF

PYTHONPATH=. "$PYTHON_BIN" scripts/run_tender_decision_chain.py \
    --selection-mode database-sequential \
    --source-mode database \
    --max-decisions 10 \
    --report-dir "$REPORT_DIR" \
    --log-level INFO \
    > >(tee "$DECISION_LOG") 2>&1 &

TEST_PID=$!

echo "$TEST_PID" > "$REPORT_DIR/test_pid.txt"

echo "Karar sureci PID: $TEST_PID"
echo "Kaynaklar 2 saniyede bir olculuyor."
echo


while kill -0 "$TEST_PID" 2>/dev/null; do

    NOW="$(date --iso-8601=seconds)"

    # --------------------------------------------------------
    # TOPLAM CPU
    # --------------------------------------------------------

    CPU_PERCENT="$(
        top -bn1 |
        awk '/Cpu\(s\)/ {
            for (i=1; i<=NF; i++) {
                if ($i ~ /id,/) {
                    gsub(",", "", $(i-1));
                    printf "%.2f", 100-$(i-1);
                    exit
                }
            }
        }'
    )"

    [ -z "${CPU_PERCENT:-}" ] && CPU_PERCENT="0"

    read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg


    # --------------------------------------------------------
    # RAM
    # --------------------------------------------------------

    MEM_TOTAL_KB="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
    MEM_AVAILABLE_KB="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"

    MEM_USED_KB=$((MEM_TOTAL_KB - MEM_AVAILABLE_KB))

    MEM_TOTAL_MB=$((MEM_TOTAL_KB / 1024))
    MEM_USED_MB=$((MEM_USED_KB / 1024))
    MEM_AVAILABLE_MB=$((MEM_AVAILABLE_KB / 1024))

    MEM_PERCENT="$(
        awk -v u="$MEM_USED_KB" -v t="$MEM_TOTAL_KB" \
        'BEGIN {
            if (t > 0) printf "%.2f", (u/t)*100;
            else print "0"
        }'
    )"


    # --------------------------------------------------------
    # SWAP
    # --------------------------------------------------------

    SWAP_TOTAL_KB="$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)"
    SWAP_FREE_KB="$(awk '/SwapFree:/ {print $2}' /proc/meminfo)"

    SWAP_USED_KB=$((SWAP_TOTAL_KB - SWAP_FREE_KB))

    SWAP_TOTAL_MB=$((SWAP_TOTAL_KB / 1024))
    SWAP_USED_MB=$((SWAP_USED_KB / 1024))

    SWAP_PERCENT="$(
        awk -v u="$SWAP_USED_KB" -v t="$SWAP_TOTAL_KB" \
        'BEGIN {
            if (t > 0) printf "%.2f", (u/t)*100;
            else print "0"
        }'
    )"


    # --------------------------------------------------------
    # PYTHON KARAR SURECI
    # --------------------------------------------------------

    if ps -p "$TEST_PID" >/dev/null 2>&1; then

        read -r PY_CPU PY_MEM PY_RSS_KB <<< "$(
            ps -p "$TEST_PID" -o %cpu=,%mem=,rss= 2>/dev/null |
            awk '{print $1, $2, $3}'
        )"

    else
        PY_CPU="0"
        PY_MEM="0"
        PY_RSS_KB="0"
    fi

    PY_CPU="${PY_CPU:-0}"
    PY_MEM="${PY_MEM:-0}"
    PY_RSS_KB="${PY_RSS_KB:-0}"

    PY_RSS_MB=$((PY_RSS_KB / 1024))


    # --------------------------------------------------------
    # OLLAMA - TUM OLLAMA SURECLERINI TOPLA
    # Onceki monitor runner PID'sini kacirabiliyordu.
    # Burada comm=ollama olan tum surecler toplaniyor.
    # --------------------------------------------------------

    read -r OLLAMA_CPU OLLAMA_MEM OLLAMA_RSS_KB <<< "$(
        ps -eo comm=,%cpu=,%mem=,rss= |
        awk '
            tolower($1) == "ollama" {
                cpu += $2
                mem += $3
                rss += $4
            }
            END {
                printf "%.2f %.2f %.0f", cpu+0, mem+0, rss+0
            }
        '
    )"

    OLLAMA_CPU="${OLLAMA_CPU:-0}"
    OLLAMA_MEM="${OLLAMA_MEM:-0}"
    OLLAMA_RSS_KB="${OLLAMA_RSS_KB:-0}"

    OLLAMA_RSS_MB=$((OLLAMA_RSS_KB / 1024))


    # --------------------------------------------------------
    # CSV
    # --------------------------------------------------------

    echo \
"$NOW,$CPU_PERCENT,$LOAD1,$LOAD5,$LOAD15,$MEM_TOTAL_MB,$MEM_USED_MB,$MEM_AVAILABLE_MB,$MEM_PERCENT,$SWAP_TOTAL_MB,$SWAP_USED_MB,$SWAP_PERCENT,$TEST_PID,$PY_CPU,$PY_MEM,$PY_RSS_MB,$OLLAMA_CPU,$OLLAMA_MEM,$OLLAMA_RSS_MB" \
    >> "$RESOURCE_LOG"

    sleep 2

done


wait "$TEST_PID"
TEST_EXIT=$?

END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))

date --iso-8601=seconds > "$REPORT_DIR/end_time.txt"
echo "$TEST_EXIT" > "$REPORT_DIR/exit_code.txt"

ollama ps > "$REPORT_DIR/ollama_ps_after_test.txt" 2>&1 || true

ps -eo pid,comm,%cpu,%mem,rss,args |
    grep -i '[o]llama' \
    > "$REPORT_DIR/ollama_processes_after.txt" || true


# ============================================================
# 10. KAYNAK + QWEN TANILAMA OZETI
# ============================================================

echo
echo "[10/10] Kaynak ve Qwen tanilama ozeti olusturuluyor..."
echo

"$PYTHON_BIN" - \
    "$RESOURCE_LOG" \
    "$DECISION_LOG" \
    "$REPORT_DIR/system_resources_summary.txt" \
    "$ELAPSED" <<'PY'

import csv
import re
import sys
from pathlib import Path

resource_path = Path(sys.argv[1])
decision_log = Path(sys.argv[2])
summary_path = Path(sys.argv[3])
elapsed = int(sys.argv[4])


# ------------------------------------------------------------
# RESOURCE CSV
# ------------------------------------------------------------

with resource_path.open(encoding="utf-8") as f:
    rows = list(csv.DictReader(f))


def values(field):
    out = []

    for row in rows:
        try:
            out.append(float(row[field]))
        except Exception:
            pass

    return out


def avg(field):
    vals = values(field)
    return sum(vals) / len(vals) if vals else 0.0


def maxv(field):
    vals = values(field)
    return max(vals) if vals else 0.0


# ------------------------------------------------------------
# QWEN DIAGNOSTICS
# ------------------------------------------------------------

text = decision_log.read_text(
    encoding="utf-8",
    errors="replace",
)

diag_lines = [
    line
    for line in text.splitlines()
    if "[DIAGNOSTICS]" in line
]

prompt_counts = []
eval_counts = []
total_durations = []
prompt_speeds = []
generation_speeds = []
final_prompt_chars = []

for line in diag_lines:

    def grab(name):
        m = re.search(
            rf"\b{name}=([0-9.]+)",
            line,
        )
        return float(m.group(1)) if m else None

    pc = grab("prompt_eval_count")
    ec = grab("eval_count")
    td = grab("total_duration")
    ps = grab("prompt_tokens_per_second")
    gs = grab("generation_tokens_per_second")

    if pc is not None:
        prompt_counts.append(pc)

    if ec is not None:
        eval_counts.append(ec)

    if td is not None:
        # Ollama ns
        total_durations.append(td / 1_000_000_000)

    if ps is not None:
        prompt_speeds.append(ps)

    if gs is not None:
        generation_speeds.append(gs)


for line in text.splitlines():
    if "[CONTEXT_DIAGNOSTICS]" in line:

        m = re.search(
            r"\bfinal_prompt_chars=([0-9]+)",
            line,
        )

        if m:
            final_prompt_chars.append(
                float(m.group(1))
            )


def simple_avg(values):
    return (
        sum(values) / len(values)
        if values
        else 0.0
    )


h = elapsed // 3600
m = (elapsed % 3600) // 60
s = elapsed % 60


summary = f"""
============================================================
10 EN YENI IHALE
KAYNAK + MODEL PERFORMANS OZETI
============================================================

Toplam duvar saati:
  {elapsed} saniye
  {h:02d}:{m:02d}:{s:02d}

Karar sayisi hedefi:
  10

Ornekleme sayisi:
  {len(rows)}

------------------------------------------------------------
CPU
------------------------------------------------------------

Ortalama toplam CPU:
  %{avg('total_cpu_percent'):.2f}

Tepe toplam CPU:
  %{maxv('total_cpu_percent'):.2f}

------------------------------------------------------------
RAM
------------------------------------------------------------

Ortalama RAM:
  {avg('mem_used_mb'):.2f} MB

Tepe RAM:
  {maxv('mem_used_mb'):.2f} MB

Ortalama RAM yuzdesi:
  %{avg('mem_percent'):.2f}

Tepe RAM yuzdesi:
  %{maxv('mem_percent'):.2f}

------------------------------------------------------------
SWAP
------------------------------------------------------------

Ortalama swap:
  {avg('swap_used_mb'):.2f} MB

Tepe swap:
  {maxv('swap_used_mb'):.2f} MB

Tepe swap yuzdesi:
  %{maxv('swap_percent'):.2f}

------------------------------------------------------------
PYTHON
------------------------------------------------------------

Ortalama Python CPU:
  %{avg('python_cpu_percent'):.2f}

Tepe Python CPU:
  %{maxv('python_cpu_percent'):.2f}

Ortalama Python RSS:
  {avg('python_rss_mb'):.2f} MB

Tepe Python RSS:
  {maxv('python_rss_mb'):.2f} MB

------------------------------------------------------------
OLLAMA
------------------------------------------------------------

Ortalama Ollama CPU:
  %{avg('ollama_cpu_percent'):.2f}

Tepe Ollama CPU:
  %{maxv('ollama_cpu_percent'):.2f}

Ortalama Ollama RSS:
  {avg('ollama_rss_mb'):.2f} MB

Tepe Ollama RSS:
  {maxv('ollama_rss_mb'):.2f} MB

------------------------------------------------------------
LOAD
------------------------------------------------------------

Ortalama 1 dk load:
  {avg('load1'):.2f}

Tepe 1 dk load:
  {maxv('load1'):.2f}

------------------------------------------------------------
QWEN
------------------------------------------------------------

DIAGNOSTICS kaydi:
  {len(diag_lines)}

Ortalama prompt token:
  {simple_avg(prompt_counts):.2f}

Ortalama output token:
  {simple_avg(eval_counts):.2f}

Ortalama model total_duration:
  {simple_avg(total_durations):.2f} saniye

Ortalama prompt hizi:
  {simple_avg(prompt_speeds):.2f} token/s

Ortalama generation hizi:
  {simple_avg(generation_speeds):.2f} token/s

------------------------------------------------------------
CONTEXT
------------------------------------------------------------

Ortalama final prompt karakteri:
  {simple_avg(final_prompt_chars):.2f}

En buyuk final prompt:
  {max(final_prompt_chars) if final_prompt_chars else 0:.0f}

============================================================
"""

summary_path.write_text(
    summary.strip() + "\n",
    encoding="utf-8",
)

print(summary)
PY


# ============================================================
# SON KONTROLLER
# ============================================================

echo
echo "============================================================"
echo "CANLI TEST SONRASI HIZLI KONTROLLER"
echo "============================================================"

echo
echo "--- COMPACT MODE ---"

grep '\[COMPANY_CONTEXT_MODE\]' "$DECISION_LOG" |
    head -20 || true

echo
echo "--- SELECTION MODE ---"

grep '\[SELECTION_MODE\]' "$DECISION_LOG" |
    head -5 || true

echo
echo "--- SEQUENTIAL SELECTION ---"

grep '\[DATABASE_SEQUENTIAL_SELECTION\]' "$DECISION_LOG" |
    head -30 || true

echo
echo "--- KARARLAR ---"

grep -E 'karar=(uygun|uygun_degil|inceleme_gerekli)' \
    "$DECISION_LOG" || true

echo
echo "--- KARAR ZINCIRI OZETI ---"

grep -A15 '\[KARAR ZİNCİRİ ÖZETİ\]' \
    "$DECISION_LOG" || true


echo
echo "============================================================"
echo "ISLEM TAMAMLANDI"
echo "============================================================"
echo "Exit code     : $TEST_EXIT"
echo "Toplam sure   : $ELAPSED saniye"
echo "Rapor klasoru : $REPORT_DIR"
echo
echo "Dosyalar:"
find "$REPORT_DIR" \
    -maxdepth 1 \
    -type f \
    -printf '  %f\n' |
    sort
echo
echo "Terminal kapatilmadi."
echo "============================================================"

