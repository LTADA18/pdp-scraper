# run_shopee_all.ps1 — เก็บ Shopee ทั้งหมด (ต้องเปิด Chrome CDP + login Shopee ค้างไว้ที่ port 9222)
# ใช้:  powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1
#      powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1 -Delay 12   # ปรับให้ช้าลงถ้าโดน throttle
# เกาะ Chrome ตัวเดียวกับ TikTok ไม่ได้ ถ้าจะรันพร้อม TikTok ให้ TikTok ใช้ --cdp http://localhost:9223 แยก
# โหมดแนะนำ (เบาที่สุด): -ApiOnly  = ไม่เปิดหน้า PDP ยิงแค่ API ~1.3 req/สินค้า
#   powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1 -ApiOnly -Limit 50   # ลองน้อย ๆ ก่อน
# ค่า default ตั้งจากผลจริง: delay 8 -> โดนกัน 22% (41/187) | delay 18 -> โดนที่ 58 ตัว
# เพิ่ม delay ไม่ช่วย เพราะตัวปัญหาคือ "จำนวน request" ไม่ใช่ความถี่ -> ใช้ -ApiOnly ดีกว่า
param(
  [switch]$Chunked,             # สูตรทีม: ก้อนละ 20 ลิงก์ + พัก 5 นาที + เจอ captcha รอคนกดแล้วลองซ้ำ (แนะนำ)
  [switch]$ApiOnly,             # ยิง API อย่างเดียว ไม่เปิดหน้า PDP (ลด request ~100 เท่า)
  [double]$CaptchaWait = 300,   # เจอ captcha: รอกี่วินาทีให้คนไปกดในหน้าต่าง Chrome
  [int]$CaptchaRounds = 5,      # ลองลิงก์เดิมซ้ำได้กี่ครั้ง (ไม่ข้าม ไม่หยุดรัน)
  [string]$ProfileDir = ".chrome_cdp",  # โปรไฟล์ Chrome — ตั้งใหม่ (เช่น .chrome_shopee) เพื่อล้าง cookie/fingerprint เดิม
  [int]$Port = 9222,            # debug port (ใช้ port อื่นได้ถ้าจะเปิดหลายโปรไฟล์พร้อมกัน)
  [double]$DelayMax = 0,        # เพดานหน่วงแบบสุ่ม 0=ใช้ delay x1.8 | เช่น -Delay 50 -DelayMax 75 = สุ่ม 50-75 วิ
  [double]$Delay = 18,          # หน่วงขั้นต่ำ / โหมด -ApiOnly ลดให้เอง (ดูด้านล่าง)
  [int]$BatchSize = 25,         # เก็บครบกี่ตัว พักเบรกทีนึง
  [double]$BatchCooldown = 180, # เบรกกี่วินาที ล้าง throttle ที่สะสม
  [int]$Limit = 400,            # โควตาต่อรอบ (0=ไม่จำกัด) — ยิงรวดเดียว 3274 ตัวเสี่ยงโดนแบน
  [int]$StopStreak = 8,         # โดนกันติดกันกี่ตัว = หยุดทันที กันโดนแบนบัญชี
  [string]$AlertSound = "C:\Windows\Media\Alarm03.wav"
)
# โหมด -Chunked = สูตรที่ทีมใช้แล้วผ่านจริง: ยิงเป็นก้อนเล็ก ๆ เร็ว ๆ แล้วพักยาว
#   20 ลิงก์ + พัก 5 นาที = ~240 ลิงก์/ชม. | เจอ captcha -> พัก 300s ให้คนกด แล้วลองลิงก์เดิมซ้ำ ไม่หยุดรัน
# (ต่างจากเดิมที่หน่วงทีละลิงก์นาน ๆ ซึ่งพิสูจน์แล้วว่าไม่ช่วย — Shopee นับจำนวนครั้งต่อ burst ไม่ใช่ความถี่)
if ($Chunked) {
  if (-not $PSBoundParameters.ContainsKey('Delay'))         { $Delay = 3 }      # ในก้อนยิงเร็ว (ต่ำสุด 3 ตามกฎเหล็ก)
  if (-not $PSBoundParameters.ContainsKey('DelayMax'))      { $DelayMax = 6 }
  if (-not $PSBoundParameters.ContainsKey('BatchSize'))     { $BatchSize = 20 }     # ก้อนละ 20 ลิงก์
  if (-not $PSBoundParameters.ContainsKey('BatchCooldown')) { $BatchCooldown = 300 }# พัก 5 นาทีระหว่างก้อน
  if (-not $PSBoundParameters.ContainsKey('CaptchaWait'))   { $CaptchaWait = 300 }  # เจอ captcha รอคนกด 5 นาที
  if (-not $PSBoundParameters.ContainsKey('CaptchaRounds')) { $CaptchaRounds = 5 }  # ลองซ้ำสูงสุด 5 ครั้ง
  if (-not $PSBoundParameters.ContainsKey('StopStreak'))    { $StopStreak = 0 }     # 0 = ไม่หยุดเอง
}
elseif ($ApiOnly) {
  # -ApiOnly ยิงน้อยกว่าเดิม ~100 เท่า เลยไม่ต้องหน่วงหนักเท่าโหมดเปิดหน้า
  if (-not $PSBoundParameters.ContainsKey('Delay'))         { $Delay = 6 }
  if (-not $PSBoundParameters.ContainsKey('BatchSize'))     { $BatchSize = 50 }
  if (-not $PSBoundParameters.ContainsKey('BatchCooldown')) { $BatchCooldown = 120 }
}
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# เปิด Chrome ด้วยโปรไฟล์ที่เลือก ถ้ายังไม่เปิดอยู่
# โปรไฟล์ใหม่ = cookie/localStorage/fingerprint ชุดใหม่ (แต่ IP ยังตัวเดิม ถ้าโดนที่ IP ก็ยังโดน)
$profilePath = Join-Path $PSScriptRoot $ProfileDir
$ok = try { (Invoke-WebRequest "http://localhost:$Port/json/version" -TimeoutSec 3 -UseBasicParsing).StatusCode } catch { 0 }
if ($ok -ne 200) {
  if (-not (Test-Path $profilePath)) {
    New-Item -ItemType Directory -Path $profilePath | Out-Null
    Write-Host "สร้างโปรไฟล์ใหม่: $profilePath (ยังไม่ได้ล็อกอิน — สะอาด ไม่มีประวัติเดิม)"
  }
  Write-Host "เปิด Chrome (port $Port, profile $ProfileDir)..."
  Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" `
    -ArgumentList "--remote-debugging-port=$Port", "--user-data-dir=$profilePath", "https://shopee.co.th/"
  Start-Sleep -Seconds 10
  $ok = try { (Invoke-WebRequest "http://localhost:$Port/json/version" -TimeoutSec 5 -UseBasicParsing).StatusCode } catch { 0 }
  if ($ok -ne 200) { Write-Error "เปิด Chrome ไม่สำเร็จที่ port $Port"; exit 1 }
} else {
  Write-Host "ใช้ Chrome ที่เปิดอยู่แล้วที่ port $Port"
}

