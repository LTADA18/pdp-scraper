# resume_tiktok.ps1 — ทำต่อ TikTok scan หลังคอมดับ/หลุด/ปิด Claude
# ข้อมูลถูกเซฟลงไฟล์ทุกลิงก์อยู่แล้ว (flush) ตัวนี้แค่เปิด Chrome + ทำต่อจากที่ค้าง
# ใช้:  powershell -ExecutionPolicy Bypass -File resume_tiktok.ps1
# มีคนเฝ้ากด CAPTCHA เอง อยากเร็วขึ้น:  ... -File resume_tiktok.ps1 -Delay 5
param(
  [double]$Delay = 15,          # หน่วงต่อสินค้า: 15=รันข้ามคืนไม่มีคน, 5=มีคนเฝ้ากด CAPTCHA เอง (ต่ำสุด 3)
  [double]$BatchCooldown = 90,  # เบรกทุก batch กัน CAPTCHA สะสม (0=ปิด) — แนะนำคงไว้แม้ delay ต่ำ
  [int]$BatchSize = 40
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1) เปิด Chrome debug (9222) ถ้ายังไม่เปิด
$up = $false
try { $up = (Invoke-WebRequest "http://localhost:9222/json/version" -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 } catch { $up = $false }
if (-not $up) {
  Write-Host "เปิด Chrome debug (9222)..."
  Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=$PSScriptRoot\.chrome_cdp"
  Write-Host "รอ Chrome พร้อม 6 วิ... (ถ้าเจอ CAPTCHA ในหน้าต่างนี้ ลากผ่านก่อนได้)"
  Start-Sleep -Seconds 6
}

# 2) ทำต่อจากที่ค้าง (resume ข้ามตัวที่เก็บแล้ว)
Write-Host "=== ทำต่อ TikTok scan (delay $Delay s, batch $BatchSize/$BatchCooldown s | Ctrl+C เพื่อหยุด, รันซ้ำได้) ==="
& ".\.venv\Scripts\python.exe" scrape_pdp.py --urls urls_tiktok_all.txt --out "data\raw_tiktok_all.ndjson" --platform tiktok --cdp --resume --delay $Delay --batch-size $BatchSize --batch-cooldown $BatchCooldown
