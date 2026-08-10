#!/usr/bin/env bash

set -o pipefail

PYTHON_BIN=".venv/bin/python3"
SEED=20260810
REPORT_DIR="reports/live_db_random_10_semantic_$(date +%Y%m%d_%H%M%S)"

mkdir -p "$REPORT_DIR"

echo "$SEED" > "$REPORT_DIR/random_seed.txt"

echo "============================================================"
echo "CANLI DB - 10 SEMANTIK KANITLI RANDOM IHALE TESTI"
echo "Seed  : $SEED"
echo "Rapor : $REPORT_DIR"
echo "============================================================"

# ------------------------------------------------------------
# 1) SISTEM BILGILERI
# ------------------------------------------------------------

{
    echo "Tarih:"
    date

    echo
    echo "Kernel:"
    uname -a

    echo
    echo "CPU:"
    lscpu

    echo
    echo "Bellek:"
    free -h

    echo
    echo "Python:"
    "$PYTHON_BIN" --version

    echo
    echo "Ollama:"
    ollama --version 2>&1 || true

    echo
    echo "Ollama surecleri:"
    ollama ps 2>&1 || true

    echo
    echo "Git commit:"
    git rev-parse HEAD 2>/dev/null || true

    echo
    echo "Git durum:"
    git status --short 2>/dev/null || true
} > "$REPORT_DIR/system_info.txt" 2>&1

# ------------------------------------------------------------
# 2) POSTGRESQL KONTROLU
# ------------------------------------------------------------

PYTHONPATH=. "$PYTHON_BIN" scripts/check_database.py \
    2>&1 | tee "$REPORT_DIR/database_check.log"

DB_EXIT=${PIPESTATUS[0]}

if [ "$DB_EXIT" -ne 0 ]; then
    echo "$DB_EXIT" > "$REPORT_DIR/exit_code.txt"

    echo
    echo "============================================================"
    echo "POSTGRESQL KONTROLU BASARISIZ"
    echo "Test baslatilmadi."
    echo "Terminal acik birakildi."
    echo "Rapor: $REPORT_DIR"
    echo "============================================================"

    return 0 2>/dev/null || true
fi

# ------------------------------------------------------------
# 3) KAYNAK MONITORU
# ------------------------------------------------------------

(
    echo "timestamp,system_cpu_percent,load_1m,load_5m,load_15m,system_used_mb,system_available_mb,swap_used_mb,ollama_rss_mb,python_rss_mb" \
        > "$REPORT_DIR/resource_monitor.csv"

    PREV_TOTAL=0
    PREV_IDLE=0

    while true; do
        TS=$(date '+%Y-%m-%d %H:%M:%S')

        MEM_TOTAL=$(awk '/MemTotal:/ {print $2}' /proc/meminfo)
        MEM_AVAIL=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
        SWAP_TOTAL=$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)
        SWAP_FREE=$(awk '/SwapFree:/ {print $2}' /proc/meminfo)

        MEM_USED=$((MEM_TOTAL-MEM_AVAIL))
        SWAP_USED=$((SWAP_TOTAL-SWAP_FREE))

        read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat

        IDLE_NOW=$((idle+iowait))
        TOTAL_NOW=$((user+nice+system+idle+iowait+irq+softirq+steal))

        if [ "$PREV_TOTAL" -eq 0 ]; then
            CPU_PERCENT="0.00"
        else
            TOTAL_DIFF=$((TOTAL_NOW-PREV_TOTAL))
            IDLE_DIFF=$((IDLE_NOW-PREV_IDLE))

            CPU_PERCENT=$(awk \
                -v total="$TOTAL_DIFF" \
                -v idle="$IDLE_DIFF" \
                'BEGIN {
                    if (total > 0)
                        printf "%.2f",100*(total-idle)/total
                    else
                        printf "0.00"
                }')
        fi

        PREV_TOTAL=$TOTAL_NOW
        PREV_IDLE=$IDLE_NOW

        read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg

        OLLAMA_RSS=$(
            ps -eo rss=,args= |
            awk 'tolower($0) ~ /ollama/ && $0 !~ /awk/ {s+=$1} END {print s+0}'
        )

        PYTHON_RSS=$(
            ps -eo rss=,args= |
            awk '/run_tender_decision_chain.py/ && $0 !~ /awk/ {s+=$1} END {print s+0}'
        )

        awk \
          -v ts="$TS" \
          -v cpu="$CPU_PERCENT" \
          -v l1="$LOAD1" \
          -v l5="$LOAD5" \
          -v l15="$LOAD15" \
          -v mem="$MEM_USED" \
          -v avail="$MEM_AVAIL" \
          -v swap="$SWAP_USED" \
          -v ollama="$OLLAMA_RSS" \
          -v py="$PYTHON_RSS" \
          'BEGIN {
              printf "%s,%s,%s,%s,%s,%.2f,%.2f,%.2f,%.2f,%.2f\n",
              ts,cpu,l1,l5,l15,
              mem/1024,
              avail/1024,
              swap/1024,
              ollama/1024,
              py/1024
          }' >> "$REPORT_DIR/resource_monitor.csv"

        sleep 1
    done
) &

