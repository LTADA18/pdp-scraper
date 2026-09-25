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

## ข้อควรรู้

- รหัสเอกสารผลตรวจ = `lzd-<product_id>-<YYYYmmddHHMM>` ตรวจซ้ำได้เอกสารใหม่ ไม่ทับของเดิม (เก็บเป็นประวัติ)
- db ของหน้าเว็บจุ 5,000 เอกสาร — ผลตรวจ 1 หน้าใช้ ~20 เอกสาร (รูปแยกเอกสารละรูป เพราะจำกัด 256 KiB/เอกสาร)
  ใกล้เต็มให้ลบผลเก่า (results/<id> + results/<id>/imgs/*)
- ใช้เวลา: ลิงก์ละ ~20 วิ · ร้านละ ~2–4 นาที (ตรวจเร็วทุกหน้า + ตรวจลึก 10 หน้า) · delay ≥ 3 วิ ตามกฎเหล็ก
- ข้อ ★ "ใช้คำตรงกับ price list" (ของในกล่อง/สเปก/คีย์ฟีเจอร์) ยังตรวจไม่ได้ — `intel.ref_portal_product`
  ไม่มี 3 ช่องนี้ จึงเป็น pending ไม่หักคะแนน รอเจ้าของงานบอกแหล่งข้อมูล
