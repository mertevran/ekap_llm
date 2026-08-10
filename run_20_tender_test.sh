set -o pipefail

SEED=20260810
REPORT_DIR="reports/random_tender_analysis_20_$(date +%Y%m%d_%H%M%S)"

mkdir -p "$REPORT_DIR"
echo "$SEED" > "$REPORT_DIR/random_seed.txt"

echo "============================================================"
echo "CANLI DB - PROFİLSİZ RASTGELE 20 İHALE ANALİZİ"
echo "Seed:  $SEED"
echo "Rapor: $REPORT_DIR"
echo "============================================================"

# ============================================================
# 1. CANLI POSTGRESQL BAĞLANTISINI KONTROL ET
# ============================================================

PYTHONPATH=. python scripts/check_database.py \
    2>&1 | tee "$REPORT_DIR/database_check.log"

DB_EXIT=${PIPESTATUS[0]}

if [ "$DB_EXIT" -ne 0 ]; then
    echo "PostgreSQL bağlantısı başarısız. Test başlatılmadı."
    echo "$DB_EXIT" > "$REPORT_DIR/exit_code.txt"
    exit "$DB_EXIT"
fi


# ============================================================
# 2. SİSTEM KAYNAK İZLEME
# CPU + RAM + SWAP + OLLAMA/QWEN + PYTHON
# ============================================================

(
    echo "timestamp,system_cpu_percent,load_1m,load_5m,load_15m,system_used_mb,system_available_mb,swap_used_mb,ollama_family_rss_mb,python_rss_mb" \
        > "$REPORT_DIR/resource_monitor.csv"

    PREV_TOTAL=0
    PREV_IDLE=0

    while true; do

        TS=$(date '+%Y-%m-%d %H:%M:%S')

        # -------------------------
        # RAM / SWAP
        # -------------------------
        MEM_TOTAL=$(awk '/MemTotal:/ {print $2}' /proc/meminfo)
        MEM_AVAIL=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)

        SWAP_TOTAL=$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)
        SWAP_FREE=$(awk '/SwapFree:/ {print $2}' /proc/meminfo)

        MEM_USED=$((MEM_TOTAL - MEM_AVAIL))
        SWAP_USED=$((SWAP_TOTAL - SWAP_FREE))

        # -------------------------
        # CPU
        # -------------------------
        read -r cpu user nice system idle iowait irq softirq steal guest guest_nice < /proc/stat

        IDLE_NOW=$((idle + iowait))
        TOTAL_NOW=$((user + nice + system + idle + iowait + irq + softirq + steal))

        if [ "$PREV_TOTAL" -eq 0 ]; then
            CPU_PERCENT="0.00"
        else
            TOTAL_DIFF=$((TOTAL_NOW - PREV_TOTAL))
            IDLE_DIFF=$((IDLE_NOW - PREV_IDLE))

            if [ "$TOTAL_DIFF" -gt 0 ]; then
                CPU_PERCENT=$(awk \
                    -v total="$TOTAL_DIFF" \
                    -v idle="$IDLE_DIFF" \
                    'BEGIN {
                        printf "%.2f", 100 * (total-idle) / total
                    }')
            else
                CPU_PERCENT="0.00"
            fi
        fi

        PREV_TOTAL=$TOTAL_NOW
        PREV_IDLE=$IDLE_NOW

        # -------------------------
        # SYSTEM LOAD
        # -------------------------
        read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg

        # -------------------------
        # OLLAMA / QWEN RSS
        # -------------------------
        OLLAMA_RSS=$(
            ps -eo rss=,args= 2>/dev/null |
            awk '
                tolower($0) ~ /ollama/ &&
                $0 !~ /awk/ {
                    sum += $1
                }
                END {
                    print sum+0
                }
            '
        )

        # -------------------------
        # PYTHON TEST RSS
        # -------------------------
        PYTHON_RSS=$(
            ps -eo rss=,args= 2>/dev/null |
            awk '
                /python .*scripts\/run_random_tender_analysis.py/ &&
                $0 !~ /awk/ {
                    sum += $1
                }
                END {
                    print sum+0
                }
            '
        )

        awk \
            -v ts="$TS" \
            -v cpu="$CPU_PERCENT" \
            -v l1="$LOAD1" \
            -v l5="$LOAD5" \
            -v l15="$LOAD15" \
            -v used="$MEM_USED" \
            -v avail="$MEM_AVAIL" \
            -v swap="$SWAP_USED" \
            -v ollama="$OLLAMA_RSS" \
            -v python="$PYTHON_RSS" \
            'BEGIN {
                printf "%s,%s,%s,%s,%s,%.2f,%.2f,%.2f,%.2f,%.2f\n",
                    ts,
                    cpu,
                    l1,
                    l5,
                    l15,
                    used/1024,
                    avail/1024,
                    swap/1024,
                    ollama/1024,
                    python/1024
            }' >> "$REPORT_DIR/resource_monitor.csv"

        sleep 2

    done
) &

