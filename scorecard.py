r"""Listing Scorecard (Lazada) — ใส่ลิงก์สินค้า หรือชื่อ/ลิงก์ร้าน แล้วได้คะแนนตามเกณฑ์ OSUKA

    python scorecard.py --url https://www.lazada.co.th/products/pdp-i4922038314.html
    python scorecard.py --shop "TNLTOOLSTORE" --max 10
    python scorecard.py --shop https://www.lazada.co.th/shop/tby-toolscenter/ --request-id r123

⚠️ ต้องเปิด Chrome แบบ CDP ค้างไว้ก่อน (Lazada ขึ้น CAPTCHA กับเบราว์เซอร์อัตโนมัติ):
    & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$PWD\.chrome_cdp"
   เจอ CAPTCHA = หยุด รายงาน ให้คนกดผ่านในหน้าต่างนั้นเอง (ห้ามพยายามผ่านเอง — กฎเหล็ก)
   สคริปต์เปิดแท็บของตัวเองและปิดเฉพาะแท็บนั้น ไม่แตะแท็บผู้ใช้

ร้าน: ตรวจเร็วทุกหน้าสินค้า OSUKA ในร้าน (ชื่อครบ/ราคาเทียบ price list จากการ์ด)
      + ตรวจลึกหน้าสินค้าที่ขายดีสุด --max หน้า (เปิดหน้าจริง ใช้ extract_pdp.js ตัวเดียวกับ scrape_pdp)

ผลลัพธ์: <out>/<request-id>/ ไฟล์ละเอกสาร + manifest.json ({collection, doc_id, file})
         สำหรับเขียนเข้า db ของหน้า Scorecard (Claude เขียนด้วย ArtifactData batch)
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

import scorecard_rules as R

HERE = Path(__file__).resolve().parent
JS = (HERE / "extract_pdp.js").read_text(encoding="utf-8")
BASE = "https://www.lazada.co.th"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


class Blocked(Exception):
    """Lazada ตอบหน้า anti-bot/CAPTCHA แล้วไม่มีคนกดผ่านภายในเวลารอ — หยุดทั้งงาน"""


# นาทีที่รอให้คนกดผ่าน CAPTCHA ในหน้าต่าง Chrome ก่อนยอมแพ้ (ตั้งจาก --captcha-wait · 0 = หยุดทันที)
CAPTCHA_WAIT = 0.0


async def wait_for_human(page, why, still_blocked):
    """ขึ้น CAPTCHA — ⛔ ไม่พยายามผ่านเอง ดึงแท็บขึ้นหน้าสุดแล้วรอคนกดในหน้าต่าง Chrome

    ของเดิมหยุดทั้งรอบทันที ต้องสั่งใหม่ทุกครั้ง (TNL 186 หน้าโดน 3 รอบ) เจ้าของงานเลือกให้
    ตรวจรวดเดียวแล้วกดผ่านเองเมื่อขึ้น (2026-09-26) — ตรวจทุก 15 วิว่าผ่านแล้วหรือยัง แล้วทำต่อ
    """
    if CAPTCHA_WAIT <= 0:
        raise Blocked(why)
    print(f"  ⏸ CAPTCHA — รอคนกดผ่านในหน้าต่าง Chrome (สูงสุด {CAPTCHA_WAIT:g} นาที)", flush=True)
    try:
        await page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass
    deadline = time.time() + CAPTCHA_WAIT * 60
    while time.time() < deadline:
        await page.wait_for_timeout(15000)
        try:
            if not await still_blocked():
                print("  ▶ ผ่าน CAPTCHA แล้ว ทำต่อ", flush=True)
                return
        except Exception:  # noqa: BLE001   หน้ากำลังเปลี่ยน (คนกดแล้ว redirect) — รอบหน้าเช็คใหม่
            pass
    raise Blocked(f"{why} (รอ {CAPTCHA_WAIT:g} นาทีแล้วยังไม่มีคนกดผ่าน)")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- รูป

def img_uri(url, size):
    """ขอรูปย่อจาก CDN (ไม่โหลดไฟล์เต็ม) แล้วแปลงเป็น data URI · คืน (uri, ความขาวของขอบ)"""
    from PIL import Image
    u = "https:" + url if url.startswith("//") else url
    cands = ([u + f"_{size}x{size}q75.webp"] if re.search(r"slatic\.net|alicdn\.com", u) else []) + [u]
    raw = None
    for c in cands:
        try:
            raw = urllib.request.urlopen(urllib.request.Request(c, headers={"User-Agent": UA, "Referer": BASE + "/"}), timeout=30).read()
            break
        except Exception:  # noqa: BLE001
            continue
    if raw is None:
        return None, None
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = im.size
    px = im.load()
    edge = [px[x, y] for x in range(0, w, max(1, w // 60)) for y in (0, h - 1)] + \
           [px[x, y] for y in range(0, h, max(1, h // 60)) for x in (0, w - 1)]
    white = sum(1 for r, g, b in edge if r > 235 and g > 235 and b > 235) / max(len(edge), 1)
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=70)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode(), round(white, 2)


# ---------------------------------------------------------------- Lazada

AJAX_JS = """async (p) => {
    const r = await fetch(p, {credentials: 'include'});
    const t = await r.text();
    try { return {ok: true, j: JSON.parse(t)}; } catch (e) { return {ok: false, head: t.slice(0, 300)}; }
}"""
PUNISH = re.compile(r"punish|captcha|x5sec|baxia", re.I)


async def ajax(page, path):
    """GET JSON แบบ same-origin จากแท็บ Lazada — ได้หน้า punish = รอคนกดผ่าน CAPTCHA"""
    res = await page.evaluate(AJAX_JS, path)
    if not res["ok"] and PUNISH.search(res["head"]):
        # fetch เบื้องหลังคนมองไม่เห็น CAPTCHA — เปิด URL เดียวกันในแท็บให้หน้ายืนยันขึ้นมาให้กด
        await page.goto(BASE + path, wait_until="domcontentloaded", timeout=60000)

        async def still():
            r = await page.evaluate(AJAX_JS, path)
            return not r["ok"] and bool(PUNISH.search(r["head"]))
        await wait_for_human(page, "Lazada ขึ้นหน้ายืนยันตัวตน (CAPTCHA) — กดผ่านในหน้าต่าง Chrome ที่เปิดไว้ แล้วสั่งใหม่", still)
        res = await page.evaluate(AJAX_JS, path)
    if not res["ok"]:
        if PUNISH.search(res["head"]):
            raise Blocked("Lazada ขึ้นหน้ายืนยันตัวตน (CAPTCHA) — กดผ่านในหน้าต่าง Chrome ที่เปิดไว้ แล้วสั่งใหม่")
        return None
    return res["j"]


def abs_url(u):
    """itemUrl ของ Lazada มีทั้ง /products/... และ //www.lazada.co.th/products/... (เจอจริงร้าน captain-akesteel)"""
    u = str(u or "")
    return "https:" + u if u.startswith("//") else BASE + u if u.startswith("/") else u