MONITOR_PID=$!

cleanup() {
    kill "$MONITOR_PID" 2>/dev/null || true
    wait "$MONITOR_PID" 2>/dev/null || true
}

trap cleanup INT TERM

# ------------------------------------------------------------
# 4) TEST BASLANGICI
# ------------------------------------------------------------

date '+%Y-%m-%d %H:%M:%S' > "$REPORT_DIR/start_time.txt"

env \
    PYTHONPATH=. \
    QWEN_COMPACT_COMPANY_CONTEXT=true \
    QWEN_DECISION_NUM_CTX=8192 \
    QWEN_DECISION_NUM_PREDICT=1024 \
    OLLAMA_DECISION_TIMEOUT_SECONDS=7200 \
    /usr/bin/time -v \
        -o "$REPORT_DIR/time_metrics.txt" \
    "$PYTHON_BIN" scripts/run_tender_decision_chain.py \
        --source-mode database \
        --selection-mode database-random \
        --max-decisions 10 \
        --random-seed "$SEED" \
        --report-dir "$REPORT_DIR" \
        --log-level INFO \
    2>&1 | tee "$REPORT_DIR/run.log"

EXIT_CODE=${PIPESTATUS[0]}

date '+%Y-%m-%d %H:%M:%S' > "$REPORT_DIR/end_time.txt"
echo "$EXIT_CODE" > "$REPORT_DIR/exit_code.txt"

cleanup
trap - INT TERM

# ------------------------------------------------------------
# 5) LOG AYRISTIRMA
# ------------------------------------------------------------

grep '\[DIAGNOSTICS\]' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/qwen_diagnostics.log" || true

grep '\[SELECTION_MODE\]' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/selection_mode.log" || true

grep '\[DATABASE_RANDOM_SELECTION\]' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/database_random_selection.log" || true

grep '\[COMPANY_CONTEXT_MODE\]' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/company_context_mode.log" || true

grep '\[CONTEXT_DIAGNOSTICS\]' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/context_diagnostics.log" || true

grep -Ei 'ERROR|HATA|FAILED|EXCEPTION|TRACEBACK' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/errors.log" || true

# ------------------------------------------------------------
# 6) SISTEM KAYNAK OZETI
# ------------------------------------------------------------

awk -F',' '
NR>1 {
    n++
    cpu_sum += $2
    mem_sum += $6
    ollama_sum += $9
    python_sum += $10

    if ($2 > max_cpu) max_cpu=$2
    if ($3 > max_load1) max_load1=$3
    if ($6 > max_mem) max_mem=$6
    if ($8 > max_swap) max_swap=$8
    if ($9 > max_ollama) max_ollama=$9
    if ($10 > max_python) max_python=$10
}
END {
    printf "Ornek sayisi:              %d\n",n
    printf "Tepe CPU:                  %.2f %%\n",max_cpu
    printf "Ortalama CPU:              %.2f %%\n",(n ? cpu_sum/n : 0)
    printf "Tepe load 1m:              %.2f\n",max_load1

    printf "Tepe sistem RAM:           %.2f MB (%.2f GB)\n",
        max_mem,max_mem/1024

    printf "Ortalama sistem RAM:       %.2f MB (%.2f GB)\n",
        (n ? mem_sum/n : 0),
        (n ? mem_sum/n/1024 : 0)

    printf "Tepe Ollama/Qwen RSS:      %.2f MB (%.2f GB)\n",
        max_ollama,max_ollama/1024

    printf "Ortalama Ollama/Qwen RSS:  %.2f MB (%.2f GB)\n",
        (n ? ollama_sum/n : 0),
        (n ? ollama_sum/n/1024 : 0)

    printf "Tepe Python RSS:           %.2f MB (%.2f GB)\n",
        max_python,max_python/1024

    printf "Ortalama Python RSS:       %.2f MB (%.2f GB)\n",
        (n ? python_sum/n : 0),
        (n ? python_sum/n/1024 : 0)

    printf "Tepe swap:                 %.2f MB (%.2f GB)\n",
        max_swap,max_swap/1024
}' "$REPORT_DIR/resource_monitor.csv" \
| tee "$REPORT_DIR/resource_summary.txt"

