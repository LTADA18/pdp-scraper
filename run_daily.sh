#!/usr/bin/env bash
# run_daily.sh — เก็บข้อมูลประจำวัน แล้วออกเป็น Excel
# ใช้:  bash run_daily.sh            (รันปกติ)
#      bash run_daily.sh --resume   (รันต่อจากที่ค้าง)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then echo "ยังไม่ได้ setup — รัน: bash setup.sh"; exit 1; fi
# shellcheck disable=SC1091
source .venv/bin/activate

DATE=$(TZ=Asia/Bangkok date +%F)
RAW="data/raw_${DATE}.ndjson"
OUT="output/Sales_Tracker_PDP_${DATE}.xlsx"
mkdir -p data output

EXTRA=""
if [ "${1:-}" = "--resume" ]; then EXTRA="--resume"; fi

echo "=== เก็บข้อมูล ${DATE} ==="
# shellcheck disable=SC2086
python scrape_pdp.py --urls urls.txt --out "$RAW" --delay 4 $EXTRA

echo
echo "=== แปลงเป็น Excel ==="
python normalize_pdp.py --inputs "$RAW" --date "$DATE" --out "$OUT"

echo
echo "ไฟล์: $OUT"
if   command -v open     >/dev/null 2>&1; then open "$OUT"          # macOS
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$OUT"      # Linux
fi
