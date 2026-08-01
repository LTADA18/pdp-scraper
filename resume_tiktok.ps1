# resume_tiktok.ps1 — ทำต่อ TikTok scan หลังคอมดับ/หลุด/ปิด Claude
# ข้อมูลถูกเซฟลงไฟล์ทุกลิงก์อยู่แล้ว (flush) ตัวนี้แค่เปิด Chrome + ทำต่อจากที่ค้าง
# ใช้:  powershell -ExecutionPolicy Bypass -File resume_tiktok.ps1
# มีคนเฝ้ากด CAPTCHA เอง อยากเร็วขึ้น:  ... -File resume_tiktok.ps1 -Delay 5
# เปลี่ยนเสียงเตือน CAPTCHA:        ... -File resume_tiktok.ps1 -Delay 5 -AlertSound "C:\Windows\Media\Alarm03.wav"
param(
  [string]$Urls = "urls_tiktok_all.txt",   # ลิสต์ที่จะเก็บ (เช่น urls_tiktok_recover.txt = ตัวที่กู้จาก product_id)
  [double]$Delay = 15,          # หน่วงต่อสินค้า: 15=รันข้ามคืนไม่มีคน, 5=มีคนเฝ้ากด CAPTCHA เอง (ต่ำสุด 3)
  [double]$BatchCooldown = 90,  # เบรกทุก batch กัน CAPTCHA สะสม (0=ปิด) — แนะนำคงไว้แม้ delay ต่ำ
  [int]$BatchSize = 40,
  [string]$AlertSound = "",     # ไฟล์เสียงเตือนเอง (.wav เท่านั้น) ว่าง = บี๊บธรรมดา
  [int]$AlertFreq = 880,        # ความถี่บี๊บ Hz สูง=แหลม (ใช้เมื่อไม่ได้ใส่ -AlertSound)
  [int]$AlertDuration = 300,    # ความยาวบี๊บต่อครั้ง ms
  [switch]$NoAlert,             # ปิดเสียงเตือนทั้งหมด
  [double]$CaptchaWait = 30,    # เจอ CAPTCHA: ค้างรอคนกดรอบละกี่วินาที
  [int]$CaptchaRounds = 8       # รอกี่รอบก่อนข้ามลิงก์ (8 x 30s = 4 นาที)
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

# 2) ประกอบ argument เสียงเตือน (ใส่เฉพาะที่ผู้ใช้สั่ง)
$soundArgs = @()
if ($AlertSound) {
  if (Test-Path $AlertSound) {
    $soundArgs += @("--alert-sound", $AlertSound)
    Write-Host "เสียงเตือน: $AlertSound"
  } else {
    Write-Warning "ไม่พบไฟล์เสียง $AlertSound — ใช้เสียงบี๊บแทน"
  }
}
$soundArgs += @("--alert-freq", $AlertFreq, "--alert-duration", $AlertDuration)
if ($NoAlert) { $soundArgs += "--no-alert"; Write-Host "ปิดเสียงเตือน (-NoAlert)" }

# 3) ทำต่อจากที่ค้าง (resume ข้ามตัวที่เก็บแล้ว)
Write-Host "=== ทำต่อ TikTok scan [$Urls] (delay $Delay s, batch $BatchSize/$BatchCooldown s, captcha รอ $CaptchaWait s x $CaptchaRounds รอบ | Ctrl+C เพื่อหยุด, รันซ้ำได้) ==="
& ".\.venv\Scripts\python.exe" scrape_pdp.py --urls $Urls --out "data\raw_tiktok_all.ndjson" --platform tiktok --cdp --resume --delay $Delay --batch-size $BatchSize --batch-cooldown $BatchCooldown --captcha-wait $CaptchaWait --captcha-rounds $CaptchaRounds @soundArgs