# ------------------------------------------------------------
# 7) QWEN OZETI
# ------------------------------------------------------------

awk '
/\[DIAGNOSTICS\]/ {
    calls++

    for(i=1;i<=NF;i++) {
        split($i,a,"=")

        if(a[1]=="prompt_eval_count") {
            ptoken+=a[2]
            if(a[2]>maxpt) maxpt=a[2]
            if(minpt==0 || a[2]<minpt) minpt=a[2]
        }

        if(a[1]=="eval_count") {
            otoken+=a[2]
            if(a[2]>maxot) maxot=a[2]
            if(minot==0 || a[2]<minot) minot=a[2]
        }

        if(a[1]=="load_duration") load+=a[2]
        if(a[1]=="prompt_eval_duration") promptdur+=a[2]
        if(a[1]=="eval_duration") evaldur+=a[2]
        if(a[1]=="total_duration") totaldur+=a[2]
        if(a[1]=="prompt_tokens_per_second") pspeed+=a[2]
        if(a[1]=="generation_tokens_per_second") gspeed+=a[2]
    }
}

END {
    if(calls==0) {
        print "Qwen diagnostics bulunamadi."
        exit
    }

    printf "Qwen cagri sayisi:                    %d\n",calls
    printf "Toplam prompt token:                  %d\n",ptoken
    printf "Ortalama prompt token:                %.2f\n",ptoken/calls
    printf "Minimum prompt token:                 %d\n",minpt
    printf "Maksimum prompt token:                %d\n",maxpt

    printf "Toplam output token:                  %d\n",otoken
    printf "Ortalama output token:                %.2f\n",otoken/calls
    printf "Minimum output token:                 %d\n",minot
    printf "Maksimum output token:                %d\n",maxot

    printf "Ortalama model yukleme suresi:        %.2f sn\n",
        load/calls/1000000000

    printf "Ortalama prompt degerlendirme suresi: %.2f sn\n",
        promptdur/calls/1000000000

    printf "Ortalama uretim suresi:               %.2f sn\n",
        evaldur/calls/1000000000

    printf "Ortalama model toplam suresi:         %.2f sn\n",
        totaldur/calls/1000000000

    printf "Ortalama prompt hizi:                 %.2f token/sn\n",
        pspeed/calls

    printf "Ortalama uretim hizi:                 %.2f token/sn\n",
        gspeed/calls
}' "$REPORT_DIR/qwen_diagnostics.log" \
| tee "$REPORT_DIR/qwen_summary.txt"

# ------------------------------------------------------------
# 8) SURE OZETI
# ------------------------------------------------------------

grep -E \
'User time|System time|Percent of CPU|Elapsed|Maximum resident set size|Major|Minor|Voluntary context|Involuntary context|Swaps|Exit status' \
"$REPORT_DIR/time_metrics.txt" \
> "$REPORT_DIR/time_summary.txt" || true

# ------------------------------------------------------------
# 9) SEMANTIK SECIM OZETI
# ------------------------------------------------------------

SELECTION_CSV="$REPORT_DIR/database_random_selection.csv"

if [ -f "$SELECTION_CSV" ]; then
    "$PYTHON_BIN" - "$SELECTION_CSV" > "$REPORT_DIR/semantic_selection_summary.txt" <<'PY'
import csv
import sys
from collections import Counter

path = sys.argv[1]

