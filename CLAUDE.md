# CLAUDE.md

โปรเจกต์เก็บข้อมูลสินค้า Lazada / Shopee / TikTok Shop (ตลาดไทย) รายวัน
ส่วนเสริมของ Apify pipeline เดิม — ตัวนี้เก็บ **spec และ variation** ซึ่ง Apify actor ไม่มีให้

## คำสั่งประจำ

```bash
bash setup.sh                        # ติดตั้งครั้งเดียว
source .venv/bin/activate            # ทุกครั้งก่อนรันคำสั่ง python
python scrape_pdp.py --login         # ล็อกอินครั้งแรก (Shopee ต้องมี cookie)
bash run_daily.sh                    # เก็บ + แปลงเป็น Excel
bash run_daily.sh --resume           # รันต่อจากที่ค้าง
```

บน Windows ใช้ `.ps1` แทน (`.\setup.ps1`, `.\run_daily.ps1`)

### TikTok ต้องใช้โหมด CDP

TikTok บล็อกเบราว์เซอร์ที่ Playwright เปิดเอง (Security Check ทุกครั้ง) แต่ถ้า**คนเปิดเบราว์เซอร์เอง**แล้วให้สคริปต์เกาะเข้าไปจะผ่าน

```powershell
# 1) เปิด Chrome ค้างไว้ (แยก user-data-dir เพราะ Chrome 136+ ห้าม debug บนโปรไฟล์หลัก)
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$PWD\.chrome_cdp"
# 2) เข้าหน้าสินค้าสักตัว ถ้าเจอ CAPTCHA ลากผ่านเอง แล้วเปิดหน้าต่างค้างไว้
# 3) เก็บข้อมูล
.\run_daily.ps1 -Platform tiktok -Cdp
```

สคริปต์จะเปิดแท็บใหม่ในหน้าต่างนั้นและปิดเฉพาะแท็บของตัวเอง ไม่แตะแท็บผู้ใช้
`-Delay` ของ TikTok ตั้งไว้ 8 วินาที ถ้าเร็วกว่านี้จะเริ่มเจอ CAPTCHA กลางคัน

**ถ้ามี Chrome เปิดที่ port 9222 อยู่แล้ว สคริปต์จะเกาะให้อัตโนมัติ** ไม่ต้องใส่ `--cdp` และไม่มีหน้าต่างใหม่เด้ง
สั่งให้เปิดหน้าต่างใหม่เสมอด้วย `--no-cdp`

## โครงสร้าง

```
extract_pdp.js     ตัวดึงข้อมูล รันในหน้าเว็บ (อ่าน state object ของแต่ละแพลตฟอร์ม)
scrape_pdp.py      Playwright เปิดหน้า → ฉีด JS → NDJSON
normalize_pdp.py   NDJSON → Excel 3 sheet (products / variants / specs)
urls.txt           ลิสต์ลิงก์ที่ติดตาม
data/              NDJSON ดิบรายวัน
output/            Excel ที่ส่งให้ทีม
```

## ยอดขาย (sold) + ชื่อร้าน

ทุก record มี `shop_name`, `sold_count` (ยอดสะสมเลขเป๊ะ), `sold_band` (ข้อความช่วง เช่น "3k+")

| แพลตฟอร์ม | ชื่อร้าน | sold_count |
|---|---|---|
| TikTok | `deepFind` node ที่มี seller_name+seller_id | `product_model.sold_count` ✓ |
| Lazada | `fields.seller.name` ✓ | ดึงจากหน้า search (`--lazada-sold`) — ดูด้านล่าง |
| Shopee | `item.shop_name` / เรียก `get_shop_detail` | `historical_sold` เลขเป๊ะ — **ต้องเรียก `item/get`** |

**การนับยอดขายรายวัน/เดือน** — `normalize_pdp.py` เก็บ `sold_count` สะสมลง `data/sold_history.csv`
ทุกวัน แล้วคำนวณให้เอง:
- `sold_today` = วันนี้ − snapshot ล่าสุดก่อนหน้า
- `sold_mtd` = วันนี้ − ยอด ณ ต้นเดือน (ถ้าเริ่มเก็บกลางเดือน จะนับจากวันแรกที่เก็บ)
- ยอดติดลบ (ร้านรีเซ็ต/ข้อมูลเพี้ยน) → เว้นว่าง ไม่รายงานมั่ว
- **ห้ามลบ `data/sold_history.csv`** — เป็นฐานคำนวณ diff ทั้งหมด (อยู่ใน data/ ไม่ถูก commit)