NET_ERR = ("ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "ERR_CONNECTION_TIMED_OUT",
           "ERR_NETWORK_CHANGED", "ERR_CONNECTION_RESET", "Failed to fetch")


class NetworkDown(Exception):
    """เน็ตหลุดติดกันหลายหน้า — หยุดทั้งรอบ ไม่นับเป็นหน้าที่ล้ม (2026-09-25 เน็ตหลุด ~13:00 ทำให้ 302 หน้าล้มหมด)"""


def items_of(j):
    return ((j or {}).get("mods") or {}).get("listItems") or []


def pdp_captcha(rec):
    return rec.get("source") == "blocked" and any("CAPTCHA" in w or "ยืนยัน" in w for w in rec.get("warnings") or [])


async def open_pdp(page, url, delay):
    why = "หน้าสินค้าขึ้น CAPTCHA — กดผ่านในหน้าต่าง Chrome ที่เปิดไว้ แล้วสั่งใหม่"
    for attempt in range(2):
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        rec = await page.evaluate(JS)
        await page.wait_for_timeout(int(delay * 1000))
        if not pdp_captcha(rec):
            return rec
        if attempt == 0:          # รอคนกด แล้วเปิดหน้าสินค้าใหม่อีกครั้ง
            await wait_for_human(page, why, lambda: _pdp_still(page))
    raise Blocked(why)


