# Listing Scorecard — ขั้นตอนประมวลคิว (Lazada)

หน้าเว็บ: https://claude.ai/artifact/SPn3CN8Nxy6Bb2iTz3prh3 (Artifact มี db)
คนใส่ลิงก์สินค้า/ชื่อร้าน -> เอกสาร `requests/<id>` สถานะ `queued`
Claude ประมวลตามขั้นข้างล่าง -> เขียนผลกลับเข้า db -> หน้าเว็บขึ้นผลเอง

| ไฟล์ | หน้าที่ |
|---|---|
| `scorecard.py` | ต่อ Chrome (CDP) เปิดหน้าจริง ใช้ `extract_pdp.js` ให้คะแนน เขียนไฟล์ละเอกสาร + `manifest.json` |
| `scorecard_rules.py` | เกณฑ์ทั้งหมด (★ เกณฑ์หลักของเจ้าของงาน) + อ่านทะเบียนสินค้า/price list จาก Postgres (อ่านอย่างเดียว) |
| `scorecard_batches.py` | manifest -> ชุดคำสั่ง ArtifactData batch (≤ 50 รายการ, ≤ 850 KB ต่อชุด) |

## 0. เตรียม Chrome (ครั้งเดียวต่อการเปิดเครื่อง)

```powershell
curl http://127.0.0.1:9222/json/version     # ตอบ = พร้อม
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\Users\tada.p\pdp-scraper\.chrome_cdp"
```

- **Chrome อัตโนมัติ (headless/launch ใหม่) โดน Lazada ขึ้น CAPTCHA** — ต้องใช้หน้าต่างนี้เท่านั้น
- เจอ CAPTCHA = หยุด ให้คนกดผ่านเอง **ห้ามพยายามผ่านเอง**
- สคริปต์บังคับภาษาไทยด้วยคุกกี้ `hng=TH|th|THB|764` ทุกครั้ง — โปรไฟล์นี้เคยตั้งภาษาอังกฤษ
  แล้วได้ชื่อสินค้าแปลอังกฤษ ("Ochd802-N") เกณฑ์ที่หาคำไทยตรวจผิดหมด

## 1. หยิบคิว

ArtifactData `query` collection `requests` where `status == "queued"` order by `created_at`
จดเลข `version` ของแต่ละคำขอ — **ทุกการเขียนทับเอกสารเดิมต้อง pin `if_version`** ไม่งั้น db ปฏิเสธทั้ง batch

## 2. ต่อคำขอ

1. `update` คำขอ -> `{"status":"running","started_at":<ISO>}` (pin version ที่อ่านมา) จด version ใหม่
2. รัน
   ```bash
   .venv/Scripts/python.exe scorecard.py --url "<input>" --request-id <doc_id>      # kind = url
   .venv/Scripts/python.exe scorecard.py --shop "<input>" --request-id <doc_id>     # kind = shop (ตรวจลึก 10 หน้า)
   ```
3. `.venv/Scripts/python.exe scorecard_batches.py output/scorecard/<doc_id> --request-version <version จากข้อ 1>`
4. ส่งทุกชุดด้วย ArtifactData `batch` **ตามลำดับ** (รูป -> ผลสินค้า -> ร้าน -> คำขอ)
   หน้าเว็บเห็นคำว่า "เสร็จ" เมื่อข้อมูลครบแล้วเท่านั้น
5. สคริปต์ล้ม/ติด CAPTCHA -> `summary.json` มี `status: error` + `note` -> ขั้น 3–4 เขียนสถานะ error ให้คนเห็นเหตุผล
6. **ตรวจทั้งร้านล้มกลางทางไม่เสียงาน** — ผลแต่ละหน้าเขียนลงดิสก์ทันที สั่ง `--request-id` เดิมซ้ำ
   สคริปต์หยิบหน้าที่ตรวจแล้ว (ไม่เก่ากว่า `--resume-hours` ค่าเริ่ม 96 ชม. ข้ามเสาร์-อาทิตย์ได้) มาใช้ต่อ แล้วตรวจเฉพาะหน้าที่ขาด
   ส่งคำขอกลับเข้าคิวได้เลย รอบถัดไปจะทำต่อเอง · อยากตรวจใหม่หมดให้ใส่ `--resume-hours 0`
