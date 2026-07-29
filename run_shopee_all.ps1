# run_shopee_all.ps1 — เก็บ Shopee ทั้งหมด (ต้องเปิด Chrome CDP + login Shopee ค้างไว้ที่ port 9222)
# ใช้:  powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1
#      powershell -ExecutionPolicy Bypass -File run_shopee_all.ps1 -Delay 12   # ปรับให้ช้าลงถ้าโดน throttle
# เกาะ Chrome ตัวเดียวกับ TikTok ไม่ได้ ถ้าจะรันพร้อม TikTok ให้ TikTok ใช้ --cdp http://localhost:9223 แยก
param([double]$Delay = 8)   # Shopee API ไวต่อการยิงถี่ ตั้ง 8 วินาที (ยิงถี่กว่านี้เจอ "API ไม่ตอบ")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# ต้องมี Chrome เปิด debug port 9222 + login Shopee แล้ว
$ok = try { (Invoke-WebRequest "http://localhost:9222/json/version" -TimeoutSec 3 -UseBasicParsing).StatusCode } catch { 0 }
if ($ok -ne 200) {
  Write-Error "ไม่พบ Chrome ที่ port 9222 — เปิดก่อนด้วย:`n  & `"C:\Program Files\Google\Chrome\Application\chrome.exe`" --remote-debugging-port=9222 --user-data-dir=`"$PSScriptRoot\.chrome_cdp`"`nแล้ว login Shopee ในหน้าต่างนั้นให้เรียบร้อย"
  exit 1
}

Write-Host "=== เก็บ Shopee ทั้งหมด (Ctrl+C เพื่อหยุด, รันซ้ำ resume ต่อได้) ==="
Write-Host "delay = $Delay วินาที"
& ".\.venv\Scripts\python.exe" scrape_pdp.py --urls urls_shopee_all.txt --out "data\raw_shopee_all.ndjson" --platform shopee --cdp --resume --delay $Delay