### Lazada sold — ดึงจากหน้า search (`--lazada-sold`)

PDP ไม่มี sold รายสินค้า แต่ catalog AJAX มี: `GET /catalog/?ajax=true&page=N&q=<ชื่อสินค้า>`
คืน array ที่มี `nid` (=product_id) + `itemSoldCntShow` — `scrape_pdp.py` ยิง fetch นี้แบบ same-origin
บนหน้า PDP ที่โหลดอยู่แล้ว (ไม่ต้องเปิดหน้าใหม่) แล้ว match ด้วย nid, วนหา 3 หน้า

- **ลองหลาย query** (`_lazada_queries`): ชื่อเต็ม → แบรนด์+รหัสรุ่น → รหัสรุ่นล้วน หยุดเมื่อเจอ nid
  (ชื่อเต็มบางตัวมีคำเยอะจนกลบรหัสรุ่น ค้นไม่ติดอันดับ ต้องมี query เจาะจงสำรอง)
- filter `&sellerId=` **ใช้ไม่ได้** (Lazada เมิน)
- **ไม่มีเลขเป๊ะ**: `itemSoldCnt` = null เสมอ มีแต่ `itemSoldCntShow` — ต่ำกว่าพัน = เป๊ะ ("546 sold"),
  **เกินพันถูกปัดเป็น K** ("3.3K sold" = 3300) → diff รายวันของสินค้าเกินพันจะหยาบ (เด้งทีละ ~100)
- สินค้าที่ Lazada ไม่โชว์ยอด (ขายน้อย/ใหม่) → `itemSoldCntShow` = null เว้นว่าง + note
- enrichment นี้ **ห้ามทำ record หลักพัง** — ห่อ try/except ที่ call site แล้ว
- `run_daily.ps1` เปิด `--lazada-sold` อัตโนมัติเมื่อทำ Lazada (หรือทุกแพลตฟอร์ม)

## ข้ามลิงก์ตาย (`build_deadlist.py` + `--skip-dead`)

ลิงก์ที่เปิดไม่ได้ = **ข้ามเลย ไม่เสียเวลาเปิด** → record `source=blocked` (เป็น Null + เหตุผล)
- อัตโนมัติเสมอ: ลิงก์ `/search` / ไม่มี URL — scrape_pdp ข้ามให้ทันที
- ลิงก์ตายถาวร (สินค้าถูกลบ/404): รวบรวมด้วย `build_deadlist.py` → `data/dead_links.txt`
  scrape_pdp อ่านไฟล์นี้ (`--skip-dead`, default เปิดอยู่) แล้วข้าม **ไม่รวม CAPTCHA** (อันนั้นลองใหม่ได้)

```powershell
python build_deadlist.py --raw "data/raw_*.ndjson"   # อัปเดต deadlist หลัง scrape (รันเรื่อย ๆ ให้โต)
```

## เก็บซ้ำเฉพาะตัวที่พลาด (`redo.py`)

ไม่ต้องรันใหม่ทั้งหมด — `redo.py` กรองเฉพาะ record ที่ยังไม่สมบูรณ์แล้ว merge กลับ

```powershell
python redo.py pick  data\raw_YYYY-MM-DD.ndjson    # หาตัวพลาด -> redo_*.txt + สรุปเหตุผล + คำสั่งถัดไป
# (รัน scrape ตามคำสั่งที่ pick พิมพ์ให้ — ใช้ --cdp --lazada-sold)
python redo.py merge data\raw_YYYY-MM-DD.ndjson data\redo_YYYY-MM-DD.ndjson   # รวม, เก็บอันที่ดีกว่า
```

"ตัวพลาด" = error / blocked / source=dom / ไม่มีชื่อ / (tiktok,shopee) ไม่มี sold /
lazada ที่เก็บก่อนมีฟีเจอร์ sold ส่วนลิงก์ `/search` แจ้งให้ลบจาก urls.txt (เก็บซ้ำไปก็ไม่ได้)
merge เลือกด้วย `score()` (state/api + มี sold ชนะ blocked/error) สำรอง raw เดิมเป็น `.bak`

