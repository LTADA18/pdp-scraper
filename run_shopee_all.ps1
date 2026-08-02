# run_shopee_all.ps1 — เก็บ Shopee ทั้งหมด (ต้องเปิด Chrome CDP + login Shopee ค้างไว้ที่ port 9222)
# ใช้:  powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1
#      powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1 -Delay 12   # ปรับให้ช้าลงถ้าโดน throttle
# เกาะ Chrome ตัวเดียวกับ TikTok ไม่ได้ ถ้าจะรันพร้อม TikTok ให้ TikTok ใช้ --cdp http://localhost:9223 แยก
# ค่า default ตั้งจากผลจริงรอบก่อน: delay 8 -> โดนกัน 22% (41/187) และท้ายรอบโดนรัว 7 ตัวติด
param(
  [double]$Delay = 18,          # Shopee ไวมาก 8 วิไม่พอ — 18 วิปลอดภัยกว่ามาก
  [int]$BatchSize = 25,         # เก็บครบ 25 ตัว พักเบรกทีนึง
  [double]$BatchCooldown = 180, # เบรก 3 นาที ล้าง throttle ที่สะสม
  [int]$Limit = 400,            # โควตาต่อรอบ (0=ไม่จำกัด) — ยิงรวดเดียว 3351 ตัวเสี่ยงโดนแบน
  [int]$StopStreak = 8,         # โดนกันติดกันกี่ตัว = หยุดทันที กันโดนแบนบัญชี
  [string]$AlertSound = "C:\Windows\Media\Alarm03.wav"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# ต้องมี Chrome เปิด debug port 9222 + login Shopee แล้ว
$ok = try { (Invoke-WebRequest "http://localhost:9222/json/version" -TimeoutSec 3 -UseBasicParsing).StatusCode } catch { 0 }
if ($ok -ne 200) {
  Write-Error "ไม่พบ Chrome ที่ port 9222 — เปิดก่อนด้วย:`n  & `"C:\Program Files\Google\Chrome\Application\chrome.exe`" --remote-debugging-port=9222 --user-data-dir=`"$PSScriptRoot\.chrome_cdp`"`nแล้ว login Shopee ในหน้าต่างนั้นให้เรียบร้อย"
  exit 1
}

$soundArgs = @()
if ($AlertSound -and (Test-Path $AlertSound)) { $soundArgs += @("--alert-sound", $AlertSound) }

Write-Host "=== เก็บ Shopee (Ctrl+C เพื่อหยุด, รันซ้ำ resume ต่อได้) ==="
Write-Host "delay $Delay s | เบรก $BatchCooldown s ทุก $BatchSize ตัว | โควตารอบนี้ $Limit | หยุดถ้าโดนกันติดกัน $StopStreak ตัว"
& ".\.venv\Scripts\python.exe" scrape_pdp.py --urls urls_shopee_all.txt --out "data\raw_shopee_all.ndjson" --platform shopee --cdp --resume --delay $Delay --batch-size $BatchSize --batch-cooldown $BatchCooldown --stop-streak $StopStreak --limit $Limit @soundArgs