MONITOR_PID=$!

cleanup() {
    kill "$MONITOR_PID" 2>/dev/null || true
    wait "$MONITOR_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM


# ============================================================
# 3. TEST BAŞLANGIÇ BİLGİLERİ
# ============================================================

date '+%Y-%m-%d %H:%M:%S' > "$REPORT_DIR/start_time.txt"

{
    echo "SEED=$SEED"
    echo "COUNT=20"
    echo "MODEL=qwen3.5:4b-q4_K_M"
    echo "NUM_CTX=8192"
    echo "NUM_PREDICT=1024"
    echo "THINK=true (kullanıcı tarafından aktif edilecek)"
    echo "PROFILE_MATCHING=disabled"
    echo "GPU=disabled"
} > "$REPORT_DIR/effective_settings.txt"


# ============================================================
# 4. PROFİLSİZ 20 CANLI İHALE ANALİZİ
# ============================================================

env \
    PYTHONPATH=. \
    QWEN_DECISION_NUM_CTX=8192 \
    QWEN_DECISION_NUM_PREDICT=1024 \
    OLLAMA_DECISION_TIMEOUT_SECONDS=7200 \
    /usr/bin/time -v \
        -o "$REPORT_DIR/time_metrics.txt" \
    python scripts/run_random_tender_analysis.py \
        --count 20 \
        --random-seed "$SEED" \
        --report-dir "$REPORT_DIR" \
        --log-level INFO \
        2>&1 | tee "$REPORT_DIR/run.log"

EXIT_CODE=${PIPESTATUS[0]}

date '+%Y-%m-%d %H:%M:%S' > "$REPORT_DIR/end_time.txt"
echo "$EXIT_CODE" > "$REPORT_DIR/exit_code.txt"

cleanup
trap - EXIT INT TERM


# ============================================================
# 5. QWEN TANILAMALARINI AYIR
# ============================================================

grep '\[DIAGNOSTICS\]' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/qwen_diagnostics.log" || true

grep -Ei 'thinking|think=' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/thinking_diagnostics.log" || true

grep -Ei 'ERROR|HATA|FAILED|EXCEPTION|TRACEBACK' "$REPORT_DIR/run.log" \
    > "$REPORT_DIR/errors.log" || true


# ============================================================
# 6. SİSTEM KAYNAK ÖZETİ
# ============================================================

awk -F',' '
NR > 1 {

    if ($2 > max_cpu)
        max_cpu=$2

    if ($3 > max_load1)
        max_load1=$3

    if ($6 > max_system)
        max_system=$6

    if ($8 > max_swap)
        max_swap=$8

    if ($9 > max_ollama)
        max_ollama=$9

    if ($10 > max_python)
        max_python=$10

    cpu_sum += $2
    load_sum += $3
    system_sum += $6
    ollama_sum += $9
    python_sum += $10

    count++
}

END {

    printf "Samples:                    %d\n", count

    printf "Peak system CPU:            %.2f %%\n", max_cpu

    if (count > 0)
        printf "Average system CPU:         %.2f %%\n", cpu_sum/count

    printf "Peak system load (1m):      %.2f\n", max_load1

    if (count > 0)
        printf "Average system load (1m):   %.2f\n", load_sum/count

    printf "Peak total system RAM:      %.2f MB (%.2f GB)\n",
        max_system, max_system/1024

    if (count > 0)
        printf "Average system RAM:         %.2f MB (%.2f GB)\n",
            system_sum/count, (system_sum/count)/1024

    printf "Peak Ollama/Qwen RSS:       %.2f MB (%.2f GB)\n",
        max_ollama, max_ollama/1024

    if (count > 0)
        printf "Average Ollama/Qwen RSS:    %.2f MB (%.2f GB)\n",
            ollama_sum/count, (ollama_sum/count)/1024

    printf "Peak Python RSS:            %.2f MB (%.2f GB)\n",
        max_python, max_python/1024

    if (count > 0)
        printf "Average Python RSS:         %.2f MB (%.2f GB)\n",
            python_sum/count, (python_sum/count)/1024

    printf "Peak swap used:             %.2f MB (%.2f GB)\n",
        max_swap, max_swap/1024

}' "$REPORT_DIR/resource_monitor.csv" \
    | tee "$REPORT_DIR/resource_summary.txt"


# ============================================================
# 7. QWEN PERFORMANS ÖZETİ
# ============================================================

awk '
/\[DIAGNOSTICS\]/ {

    calls++

    for (i=1; i<=NF; i++) {

        split($i,a,"=")

        if (a[1]=="prompt_eval_count") {
            prompt_tokens += a[2]

            if (a[2] > max_prompt_tokens)
                max_prompt_tokens=a[2]

            if (min_prompt_tokens==0 || a[2] < min_prompt_tokens)
                min_prompt_tokens=a[2]
        }

        if (a[1]=="eval_count") {
            output_tokens += a[2]

            if (a[2] > max_output_tokens)
                max_output_tokens=a[2]
        }

        if (a[1]=="load_duration")
            load_ns += a[2]

        if (a[1]=="prompt_eval_duration")
            prompt_ns += a[2]

        if (a[1]=="eval_duration")
            eval_ns += a[2]

        if (a[1]=="total_duration")
            total_ns += a[2]

        if (a[1]=="prompt_tokens_per_second")
            prompt_speed += a[2]

        if (a[1]=="generation_tokens_per_second")
            generation_speed += a[2]
    }
}

END {

    if (calls > 0) {

        printf "Qwen call count:                    %d\n", calls

        printf "Average prompt tokens:              %.2f\n",
            prompt_tokens/calls

        printf "Minimum prompt tokens:              %d\n",
            min_prompt_tokens

        printf "Maximum prompt tokens:              %d\n",
            max_prompt_tokens

        printf "Average output tokens:              %.2f\n",
            output_tokens/calls

        printf "Maximum output tokens:              %d\n",
            max_output_tokens

        printf "Average model load duration:        %.2f sec\n",
            (load_ns/calls)/1000000000

        printf "Average prompt evaluation:          %.2f sec\n",
            (prompt_ns/calls)/1000000000

        printf "Average generation duration:        %.2f sec\n",
            (eval_ns/calls)/1000000000

        printf "Average model total duration:       %.2f sec\n",
            (total_ns/calls)/1000000000

        printf "Average prompt processing speed:    %.2f token/s\n",
            prompt_speed/calls

        printf "Average generation speed:           %.2f token/s\n",
            generation_speed/calls

        printf "Total prompt tokens:                %d\n",
            prompt_tokens

        printf "Total generated tokens:             %d\n",
            output_tokens

    } else {

        print "Qwen diagnostics bulunamadı."

    }

}' "$REPORT_DIR/qwen_diagnostics.log" \
    | tee "$REPORT_DIR/qwen_summary.txt"


# ============================================================
# 8. /usr/bin/time ÖNEMLİ DEĞERLERİ AYIR
# ============================================================

grep -E \
    'Elapsed|User time|System time|Percent of CPU|Maximum resident set size|Major|Minor|Voluntary context|Involuntary context|Swaps|Exit status' \
    "$REPORT_DIR/time_metrics.txt" \
    > "$REPORT_DIR/time_summary.txt" || true


# ============================================================
# 9. TEST SONU
# ============================================================

echo
echo "============================================================"
echo "20 İHALE TESTİ TAMAMLANDI"
echo "============================================================"
echo "Exit code : $EXIT_CODE"
echo "Seed      : $SEED"
echo "Rapor     : $REPORT_DIR"
echo

echo "---------------- SYSTEM ----------------"
cat "$REPORT_DIR/resource_summary.txt"

echo
echo "---------------- QWEN ------------------"
cat "$REPORT_DIR/qwen_summary.txt"

echo
echo "---------------- TIME ------------------"
cat "$REPORT_DIR/time_summary.txt"

echo
echo "---------------- ERRORS ----------------"
if [ -s "$REPORT_DIR/errors.log" ]; then
    cat "$REPORT_DIR/errors.log"
else
    echo "Loglarda hata kaydı bulunamadı."
fi

echo
echo "============================================================"

exit "$EXIT_CODE"