async def _pdp_still(page):
    return pdp_captcha(await page.evaluate(JS))


async def search_rank(page, query, pid, delay, qcache=None):
    """ค้น query บน Lazada แล้วหาอันดับของสินค้านี้ในหน้าแรก (40 การ์ด)

    qcache: ตรวจทั้งร้าน สินค้าหลายหน้าเป็นรุ่นเดียวกัน (ต่างแค่ชุด/ตัวเลือก) ค้นคำเดียวกันซ้ำ
    เก็บผลค้นไว้ใช้ซ้ำในรอบเดียวกัน — ยิงคำขอน้อยลง เร็วขึ้น และเสี่ยงโดน CAPTCHA น้อยลง
    """
    if qcache is not None and query in qcache:
        items = qcache[query]
    else:
        j = await ajax(page, f"/catalog/?ajax=true&page=1&q={urllib.parse.quote(query)}")
        await page.wait_for_timeout(int(delay * 1000))
        items = items_of(j)
        if qcache is not None:
            qcache[query] = items
    pos = next((i + 1 for i, it in enumerate(items) if str(it.get("nid") or it.get("itemId")) == str(pid)), None)
    return items, pos


def norm(s):
    return re.sub(r"[^a-z0-9ก-๙]", "", (s or "").lower())


async def resolve_shop(page, text, delay):
    """ชื่อร้าน หรือ ลิงก์ร้าน -> (slug, ชื่อร้าน)"""
    m = re.search(r"lazada\.co\.th/(?:shop/)?([^/?#]+)", text)
    if m and m.group(1) not in ("products", "catalog"):
        slug = m.group(1)
        j = await ajax(page, f"/{slug}/?ajax=true&from=wangpu&langFlag=th&page=1&q=osuka")
        it = items_of(j)
        return slug, (it[0].get("sellerName") if it else slug)
    guess = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if guess:
        j = await ajax(page, f"/{guess}/?ajax=true&from=wangpu&langFlag=th&page=1&q=osuka")
        it = items_of(j)
        await page.wait_for_timeout(int(delay * 1000))
        if it and norm(it[0].get("sellerName")) == norm(text):
            return guess, it[0].get("sellerName")
    # สำรอง: ค้น "osuka <ชื่อร้าน>" หาการ์ดของร้านนี้ แล้วอ่านลิงก์ร้านจากหน้าสินค้า
    for q in (f"osuka {text}", text):
        for pg in (1, 2):
            items = items_of(await ajax(page, f"/catalog/?ajax=true&page={pg}&q={urllib.parse.quote(q)}"))
            await page.wait_for_timeout(int(delay * 1000))
            hit = next((x for x in items if norm(x.get("sellerName")) == norm(text)), None)
            if hit:
                url = abs_url(hit.get("itemUrl"))
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                for _ in range(20):
                    su = await page.evaluate("() => { try { const s = window.__moduleData__.data.root.fields.seller; return s && s.url; } catch (e) { return null; } }")
                    if su:
                        break
                    await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight / 2)")
                    await page.wait_for_timeout(500)
                m2 = re.search(r"/shop/([^/?#]+)", su or "")
                if m2:
                    return m2.group(1), hit.get("sellerName")
    return None, None


# ---------------------------------------------------------------- ประกอบผล