## หัวข้อ spec กลาง + category (spec_map.csv / category_map.csv)

spec ของ 3 แพลตฟอร์มความหมายตรงกันแต่ชื่อต่างกัน (Brand=แบรนด์, Drill Type=ประเภทสว่าน=ประเภทของสว่าน)
รวมเป็นหัวข้อกลางด้วยตาราง map ที่**แก้เองได้** ไม่ต้องแตะโค้ด

```powershell
# สร้าง/รีเฟรช map (รันเมื่อมี spec ชื่อใหม่ หรือ category เปลี่ยน)
python build_maps.py --raw "data/raw_*.ndjson" --ids "…/MarketSize-Share (1)_ids.xlsx"
# normalize อ่าน spec_map.csv + category_map.csv อัตโนมัติ
python normalize_pdp.py --inputs data\raw_YYYY-MM-DD.ndjson --out output\...xlsx
```

- `spec_map.csv` = `raw_spec_name -> canonical` — ชื่อ spec ใหม่ที่ยังไม่ map จะ `canonical` ว่าง **เติมเองได้**
  (หัวข้อกลางนิยามใน `CANON` ของ build_maps.py) ตัวที่ยังว่าง = ไม่ขึ้นใน sheet spec_matrix แต่ยังอยู่ใน sheet specs (ชื่อดิบ)
- `category_map.csv` = `product_id -> category` จากไฟล์ MarketSize-Share (`_ids.xlsx`)
- Excel เพิ่ม sheet **`spec_matrix`** = 1 แถว/สินค้า, คอลัมน์ = หัวข้อกลาง (ข้าม 3 แพลตฟอร์มมารวมคอลัมน์เดียว) + คอลัมน์ `category` ใน sheet products

## กฎเหล็ก

1. **ห้ามสร้างตัวเลขขึ้นเอง** ทุกค่าต้องมาจากหน้าเว็บจริง ขาดอะไร = ใส่ `"Null"` (ไม่ปล่อยช่องว่าง)
   + เขียนเหตุผลลง `notes` — `fill_null()` ใน normalize เติม Null ให้ทุก sheet ตอนเขียน Excel
   (ทำบนสำเนา ไม่กระทบ `sold_history.csv` ที่ใช้คำนวณ diff)
2. **ห้ามเขียน logic แกะข้อมูลใหม่ในไฟล์อื่น** — แก้ที่ `extract_pdp.js` ที่เดียว เพื่อให้ Claude in Chrome กับ Playwright ใช้ตัวเดียวกัน
3. **`--delay` ห้ามต่ำกว่า 3 วินาที** ยิงรัวโดนบล็อก IP/บัญชี
4. **ห้าม commit** `.browser_profile/` (มี cookie ล็อกอิน), `.venv/`, `data/`
5. ID สินค้าเป็น **string เสมอ** — TikTok product_id ยาว 19 หลัก ถ้าเผลอเป็น int จะเพี้ยน
6. ตอบเป็นภาษาไทย กระชับ เน้นตัวเลขและสิ่งที่ต้อง action

## Debug

ทุก record มี field `source`:

| ค่า | ความหมาย | ต้องทำอะไร |
|---|---|---|
| `state` / `api` | อ่านจาก data ต้นทาง | ปกติ เชื่อได้ |
| `dom` / `jsonld` | fallback — state ของเว็บเปลี่ยนแล้ว | **แก้ selector ใน extract_pdp.js ก่อนใช้ตัวเลข** |
| `blocked` | โดน CAPTCHA/anti-bot หรือ extractor throw — ทุกค่าว่างโดยตั้งใจ | อ่านเหตุผลใน `notes` แล้วรัน `--login` ผ่าน CAPTCHA เอง |

ทดสอบ logic ของ JS โดยไม่ต้องเปิดเว็บจริง: stub `window`/`document`/`location` แล้ว `eval` ไฟล์ด้วย node
(ดูตัวอย่างใน README ส่วน "ทดสอบ")

เจอสินค้าที่ `source=dom` ให้เปิดหน้านั้นในเบราว์เซอร์ พิมพ์ใน DevTools console:
`Object.keys(window).filter(k => k.startsWith('__'))` เพื่อหาชื่อ state ตัวใหม่

## จุดที่พังบ่อย

