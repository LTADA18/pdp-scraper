# run_lazada_watch.ps1 — เก็บ Lazada แบบ "นั่งเฝ้า" (มีคนกดผ่าน reCAPTCHA เอง)
# reCAPTCHA เด้ง -> รอ 15s ลองใหม่ (กดผ่านในหน้าต่างเบราว์เซอร์ระหว่างนั้น) ลองได้ 8 รอบ
# ไม่มีพักยาว/เบรก (พวกนั้นไว้โหมดไม่มีคน = run_lazada_all.ps1)
# ใช้:  powershell -ExecutionPolicy Bypass -File run_lazada_watch.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== เก็บ Lazada (โหมดเฝ้า) — reCAPTCHA เด้งให้กดผ่านในหน้าต่างเบราว์เซอร์ ==="
& ".\.venv\Scripts\python.exe" scrape_pdp.py --urls urls_lazada_all.txt --out "data\raw_lazada_all.ndjson" --platform lazada --lazada-sold --no-cdp --resume --delay 10 --captcha-cooldown 15 --captcha-retries 8 --captcha-streak 9999 --batch-cooldown 0
