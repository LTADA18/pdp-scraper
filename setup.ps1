# setup.ps1 — ติดตั้งครั้งเดียว (Windows PowerShell)
# ใช้:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> 1/5 ตรวจ Python"
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Write-Error "ไม่พบ Python — ติดตั้งจาก https://www.python.org/downloads/ (ติ๊ก 'Add to PATH')"; exit 1 }
python --version

Write-Host "==> 2/5 สร้าง virtual environment (.venv)"
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install --quiet --upgrade pip

Write-Host "==> 3/5 ติดตั้ง library"
pip install --quiet playwright pandas openpyxl

Write-Host "==> 4/5 ดาวน์โหลด Chromium (1-3 นาที ~150MB)"
playwright install chromium

Write-Host "==> 5/5 เตรียมไฟล์"
if (-not (Test-Path urls.txt)) {
@"
# ใส่ลิงก์สินค้า บรรทัดละ 1 ลิงก์  (# = คอมเมนต์)
# https://www.lazada.co.th/products/xxx-i1234567890-s1.html
# https://shopee.co.th/product/987758564/41126959038
# https://www.tiktok.com/view/product/1734103417235146278
"@ | Out-File -Encoding utf8 urls.txt
}
New-Item -ItemType Directory -Force -Path data, output | Out-Null
@"
.venv/
.browser_profile/
data/
output/
__pycache__/
"@ | Out-File -Encoding utf8 .gitignore

Write-Host ""
Write-Host "เสร็จแล้ว ขั้นต่อไป:"
Write-Host "  1) .\.venv\Scripts\Activate.ps1"
Write-Host "  2) python scrape_pdp.py --login      # ล็อกอิน Shopee ครั้งแรก"
Write-Host "  3) ใส่ลิงก์ใน urls.txt"
Write-Host "  4) powershell -ExecutionPolicy Bypass -File run_daily.ps1"