def result_doc(rec, card, pos, med, query, cover_white, sc, req_id):
    L1 = R.score_card(card, pos, med, cover_white, query) if card else None
    tot = R.total(L1, sc["L2"], sc["L3"], sc["L4"])
    return {
        "platform": "lazada", "id": str(rec.get("product_id")), "url": rec.get("url"), "request_id": req_id,
        "title": rec.get("product_name"), "seller": rec.get("shop_name"), "shop_id": rec.get("shop_id"),
        "price": rec.get("price"), "orig": rec.get("original_price"), "sold": (card or {}).get("itemSoldCntShow"),
        "rating": rec.get("rating"), "review_count": rec.get("review_count"), "rating_breakdown": rec.get("rating_breakdown"),
        "video_count": rec.get("video_count"), "image_count": rec.get("image_count"),
        "spec": [[s.get("name"), str(s.get("value") or "")] for s in rec.get("spec") or []],
        "variation": [[v.get("name"), v.get("options") or []] for v in rec.get("variation") or []],
        "skus": sc["skus"], "below": sc["below"], "portal_name": sc["portal_name"],
        "description": (rec.get("description") or "")[:30000],   # ภาษาไทย 3 ไบต์/ตัว — กันเอกสารเกิน 256 KiB
        "L1": L1, "L2": sc["L2"], "L3": sc["L3"], "L4": sc["L4"], "total": tot,
        "search": {"query": query, "pos": pos, "of": med["n"] if med else 0},
        "warnings": rec.get("warnings") or [], "scraped_at": now(),
    }


async def deep_one(page, url, ref, req_id, delay, out, stamp, shop_id=None, with_images=True, qcache=None):
    rec = await open_pdp(page, url, delay)
    if not rec.get("product_name"):
        raise RuntimeError("เปิดหน้าสินค้าแล้วอ่านข้อมูลไม่ได้: " + "; ".join(rec.get("warnings") or [])[:200])
    pid = str(rec.get("product_id"))
    tm = R.find_models(rec.get("product_name"), ref)
    toks = [tok for tok, _, ok in tm if ok] or [tok for tok, _, _ in tm]
    m0 = re.match(r"[A-Z]{2,6}(?:-?[A-Z]{1,3})?-?\d{2,4}", toks[0]) if toks else None
    query = m0.group(0) if m0 else " ".join((rec.get("product_name") or "").split()[:3])   # ค้นด้วยรหัสฐาน เช่น OCHD802
    items, pos = await search_rank(page, query, pid, delay, qcache)
    med = R.page_medians(items) if items else {"disc": 0, "review": 0, "sold": 0, "n": 0}
    card = next((it for it in items if str(it.get("nid") or it.get("itemId")) == pid), None)
    if card is None:          # ไม่ติดหน้าแรก — ใช้ข้อมูลหน้าสินค้าแทนการ์ด (ข้อที่ไม่มีข้อมูลจะถูกข้าม)
        card = {"name": rec.get("product_name"), "brandName": "OSUKA" if "osuka" in (rec.get("product_name") or "").lower() else "",
                "discount": f"{round((1 - rec['price'] / rec['original_price']) * 100)}%" if rec.get("price") and rec.get("original_price") else "",
                "review": str(rec.get("review_count") or ""), "ratingScore": rec.get("rating"), "icons": [],
                "image": (rec.get("images") or [None])[0]}
    cover, white = (img_uri(card.get("image"), 260) if card.get("image") else (None, None))
    sc = R.score_pdp(rec, ref)
    doc = result_doc(rec, card, pos, med, query, white, sc, req_id)
    rid = f"lzd-{pid}-{stamp}"        # 1 รอบตรวจ = 1 เอกสาร (ตรวจซ้ำไม่ทับของเดิม เก็บเป็นประวัติ)
    files = [write(out, "results", rid, doc)]
    # รูปรวมเป็นก้อน (≤ 220 KB/เอกสาร) แทน 1 รูป = 1 เอกสาร — db ทั้งหน้าจุ 5,000 เอกสาร
    # ตรวจทั้งร้าน 100+ หน้า ถ้าแยกรูปละเอกสารจะใช้ ~20 เอกสาร/หน้า เต็มในไม่กี่ร้าน (รูปปกอยู่ในการ์ดแล้ว)
    # ตรวจทั้งร้าน: คะแนนนับจากหน้าจริงครบทุกหน้า แต่เก็บรูปแกลเลอรี/คำอธิบายเฉพาะหน้าที่ขายดี (with_images)
    gal = [d for d, _ in (img_uri(u, 480) for u in list(dict.fromkeys(rec.get("images") or []))[:12]) if d] if with_images else []
    dsc = [d for d, _ in (img_uri(u, 640) for u in (rec.get("description_images") or [])[:10]) if d] if with_images else []
    gal = [d for d in gal if len(d) < 240_000]
    dsc = [d for d in dsc if len(d) < 240_000]

    def packs(prefix, items):
        docs, cur, size = [], [], 0
        for d in items:
            if cur and size + len(d) > 220_000:
                docs.append((f"{prefix}{len(docs)}", cur))
                cur, size = [], 0
            cur.append(d)
            size += len(d)
        if cur:
            docs.append((f"{prefix}{len(docs)}", cur))
        return docs

    for name, items in packs("g", gal) + packs("d", dsc):
        files.append(write(out, f"results/{rid}/imgs", name, {"items": items}))
    doc["n_gallery"] = len(gal)
    doc["n_desc_imgs"] = len(dsc)
    doc["images_stored"] = with_images
    doc["gallery_count"] = len(dict.fromkeys(rec.get("images") or []))   # จำนวนรูปจริงบนหน้า (ใช้แสดงแม้ไม่ได้เก็บรูป)
    doc["has_cover"] = bool(cover)
    write(out, "results", rid, doc)            # เขียนทับพร้อมจำนวนรูป
    # การ์ดย่อสำหรับหน้า Marketplace ของเว็บ (เล็ก โหลดทีละหลายใบได้ — ผลเต็มอยู่ใน results/<rid>)
    fails = [c for L in ("L1", "L2", "L3", "L4") if doc.get(L) for c in doc[L]["checks"]
             if c.get("ok") is False or (c.get("ok") and c.get("ratio") is not None and c["ratio"] < 1)]
    files.append(write(out, "cards", rid, {
        "rid": rid, "pid": pid, "url": doc["url"], "title": doc["title"], "seller": doc["seller"],
        "price": doc["price"], "orig": doc["orig"], "rating": doc["rating"], "review_count": doc["review_count"],
        "sold": doc["sold"], "total": doc["total"], "below": doc["below"],
        "layers": {L: (doc[L] or {}).get("score") for L in ("L1", "L2", "L3", "L4")},
        "fix_main": sum(1 for c in fails if c.get("main")), "fix_all": len(fails),
        "search_pos": pos, "search_query": query, "cover": cover,
        "shop_id": shop_id, "request_id": req_id, "scraped_at": doc["scraped_at"]}))
    return rid, doc, files


