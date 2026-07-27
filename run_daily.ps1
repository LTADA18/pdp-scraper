# run_daily.ps1 — เก็บข้อมูลประจำวัน (Windows)
# ใช้:  .\run_daily.ps1
#      .\run_daily.ps1 -Resume
#      .\run_daily.ps1 -Platform tiktok -Cdp      <- TikTok ต้องใช้ -Cdp (ดู CLAUDE.md)
param(
  [switch]$Resume,
  [ValidateSet("lazada","shopee","tiktok")][string]$Platform,
  [switch]$Cdp,                                  # เกาะ Chrome ที่เปิดค้างไว้ที่ port 9222
  [double]$Delay = 0                             # 0 = เลือกให้อัตโนมัติตามแพลตฟอร์ม
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { Write-Error "ยังไม่ได้ setup — รัน setup.ps1 ก่อน"; exit 1 }
& .\.venv\Scripts\Activate.ps1

$DATE = (Get-Date).ToUniversalTime().AddHours(7).ToString("yyyy-MM-dd")   # เวลาไทย
$RAW  = "data\raw_$DATE.ndjson"
$OUT  = "output\Sales_Tracker_PDP_$DATE.xlsx"
New-Item -ItemType Directory -Force -Path data, output | Out-Null

# TikTok ไวต่อการยิงถี่มาก (เทสต์ 300: delay 8 ผ่านแค่ 35%) ถ่างเป็น 15 วินาที ที่เหลือ 4 (ห้ามต่ำกว่า 3 — กฎเหล็กข้อ 3)
if ($Delay -le 0) { $Delay = if ($Platform -eq "tiktok") { 15 } else { 4 } }

$ARGS_ = @("--urls","urls.txt","--out",$RAW,"--delay","$Delay")
if ($Resume)   { $ARGS_ += "--resume" }
if ($Platform) { $ARGS_ += @("--platform",$Platform) }
# เก็บยอดขาย Lazada จากหน้า search (PDP ไม่มี) — เปิดเมื่อทำ Lazada หรือทำทุกแพลตฟอร์ม
if (-not $Platform -or $Platform -eq "lazada") { $ARGS_ += "--lazada-sold" }
if ($Cdp) {
  $ARGS_ += "--cdp"
  $probe = try { (Invoke-WebRequest "http://localhost:9222/json/version" -TimeoutSec 3 -UseBasicParsing).StatusCode } catch { 0 }
  if ($probe -ne 200) {
    Write-Error "ไม่พบ Chrome ที่ port 9222 — เปิดก่อนด้วย:`n  & `"C:\Program Files\Google\Chrome\Application\chrome.exe`" --remote-debugging-port=9222 --user-data-dir=`"$PSScriptRoot\.chrome_cdp`""
    exit 1
  }
}

$TAG = ""
if ($Platform) { $TAG = " ($Platform อย่างเดียว)" }
if ($Cdp)      { $TAG = "$TAG [cdp]" }

Write-Host "=== เก็บข้อมูล $DATE$TAG  delay=$Delay ==="
python scrape_pdp.py @ARGS_

Write-Host "`n=== แปลงเป็น Excel ==="
python normalize_pdp.py --inputs $RAW --date $DATE --out $OUT

Write-Host "`nไฟล์: $OUT"
Invoke-Item $OUT
