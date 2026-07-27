#!/usr/bin/env bash
# setup.sh — ติดตั้งครั้งเดียว (macOS / Linux)
# ใช้: bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/5 ตรวจ Python"
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python  >/dev/null 2>&1; then PY=python
else echo "ไม่พบ Python — ติดตั้งจาก https://www.python.org/downloads/ ก่อน"; exit 1; fi
$PY --version

echo "==> 2/5 สร้าง virtual environment (.venv)"
[ -d .venv ] || $PY -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

echo "==> 3/5 ติดตั้ง library"
pip install --quiet playwright pandas openpyxl

echo "==> 4/5 ดาวน์โหลด Chromium (ครั้งแรกใช้เวลา 1-3 นาที ~150MB)"
playwright install chromium

echo "==> 5/5 เตรียมไฟล์"
[ -f urls.txt ] || cat > urls.txt <<'EOF'
# ใส่ลิงก์สินค้า บรรทัดละ 1 ลิงก์  (# = คอมเมนต์)
# https://www.lazada.co.th/products/xxx-i1234567890-s1.html
# https://shopee.co.th/product/987758564/41126959038
# https://www.tiktok.com/view/product/1734103417235146278
EOF
mkdir -p data output
cat > .gitignore <<'EOF'
.venv/
.browser_profile/
data/
output/
__pycache__/
EOF

cat <<'EOF'

เสร็จแล้ว ขั้นต่อไป:

  1) ล็อกอินครั้งแรก (จำเป็นสำหรับ Shopee):
       source .venv/bin/activate
       python scrape_pdp.py --login

  2) ใส่ลิงก์สินค้าลงใน urls.txt

  3) รันประจำวัน:
       bash run_daily.sh

EOF