with open(path, "r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

status_counts = Counter(
    (r.get("selection_status") or "").strip()
    for r in rows
)

semantic_true = [
    r for r in rows
    if str(r.get("semantic_evidence_available", "")).strip().lower()
    in {"true", "1", "yes", "evet"}
]

semantic_false = [
    r for r in rows
    if str(r.get("semantic_evidence_available", "")).strip().lower()
    in {"false", "0", "no", "hayir", "hayır"}
]

selected = [
    r for r in rows
    if (r.get("selection_status") or "").strip() == "selected"
]

print(f"Incelenen ihale:                  {len(rows)}")
print(f"Semantik kanit var:               {len(semantic_true)}")
print(f"Semantik kanit yok:               {len(semantic_false)}")
print(f"Modele secilen:                   {len(selected)}")
print(f"missing_faiss_evidence:           {status_counts.get('missing_faiss_evidence', 0)}")

print()
print("Modele secilen ihaleler:")
for r in selected:
    print(
        f"{r.get('selection_rank','')} | "
        f"{r.get('ikn','')} | "
        f"profile={r.get('selected_profile_code','')} | "
        f"score={r.get('selected_profile_score','')} | "
        f"semantic={r.get('semantic_evidence_available','')}"
    )

bad = [
    r for r in selected
    if str(r.get("semantic_evidence_available", "")).strip().lower()
    not in {"true", "1", "yes", "evet"}
]

print()
if bad:
    print(f"HATA: {len(bad)} secili ihalede semantik kanit yok!")
else:
    print("OK: Modele secilen tum ihalelerde semantik kanit mevcut.")
PY
else
    echo "database_random_selection.csv bulunamadi." \
        > "$REPORT_DIR/semantic_selection_summary.txt"
fi

# ------------------------------------------------------------
# 10) KARAR OZETI
# ------------------------------------------------------------

PUBLIC_CSV="$REPORT_DIR/tender_public_decisions.csv"

if [ -f "$PUBLIC_CSV" ]; then
    "$PYTHON_BIN" - "$PUBLIC_CSV" > "$REPORT_DIR/decision_summary.txt" <<'PY'
import csv
import sys
from collections import Counter

path = sys.argv[1]

with open(path, "r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

counts = Counter(
    (r.get("decision") or "").strip()
    for r in rows
)

review_count = sum(
    str(r.get("human_review_required", "")).strip().lower()
    in {"true", "1", "yes", "evet"}
    for r in rows
)

print(f"Toplam karar:               {len(rows)}")
print(f"uygun:                      {counts.get('uygun', 0)}")
print(f"uygun_degil:                {counts.get('uygun_degil', 0)}")
print(f"inceleme_gerekli:           {counts.get('inceleme_gerekli', 0)}")
print(f"Insan incelemesi gereken:   {review_count}")

print()
print("IKN bazinda kararlar:")

for r in rows:
    print(
        f"{r.get('ikn','')} | "
        f"{r.get('decision','')} | "
        f"confidence={r.get('confidence','')} | "
        f"review={r.get('human_review_required','')}"
    )
PY
else
    echo "tender_public_decisions.csv bulunamadi." \
        > "$REPORT_DIR/decision_summary.txt"
fi

# ------------------------------------------------------------
# 11) RUN SUMMARY
# ------------------------------------------------------------

SUMMARY_JSON="$REPORT_DIR/tender_decision_run_summary.json"

if [ -f "$SUMMARY_JSON" ]; then
    "$PYTHON_BIN" - "$SUMMARY_JSON" > "$REPORT_DIR/random_run_summary.txt" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    d = json.load(f)

keys = [
    "selection_mode",
    "active_tender_pool_size",
    "random_seed",
    "random_tenders_requested",
    "random_tenders_examined",
    "random_tenders_missing_faiss",
    "random_tenders_with_semantic_evidence",
    "random_tenders_submitted_to_model",
    "unique_tenders_submitted_to_model",
    "decisions",
    "elapsed_seconds",
]

for key in keys:
    print(f"{key}: {d.get(key)}")

print(f"decision_distribution: {d.get('decision_distribution')}")
print(f"failures: {d.get('failures')}")
PY
else
    echo "tender_decision_run_summary.json bulunamadi." \
        > "$REPORT_DIR/random_run_summary.txt"
fi

# ------------------------------------------------------------
# 12) FINAL RAPOR
# ------------------------------------------------------------

echo
echo "============================================================"
echo "10 SEMANTIK KANITLI RANDOM IHALE TESTI TAMAMLANDI"
echo "============================================================"
echo "Test cikis kodu : $EXIT_CODE"
echo "Seed            : $SEED"
echo "Rapor           : $REPORT_DIR"

echo
echo "---------- DATABASE-RANDOM OZETI ----------"
cat "$REPORT_DIR/random_run_summary.txt"

echo
echo "---------- SEMANTIK KANIT KONTROLU --------"
cat "$REPORT_DIR/semantic_selection_summary.txt"

echo
echo "---------- KARARLAR -----------------------"
cat "$REPORT_DIR/decision_summary.txt"

echo
echo "---------- SISTEM -------------------------"
cat "$REPORT_DIR/resource_summary.txt"

echo
echo "---------- QWEN ---------------------------"
cat "$REPORT_DIR/qwen_summary.txt"

echo
echo "---------- SURE ---------------------------"
cat "$REPORT_DIR/time_summary.txt"

echo
echo "---------- HATALAR ------------------------"
if [ -s "$REPORT_DIR/errors.log" ]; then
    cat "$REPORT_DIR/errors.log"
else
    echo "Hata bulunamadi."
fi

echo
echo "---------- RAPOR DOSYALARI ----------------"
find "$REPORT_DIR" -maxdepth 1 -type f -printf '%f\n' | sort

echo
echo "============================================================"
echo "SCRIPT TAMAMLANDI."
echo "WSL terminali ACIK birakildi."
echo "============================================================"