$soundArgs = @()
if ($AlertSound -and (Test-Path $AlertSound)) { $soundArgs += @("--alert-sound", $AlertSound) }
$modeArgs = @()
if ($ApiOnly) { $modeArgs += "--shopee-api-only" }

$delayArgs = @("--delay", $Delay)
if ($DelayMax -gt $Delay) { $delayArgs += @("--delay-max", $DelayMax) }
$shownDelay = if ($DelayMax -gt $Delay) { "$Delay-$DelayMax s (สุ่ม)" } else { "$Delay-$([math]::Round($Delay*1.8,1)) s (สุ่ม)" }

$perHour = if ($BatchCooldown -gt 0 -and $BatchSize -gt 0) {
  [math]::Round(3600 / (($BatchSize * ($Delay + $DelayMax) / 2) + $BatchCooldown) * $BatchSize)
} else { 0 }

Write-Host "=== เก็บ Shopee (Ctrl+C เพื่อหยุด, รันซ้ำ resume ต่อได้) ==="
if ($ApiOnly) { Write-Host "โหมด API-only: ไม่เปิดหน้า PDP ยิงแค่ API (~1.3 request/สินค้า)" }
if ($Chunked) { Write-Host "โหมด Chunked: ก้อนละ $BatchSize ลิงก์ + พัก $BatchCooldown s | เจอ captcha รอ $CaptchaWait s ให้คนกด แล้วลองลิงก์เดิมซ้ำ (สูงสุด $CaptchaRounds ครั้ง) ไม่หยุดรัน" }
Write-Host "delay $shownDelay | เบรก $BatchCooldown s ทุก $BatchSize ตัว | โควตารอบนี้ $Limit | ประมาณ $perHour ลิงก์/ชม."
if ($StopStreak -le 0) { Write-Host "ไม่หยุดเองเมื่อโดนกัน (StopStreak=0)" } else { Write-Host "หยุดถ้าโดนกันติดกัน $StopStreak ตัว" }
& ".\.venv\Scripts\python.exe" scrape_pdp.py --urls urls_shopee_all.txt --out "data\raw_shopee_all.ndjson" --platform shopee --cdp "http://localhost:$Port" --resume @delayArgs --batch-size $BatchSize --batch-cooldown $BatchCooldown --stop-streak $StopStreak --limit $Limit --captcha-wait $CaptchaWait --captcha-rounds $CaptchaRounds @modeArgs @soundArgs