7. **ขึ้น CAPTCHA = รอคนกด ไม่หยุดทันที** — แท็บที่ติดถูกดึงขึ้นหน้าสุด log ขึ้น `⏸ CAPTCHA — รอคนกดผ่าน`
   สคริปต์เช็คทุก 15 วิ กดผ่านแล้วทำต่อเอง (`▶ ผ่าน CAPTCHA แล้ว ทำต่อ`) รอได้ `--captcha-wait` นาที (ค่าเริ่ม 20)
   เกินนั้นค่อยหยุดเป็น error — **สคริปต์ไม่พยายามผ่านเองเด็ดขาด** มีแค่คนกดเท่านั้น
   เจ้าของงานเลือกให้ตรวจรวดเดียวแล้วกดผ่านเอง แทนการแบ่งรอบ (2026-09-26)
   Lazada ขึ้น CAPTCHA หลังเปิดหน้าสินค้าราว 30–100 หน้าติดกัน · ถ้าอยากแบ่งรอบใช้ `--max-new N`
   (ครบ N หน้าใหม่ = `status: partial` exit 3 รอบหน้าทำต่อ · ค่าเริ่ม 0 = ไม่จำกัด)

## ข้อควรรู้

- รหัสเอกสารผลตรวจ = `lzd-<product_id>-<YYYYmmddHHMM>` ตรวจซ้ำได้เอกสารใหม่ ไม่ทับของเดิม (เก็บเป็นประวัติ)
- db ของหน้าเว็บจุ 5,000 เอกสาร — สินค้า 1 หน้าใช้ ~2 เอกสาร (results + cards) รูปแกลเลอรีเก็บเฉพาะ 10 หน้าขายดี/ร้าน
  ร้าน 300 หน้า ≈ 700 เอกสาร (3 ร้านแรก 614 หน้าใช้ไป 1,431)
- **ตรวจร้านเดิมซ้ำ = ลบรอบเก่าอัตโนมัติ** (เจ้าของงานสั่ง 2026-09-26) หลังเขียนรอบใหม่ครบแล้ว:
  1. ArtifactData `list` collection `shops` (out_dir -> `output/_snap/<stamp>`)
  2. `python scorecard_cleanup.py output/_snap/<stamp>` -> batches ของคำสั่ง delete
  3. ส่งทีละ batch — เก็บรอบล่าสุดต่อร้าน ลบร้านรอบเก่า + รูปย่อ และผลสินค้าที่รอบล่าสุดชี้ไปผลอื่นแล้ว
  4. `python scorecard_cleanup.py --mark <plan.json> [--only 0,1]` จดชุดที่สำเร็จลง `output/scorecard/_deleted.json`
  ผลที่ตรวจทีละลิงก์ไม่ถูกลบ · ทะเบียนว่าเคยเขียนอะไรขึ้น db = `manifest.json` ของรอบที่ status "done" (ไม่ต้อง list cards หลายร้อย)
  ⚠️ db ลบแบบไม่ pin ได้เฉพาะเอกสารที่ไม่มีอยู่ · ลบของที่มีอยู่ต้อง pin if_version ตรงเป๊ะ · pin ของที่ไม่มีอยู่ก็ล้มทั้งชุด
     ทุกเอกสารเขียนด้วย set ครั้งเดียว = version 1 ตัวลบจึง pin 1 ให้ (ไม่ใช่ 1 ใส่ `--versions`)
     ห้ามข้ามขั้น --mark ไม่งั้นรอบหน้าจะ pin ของที่ลบไปแล้วจนล้มทั้งชุด
  ⚠️ ห้ามลบไฟล์ใน `output/scorecard/` — ตัวลบใช้เป็นทะเบียน
- ตรวจทั้งร้าน: สินค้ารุ่นเดียวกันหลายหน้าค้นหน้า search ครั้งเดียว (เก็บผลค้นไว้ใช้ซ้ำในรอบ)
- ใช้เวลา: ลิงก์ละ ~20 วิ · ร้านละ ~2–4 นาที (ตรวจเร็วทุกหน้า + ตรวจลึก 10 หน้า) · delay ≥ 3 วิ ตามกฎเหล็ก
- ข้อ ★ "ใช้คำตรงกับ price list" (ของในกล่อง/สเปก/คีย์ฟีเจอร์) ยังตรวจไม่ได้ — `intel.ref_portal_product`
  ไม่มี 3 ช่องนี้ จึงเป็น pending ไม่หักคะแนน รอเจ้าของงานบอกแหล่งข้อมูล