def resumable(out, hours):
    """ผลที่รอบก่อนของคำขอเดียวกันตรวจไว้แล้ว (ภายใน hours ชม.) -> {pid: (rid, doc, files)}

    รอบตรวจทั้งร้านยาวหลายสิบนาที ล้มกลางทางได้ (CAPTCHA / เน็ตหลุด / หน้าต่าง Chrome ถูกปิด)
    ของเดิมเริ่มใหม่หมดทุกครั้ง — TNL 186 หน้า ล้ม 2 รอบติดที่ 101 และ 20 หน้า เสียงานไปเปล่า ๆ
    ไฟล์ผลเขียนลงดิสก์ทีละหน้าอยู่แล้ว รอบถัดไปจึงหยิบมาใช้ต่อแล้วตรวจเฉพาะหน้าที่ยังขาด
    """
    got, cutoff = {}, time.time() - hours * 3600
    for p in sorted(out.glob("results__lzd-*.json")):
        if "__imgs__" in p.name or p.stat().st_mtime < cutoff:
            continue
        rid = p.stem[len("results__"):]
        card = out / f"cards__{rid}.json"
        if not card.exists():                  # เขียนไม่จบ (การ์ดเขียนเป็นไฟล์สุดท้ายของหน้า)
            continue
        doc = json.loads(p.read_text(encoding="utf-8"))
        pid = rid.split("-")[1]
        files = [{"collection": "results", "doc_id": rid, "file": str(p)}]
        for ip in sorted(out.glob(f"results__{rid}__imgs__*.json")):
            files.append({"collection": f"results/{rid}/imgs", "doc_id": ip.stem.rsplit("__", 1)[1], "file": str(ip)})
        files.append({"collection": "cards", "doc_id": rid, "file": str(card)})
        if pid not in got or rid > got[pid][0]:          # ตรวจหลายรอบ เอาอันล่าสุด
            got[pid] = (rid, doc, files)
    return got


