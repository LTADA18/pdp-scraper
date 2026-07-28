# run_lazada_all.ps1 — เก็บ Lazada ทั้งหมด (รันข้ามคืน ไม่ต้องเฝ้า — Lazada ไม่มี CAPTCHA)
# ใช้:  powershell -ExecutionPolicy Bypass -File run_lazada_all.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== เก็บ Lazada ทั้งหมด (Ctrl+C เพื่อหยุด, รันซ้ำ resume ต่อได้) ==="
& ".\.venv\Scripts\python.exe" scrape_pdp.py --urls urls_lazada_all.txt --out "data\raw_lazada_all.ndjson" --platform lazada --lazada-sold --no-cdp --resume --delay 10
