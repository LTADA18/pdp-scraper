# run_shopee_all.ps1 — เก็บ Shopee ทั้งหมด (ต้องเปิด Chrome CDP + login Shopee ค้างไว้ที่ port 9222)
# ใช้:  powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1
#      powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1 -Delay 12   # ปรับให้ช้าลงถ้าโดน throttle
# เกาะ Chrome ตัวเดียวกับ TikTok ไม่ได้ ถ้าจะรันพร้อม TikTok ให้ TikTok ใช้ --cdp http://localhost:9223 แยก
# โหมดแนะนำ (เบาที่สุด): -ApiOnly  = ไม่เปิดหน้า PDP ยิงแค่ API ~1.3 req/สินค้า
#   powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1 -ApiOnly -Limit 50   # ลองน้อย ๆ ก่อน
# ค่า default ตั้งจากผลจริง: delay 8 -> โดนกัน 22% (41/187) | delay 18 -> โดนที่ 58 ตัว
# เพิ่ม delay ไม่ช่วย เพราะตัวปัญหาคือ "จำนวน request" ไม่ใช่ความถี่ -> ใช้ -ApiOnly ดีกว่า
param(
  [switch]$ApiOnly,             # ยิง API อย่างเดียว ไม่เปิดหน้า PDP (ลด request ~100 เท่า)
  [double]$Delay = 18,          # โหมดปกติ 18 วิ / โหมด -ApiOnly ลดให้เอง (ดูด้านล่าง)
  [int]$BatchSize = 25,         # เก็บครบกี่ตัว พักเบรกทีนึง
  [double]$BatchCooldown = 180, # เบรกกี่วินาที ล้าง throttle ที่สะสม
  [int]$Limit = 400,            # โควตาต่อรอบ (0=ไม่จำกัด) — ยิงรวดเดียว 3274 ตัวเสี่ยงโดนแบน
  [int]$StopStreak = 8,         # โดนกันติดกันกี่ตัว = หยุดทันที กันโดนแบนบัญชี
  [string]$AlertSound = "C:\Windows\Media\Alarm03.wav"
)
# -ApiOnly ยิงน้อยกว่าเดิม ~100 เท่า เลยไม่ต้องหน่วงหนักเท่าโหมดเปิดหน้า
# (ยังไม่ต่ำกว่า 3 วิ ตามกฎเหล็ก และยังเบรกเป็นก้อนอยู่)
if ($ApiOnly -and -not $PSBoundParameters.ContainsKey('Delay'))         { $Delay = 6 }
if ($ApiOnly -and -not $PSBoundParameters.ContainsKey('BatchSize'))     { $BatchSize = 50 }
if ($ApiOnly -and -not $PSBoundParameters.ContainsKey('BatchCooldown')) { $BatchCooldown = 120 }
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
$modeArgs = @()
if ($ApiOnly) { $modeArgs += "--shopee-api-only" }

Write-Host "=== เก็บ Shopee (Ctrl+C เพื่อหยุด, รันซ้ำ resume ต่อได้) ==="
if ($ApiOnly) { Write-Host "โหมด API-only: ไม่เปิดหน้า PDP ยิงแค่ API (~1.3 request/สินค้า)" }
Write-Host "delay $Delay s | เบรก $BatchCooldown s ทุก $BatchSize ตัว | โควตารอบนี้ $Limit | หยุดถ้าโดนกันติดกัน $StopStreak ตัว"
& ".\.venv\Scripts\python.exe" scrape_pdp.py --urls urls_shopee_all.txt --out "data\raw_shopee_all.ndjson" --platform shopee --cdp --resume --delay $Delay --batch-size $BatchSize --batch-cooldown $BatchCooldown --stop-streak $StopStreak --limit $Limit @modeArgs @soundArgs
