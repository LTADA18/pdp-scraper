# PDP Scraper — รันบน Claude Code (Terminal)

ดึง **ชื่อสินค้า / ราคา / ราคาเดิม / spec / variation / platform** จาก Lazada, Shopee, TikTok Shop (TH)
เสริมจาก Apify pipeline เดิม ซึ่ง actor ทั้ง 4 ตัวไม่มี field spec และ variation

## ไฟล์

| ไฟล์ | หน้าที่ |
|---|---|
| `extract_pdp.js` | ตัวดึงข้อมูล รันในหน้าเว็บ (ใช้ร่วมกันได้ทั้ง Claude in Chrome และ Playwright) |
| `scrape_pdp.py` | เปิดเบราว์เซอร์ วนลิงก์ ฉีด JS เข้าไป → NDJSON |
| `normalize_pdp.py` | NDJSON → Excel ตามสคีมา Master File |
| `urls.txt` | รายการลิงก์สินค้า บรรทัดละ 1 |

## ติดตั้ง (ครั้งเดียว)

**macOS / Linux**
```bash
bash setup.sh
```

**Windows (PowerShell)**
```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

สคริปต์จะสร้าง `.venv`, ลง `playwright pandas openpyxl`, โหลด Chromium, สร้าง `urls.txt` และ `.gitignore` ให้

ถ้าอยากทำเอง:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install playwright pandas openpyxl
playwright install chromium
```

## ล็อกอินครั้งแรก (จำเป็นสำหรับ Shopee)

```bash
python scrape_pdp.py --login
```

เบราว์เซอร์จะเปิดขึ้น ล็อกอินให้ครบทั้ง 3 แพลตฟอร์ม แล้วกด Enter ในเทอร์มินัล
cookie จะถูกเก็บใน `.browser_profile/` ใช้ซ้ำได้ทุกวัน
**อย่า commit โฟลเดอร์นี้ขึ้น git** — ใส่ใน `.gitignore`

## รันประจำวัน

```bash
bash run_daily.sh              # macOS/Linux
bash run_daily.sh --resume     # รันต่อจากที่ค้าง
```

```powershell
powershell -ExecutionPolicy Bypass -File run_daily.ps1     # Windows
```

ได้ `data/raw_<วันที่>.ndjson` + `output/Sales_Tracker_PDP_<วันที่>.xlsx` แล้วเปิดไฟล์ให้อัตโนมัติ

สั่งเองทีละขั้นก็ได้:
```bash
source .venv/bin/activate
DATE=$(TZ=Asia/Bangkok date +%F)
python scrape_pdp.py --urls urls.txt --out data/raw_$DATE.ndjson --delay 4
python normalize_pdp.py --inputs data/raw_$DATE.ndjson --out output/Sales_Tracker_PDP_$DATE.xlsx
```

## ตั้งให้รันเองทุกเช้า 8 โมง (macOS/Linux)

```bash
crontab -e
# เพิ่มบรรทัดนี้ แก้ path ให้ตรง:
0 8 * * * cd /path/to/pdp_scraper && bash run_daily.sh >> data/cron.log 2>&1
```

⚠️ cron ต้องรัน `--headless` ถึงจะทำงานโดยไม่มีหน้าจอได้ ซึ่ง Shopee มักโดนบล็อก
แนะนำตั้ง cron เฉพาะลิสต์ Lazada ส่วน Shopee/TikTok รันมือแบบมีหน้าต่าง

## ทดสอบ extract_pdp.js โดยไม่ต้องเปิดเว็บจริง

```bash
node -e '
global.location={hostname:"www.lazada.co.th",href:"https://www.lazada.co.th/products/x-i123-s1.html"};
global.document={querySelector:()=>null,querySelectorAll:()=>({forEach:()=>{}})};
global.window={__moduleData__:{data:{root:{fields:{
  product:{title:"ทดสอบ"},
  skuInfos:{"0":[{skuId:"1",propPath:"1:10",price:{salePrice:{value:100},originalPrice:{value:200}}}]},
  skuBase:{props:[{pid:"1",name:"สี",values:[{vid:"10",name:"ดำ"}]}]},
  specifications:[{name:"Voltage",value:"20V"}]}}}}};
eval(require("fs").readFileSync("extract_pdp.js","utf8")).then(r=>console.log(JSON.stringify(r,null,1)));
'
```

## Flags ที่ใช้บ่อย

| Flag | ความหมาย |
|---|---|
| `--delay 4` | หน่วงระหว่างสินค้า (มี jitter สุ่มให้อัตโนมัติ) — **อย่าลดต่ำกว่า 3** |
| `--headless` | ไม่มีหน้าต่าง — เร็วกว่าแต่โดน anti-bot ง่ายกว่า ใช้กับ Lazada ได้ Shopee ไม่แนะนำ |
| `--retries 2` | จำนวนครั้งที่ลองใหม่ต่อสินค้า |
| `--resume` | ข้ามลิงก์ที่มีใน `--out` แล้ว |

## ผลลัพธ์

Excel 3 sheet:
- `products` — 1 แถว/สินค้า (มี `variation_summary`, `spec_summary` แบบย่อ)
- `variants` — 1 แถว/SKU พร้อมราคาแยกและ stock
- `specs` — long format สำหรับ pivot

ทุกแถวมี `source` = `state` / `api` / `dom`
**ถ้าเจอ `dom` แปลว่า state object ของเว็บเปลี่ยนแล้ว** ต้องไปแก้ `extract_pdp.js` ก่อนเชื่อตัวเลข

## จุดที่พังบ่อย (เรียงตามความน่าจะเป็น)

1. **TikTok** — schema เปลี่ยนบ่อยสุด โค้ดใช้ `deepFind` หา node ที่มี `sale_props`/`skus` แทน hardcode path เพื่อทนการเปลี่ยนแปลง แต่ก็ยังพังได้
2. **Shopee** — `/api/v4/pdp/get_pc` อาจตอบ error ถ้า cookie หมดอายุ → รัน `--login` ใหม่
3. **Lazada** — นิ่งที่สุด `window.__moduleData__` แทบไม่เปลี่ยน

## ข้อควรระวัง

- ยิงเร็วเกินไปโดนบล็อก IP หรือบัญชี — งาน bulk ยังควรใช้ Apify เหมือนเดิม ตัวนี้เหมาะกับลิสต์ที่ติดตามประจำ (หลักสิบ–ร้อยลิงก์/วัน)
- `product_id` ถูกบังคับเป็น text ในไฟล์ Excel แล้ว (กัน TikTok 19 หลักโดนปัดเหลือ 15)
- สคริปต์ไม่เดาค่า ขาดอะไรเว้นว่างและเขียนเหตุผลลงคอลัมน์ `notes`