def write(out, collection, doc_id, data):
    p = out / (collection.replace("/", "__") + "__" + doc_id + ".json")
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"collection": collection, "doc_id": doc_id, "file": str(p)}


# ---------------------------------------------------------------- main

async def run(a):
    ref = R.load_ref()
    req_id = a.request_id or "cli-" + time.strftime("%Y%m%d-%H%M%S")
    out = Path(a.out) / req_id
    stamp = time.strftime("%Y%m%d%H%M")
    out.mkdir(parents=True, exist_ok=True)
    manifest, summary = [], {"request_id": req_id, "started_at": now(), "ref_error": ref["error"],
                             "master_rows": len(ref["master"]), "portal_rows": len(ref["portal"])}
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(a.cdp, timeout=15000)
        except Exception as e:  # noqa: BLE001
            summary.update(status="error", note=f"ต่อ Chrome ที่ {a.cdp} ไม่ได้ — เปิด Chrome แบบ --remote-debugging-port=9222 ก่อน ({type(e).__name__})")
            return finish(out, manifest, summary)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        # ⚠️ โปรไฟล์ที่ตั้งภาษาอังกฤษจะได้ชื่อ/คำอธิบายแปลอังกฤษ ("Ochd802-N") -> เกณฑ์ที่หาคำไทยตรวจผิดหมด
        #    บังคับภาษาไทยผ่านคุกกี้ hng ของ Lazada ทุกครั้ง (เจอจริง 2026-09-25)
        await ctx.add_cookies([{"name": "hng", "value": "TH|th|THB|764", "domain": ".lazada.co.th", "path": "/"}])
        page = await ctx.new_page()
        try:
            await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            lang = await page.evaluate("document.documentElement.lang")
            if lang and not lang.lower().startswith("th"):
                summary.update(status="error", note=f"หน้า Lazada เป็นภาษา {lang} ไม่ใช่ภาษาไทย — เกณฑ์จะตรวจผิด เปลี่ยนภาษาในหน้าต่าง Chrome เป็นไทยแล้วสั่งใหม่")
                return finish(out, manifest, summary)
            results = []
            if a.url:
                for u in a.url:
                    try:
                        rid, doc, files = await deep_one(page, u, ref, req_id, a.delay, out, stamp)
                        manifest += files
                        results.append({"id": rid, "total": doc["total"], "title": doc["title"]})
                    except Blocked:
                        raise
                    except Exception as e:  # noqa: BLE001
                        results.append({"url": u, "error": str(e)[:300]})
                summary.update(kind="url", results=results)
            else:
                slug, sname = await resolve_shop(page, a.shop, a.delay)
                if not slug:
                    summary.update(status="error", kind="shop", note=f"หาร้าน “{a.shop}” บน Lazada ไม่เจอ — ลองใส่ลิงก์หน้าร้านแทน")
                    return finish(out, manifest, summary)
                rows, pg, total_n = [], 1, None
                while pg <= a.max_pages:
                    j = await ajax(page, f"/{slug}/?ajax=true&from=wangpu&langFlag=th&page={pg}&q=osuka")
                    its = items_of(j)
                    total_n = total_n or int((j or {}).get("mainInfo", {}).get("totalResults") or 0)
                    for it in its:
                        if not re.search("osuka", (it.get("name") or "") + " " + (it.get("brandName") or ""), re.I):
                            continue
                        price = R.num(it.get("price"))
                        q = R.quick_title_check(it.get("name"), price, ref)
                        rows.append({"id": str(it.get("nid") or it.get("itemId")), "name": it.get("name"), "price": price,
                                     "orig": R.num(it.get("originalPrice")), "image": it.get("image"),
                                     "rating": R.num(it.get("ratingScore")), "review": R.num(it.get("review")),
                                     "sold": it.get("itemSoldCntShow"), "sold_n": R.sold_n(it.get("itemSoldCntShow")) or 0,
                                     "url": abs_url(it.get("itemUrl")),
                                     **{k: q[k] for k in ("brand", "type", "model", "unknown", "known", "below", "pmin")}})
                    await page.wait_for_timeout(int(a.delay * 1000))
                    if len(its) < 40 or len(rows) >= (total_n or 0):
                        break
                    pg += 1
                sid = f"lzd-{slug}-{stamp}"
                # ตรวจละเอียดทุกหน้า (ได้คะแนนเต็มครบ) · เก็บรูปเฉพาะ --max หน้าที่ขายดีสุด
                deep = sorted(rows, key=lambda r: -r["sold_n"])[:a.max_deep]
                done_before = resumable(out, a.resume_hours)
                if done_before:
                    print(f"  ทำต่อจากรอบก่อน: ตรวจไว้แล้ว {sum(1 for r in deep if r['id'] in done_before)}/{len(deep)} หน้า", flush=True)
                net_fail, qcache, n_new = 0, {}, 0
                for n_done, r in enumerate(deep):
                    if a.max_new and n_new >= a.max_new and r["id"] not in done_before:
                        # ครบโควตารอบนี้ — พักให้ Lazada ไม่ขึ้น CAPTCHA ผลที่ได้อยู่บนดิสก์ รอบหน้าทำต่อ
                        left = sum(1 for x in deep[n_done:] if x["id"] not in done_before)
                        summary.update(status="partial", kind="shop", progress_done=len(deep) - left, progress_total=len(deep),
                                       note=f"ตรวจแล้ว {len(deep) - left}/{len(deep)} หน้า — รอบละ {a.max_new} หน้ากันโดน CAPTCHA รอบถัดไปทำต่อเอง")
                        return finish(out, manifest, summary)
                    try:
                        if r["id"] in done_before:
                            rid, doc, files = done_before[r["id"]]
                            # การ์ดเดิมผูกกับ shop_id ของรอบก่อน — ชี้มารอบนี้ ไม่งั้นหน้าเว็บจัดกลุ่มผิดร้าน
                            cp = Path(files[-1]["file"])
                            c = json.loads(cp.read_text(encoding="utf-8"))
                            c["shop_id"] = sid
                            cp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
                            manifest += files
                            r["result_id"], r["total"] = rid, doc["total"]
                            results.append({"id": rid, "total": doc["total"], "title": doc["title"]})
                            continue
                        n_new += 1
                        rid, doc, files = await deep_one(page, r["url"], ref, req_id, a.delay, out, stamp, sid,
                                                         with_images=n_done < a.max, qcache=qcache)
                        print(f"  [{n_done + 1}/{len(deep)}] {doc['total']} {doc['title'][:40]}", flush=True)
                        manifest += files
                        r["result_id"], r["total"] = rid, doc["total"]
                        results.append({"id": rid, "total": doc["total"], "title": doc["title"]})
                    except Blocked:
                        raise
                    except Exception as e:  # noqa: BLE001
                        r["error"] = str(e)[:200]
                        results.append({"url": r["url"], "error": str(e)[:300]})
                        net_fail = net_fail + 1 if any(k in str(e) for k in NET_ERR) else 0
                        if net_fail >= 5:
                            raise NetworkDown(f"เน็ตหลุด — เปิดหน้าไม่ได้ติดกัน {net_fail} หน้า ({str(e).splitlines()[0][:80]}) ตรวจใหม่เมื่อเน็ตกลับมา")
                    else:
                        net_fail = 0
                # รูปการ์ดของทุกหน้าในร้าน (ย่อ 160px) รวมเป็นก้อนละ ≤ 180 KB — db จำกัด 256 KiB/เอกสาร
                chunk, size, k = {}, 0, 0
                for r in rows:
                    d, _ = img_uri(r.pop("image", None) or "", 160) if r.get("image") else (None, None)
                    if not d:
                        continue
                    if chunk and size + len(d) > 180_000:
                        manifest.append(write(out, f"shops/{sid}/thumbs", f"t{k:02d}", {"items": chunk}))
                        chunk, size, k = {}, 0, k + 1
                    chunk[r["id"]] = d
                    size += len(d)
                    r["thumb"] = f"t{k:02d}"
                if chunk:
                    manifest.append(write(out, f"shops/{sid}/thumbs", f"t{k:02d}", {"items": chunk}))
                for r in rows:
                    r.pop("image", None)
                tots = [r["total"] for r in rows if r.get("total") is not None]
                shop = {"platform": "lazada", "slug": slug, "name": sname, "url": f"{BASE}/shop/{slug}/",
                        "request_id": req_id, "osuka_listings": len(rows), "store_total_hits": total_n,
                        "deep_count": len(tots), "avg_total": round(sum(tots) / len(tots)) if tots else None,
                        "title_complete": sum(1 for r in rows if r["brand"] and r["type"] and r["model"]),
                        "unknown_model": sum(1 for r in rows if r["unknown"]), "below_price": sum(1 for r in rows if r["below"]),
                        "rows": rows[:400], "scraped_at": now()}
                manifest.append(write(out, "shops", sid, shop))
                summary.update(kind="shop", shop_id=sid, shop_name=sname, results=results)
            ok = [r for r in results if r.get("id")]
            summary.update(status="done" if ok or summary.get("kind") == "shop" else "error",
                           result_ids=[r["id"] for r in ok],
                           note="" if ok else "; ".join(r.get("error", "") for r in results)[:300])
        except (Blocked, NetworkDown) as e:
            summary.update(status="error", note=str(e))
        finally:
            await page.close()             # ปิดเฉพาะแท็บของเรา
    return finish(out, manifest, summary)