1. **TikTok** — schema เปลี่ยนบ่อยสุด ใช้ `deepFind` หา node แทน hardcode path
   - state อยู่ที่ `window._ROUTER_DATA` (สำรอง: `<script id="__MODERN_ROUTER_DATA__" type="application/json">`)
   - `/view/product/<id>` ถูก **redirect** ไป `shop.tiktok.com/th/pdp/<slug>/<id>` — regex product_id ต้องรองรับทั้งคู่
   - **ราคาไม่ได้อยู่กับตัวสินค้า**: `product_info.product_model` = ชื่อ/skus/sale_properties,
     `product_info.promotion_model.promotion_product_price.skus_price[sku_id]` = `sale_price_decimal` / `origin_price_decimal`
   - `price` กับ `original_price` ต้องมาจาก **sku ตัวเดียวกัน** (ตัวถูกสุด) ไม่งั้น discount เพี้ยน
   - หน้านี้**ไม่มี `data-e2e`** แล้ว selector เก่าตายหมด — DOM fallback ได้แค่ `h1`
   - `deepFind` ห้ามเดินเข้า DOM/CSSOM: อ่าน `cssRules` ของ stylesheet ข้าม origin จะ throw SecurityError
2. **Shopee** — `/api/v4/pdp/get_pc` ต้องมี cookie สด ถ้า fail ให้รัน `--login` ใหม่
   - **`get_pc` ไม่มียอดขาย** (มีแค่ `display_similar_sold: null`) — ยอดขายจริงอยู่ใน **`/api/v4/item/get`**
     เท่านั้น: `historical_sold` (สะสม เลขเป๊ะ), `sold` (เดือนล่าสุด), `global_sold` — extractor เรียกเสริมให้แล้ว
   - Shopee ให้เลขเป๊ะทุกช่วง (ต่างจาก Lazada ที่เกินพันปัดเป็น K)
   - ลิงก์ต้องเป็นหน้าสินค้า (`/product/<shopid>/<itemid>` หรือ slug `...-i.<shopid>.<itemid>`) — **ลิงก์ `/search?keyword=` ใช้ไม่ได้**
3. **Lazada** — `window.__moduleData__.data.root.fields` (ไม่โดน anti-bot แต่ schema ย้ายของเงียบ ๆ)
   - `skuBase` ย้ายไปอยู่ **`fields.productOption.skuBase`** และคีย์เปลี่ยนเป็น **`properties`** (เดิม `props`)
   - `propPath` อยู่ใน `skuBase.skus` ส่วน**ราคาอยู่ใน `fields.skuInfos`** ต้อง join ด้วย `skuId`
   - `skuInfos` มีคีย์ `"0"` เป็นค่า default ของหน้า **ไม่ใช่ sku จริง** ต้องข้าม ไม่งั้นนับ sku เกิน
   - `skuInfos[id].quantity` = **ลิมิตการสั่งซื้อ** (`{limit:{max:50}}`) *ไม่ใช่* สต็อก — state ไม่มีสต็อกจริง เว้นว่าง
   - `fields.specifications` = `{ <skuId>: { features: {ชื่อ: ค่า} } }` ค่าจริงอยู่ใน `.features` ชั้นใน
   - ถ้าอ่าน state ได้แต่ `variation`/`spec` ว่าง = schema ย้ายอีกแล้ว **ไม่ใช่แค่ selector** ให้ probe `fields` ก่อน
   - สินค้าถูกลบ → หน้า title "Sorry! This product is no longer available" ไม่มี state
     extractor จับได้แล้ว คืน `source=blocked` + note "สินค้าถูกลบ" (ปกติสำหรับลิสต์เก่า ไม่ใช่บั๊ก)
4. **ไฟล์ `.ps1` ต้องเซฟเป็น UTF-8 *with BOM*** — Windows PowerShell 5.1 อ่านไฟล์ที่ไม่มี BOM ด้วยโค้ดเพจ 874
   ภาษาไทยจะเพี้ยนเป็น `เธตเธขเธง` แล้ว parser พังทั้งไฟล์ (pwsh 7 ไม่เป็น จะไม่เห็นปัญหาถ้าเทสต์แค่ 7)
   ตรวจ: `[System.IO.File]::ReadAllBytes('run_daily.ps1')[0..2]` ต้องได้ `239 187 191`