def finish(out, manifest, summary):
    summary["finished_at"] = now()
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))
    for r in summary.get("results") or []:
        print("  ", r)
    print("manifest:", out / "manifest.json", f"({len(manifest)} docs)")
    return {"done": 0, "partial": 3}.get(summary.get("status"), 1)     # 3 = ครบโควตารอบ ยังไม่จบร้าน


def main():
    sys.stdout.reconfigure(encoding="utf-8")      # log มีภาษาไทย/⏸ — ถูก redirect แล้ว cp1252 ทำตายกลางรอบ
    ap = argparse.ArgumentParser(description="Lazada Listing Scorecard")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", nargs="+", help="ลิงก์หน้าสินค้า Lazada (หลายลิงก์ได้)")
    g.add_argument("--shop", help="ชื่อร้าน หรือ ลิงก์หน้าร้าน Lazada")
    ap.add_argument("--max", type=int, default=10, help="จำนวนหน้าที่เก็บรูปครบ (ขายดีสุดก่อน)")
    ap.add_argument("--max-deep", type=int, default=400, help="จำนวนหน้าที่ตรวจละเอียด/ให้คะแนนเต็มในร้าน (ค่าเริ่มต้น = ทุกหน้า)")
    ap.add_argument("--max-pages", type=int, default=8, help="หน้ารายการในร้านสูงสุด (40 ชิ้น/หน้า)")
    ap.add_argument("--cdp", default="http://localhost:9222")
    ap.add_argument("--delay", type=float, default=4.0)
    ap.add_argument("--captcha-wait", type=float, default=20,
                    help="ขึ้น CAPTCHA แล้วรอคนกดผ่านในหน้าต่าง Chrome กี่นาที ก่อนหยุด (0 = หยุดทันที)")
    ap.add_argument("--max-new", type=int, default=0,
                    help="ตรวจทั้งร้าน: เปิดหน้าสินค้าใหม่ได้รอบละกี่หน้า แล้วหยุดเป็น partial ให้รอบหน้าทำต่อ "
                         "(Lazada ขึ้น CAPTCHA หลังเปิดราว 30–100 หน้าติดกัน) — 0 = ไม่จำกัด")
    ap.add_argument("--resume-hours", type=float, default=96,
                    help="ใช้ผลที่รอบก่อนของคำขอเดียวกันตรวจไว้แล้ว ถ้าไม่เก่ากว่านี้ (ชม.) — 0 = ตรวจใหม่หมด")
    ap.add_argument("--request-id")
    ap.add_argument("--out", default=str(HERE / "output" / "scorecard"))
    a = ap.parse_args()
    if a.delay < 3:
        ap.error("--delay ห้ามต่ำกว่า 3 วินาที (กฎเหล็กข้อ 3)")
    global CAPTCHA_WAIT
    CAPTCHA_WAIT = a.captcha_wait
    sys.exit(asyncio.run(run(a)))


if __name__ == "__main__":
    main()
