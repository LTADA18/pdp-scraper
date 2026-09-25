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
    """Lazada ตอบหน้า anti-bot/CAPTCHA — หยุดทั้งงาน ให้คนผ่านเอง"""


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

async def ajax(page, path):
    """GET JSON แบบ same-origin จากแท็บ Lazada — ได้ HTML/punish = Blocked"""
    res = await page.evaluate("""async (p) => {
        const r = await fetch(p, {credentials: 'include'});
        const t = await r.text();
        try { return {ok: true, j: JSON.parse(t)}; } catch (e) { return {ok: false, head: t.slice(0, 300)}; }
    }""", path)
    if not res["ok"]:
        if re.search(r"punish|captcha|x5sec|baxia", res["head"], re.I):
            raise Blocked("Lazada ขึ้นหน้ายืนยันตัวตน (CAPTCHA) — กดผ่านในหน้าต่าง Chrome ที่เปิดไว้ แล้วสั่งใหม่")
        return None
    return res["j"]


def items_of(j):
    return ((j or {}).get("mods") or {}).get("listItems") or []


async def open_pdp(page, url, delay):
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    rec = await page.evaluate(JS)
    await page.wait_for_timeout(int(delay * 1000))
    if rec.get("source") == "blocked" and any("CAPTCHA" in w or "ยืนยัน" in w for w in rec.get("warnings") or []):
        raise Blocked("หน้าสินค้าขึ้น CAPTCHA — กดผ่านในหน้าต่าง Chrome ที่เปิดไว้ แล้วสั่งใหม่")
    return rec


async def search_rank(page, query, pid, delay):
    """ค้น query บน Lazada แล้วหาอันดับของสินค้านี้ในหน้าแรก (40 การ์ด)"""
    j = await ajax(page, f"/catalog/?ajax=true&page=1&q={urllib.parse.quote(query)}")
    await page.wait_for_timeout(int(delay * 1000))
    items = items_of(j)
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
                url = BASE + hit["itemUrl"] if str(hit.get("itemUrl", "")).startswith("/") else hit.get("itemUrl")
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


async def deep_one(page, url, ref, req_id, delay, out, stamp):
    rec = await open_pdp(page, url, delay)
    if not rec.get("product_name"):
        raise RuntimeError("เปิดหน้าสินค้าแล้วอ่านข้อมูลไม่ได้: " + "; ".join(rec.get("warnings") or [])[:200])
    pid = str(rec.get("product_id"))
    tm = R.find_models(rec.get("product_name"), ref)
    toks = [tok for tok, _, ok in tm if ok] or [tok for tok, _, _ in tm]
    m0 = re.match(r"[A-Z]{2,6}(?:-?[A-Z]{1,3})?-?\d{2,4}", toks[0]) if toks else None
    query = m0.group(0) if m0 else " ".join((rec.get("product_name") or "").split()[:3])   # ค้นด้วยรหัสฐาน เช่น OCHD802
    items, pos = await search_rank(page, query, pid, delay)
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
    imgs = []
    if cover:
        imgs.append(("cover", cover))
    for k, u in enumerate(list(dict.fromkeys(rec.get("images") or []))[:12]):
        d, _ = img_uri(u, 480)
        if d:
            imgs.append((f"g{k:02d}", d))
    for k, u in enumerate((rec.get("description_images") or [])[:10]):
        d, _ = img_uri(u, 720)
        if d:
            imgs.append((f"d{k:02d}", d))
    for name, d in imgs:
        files.append(write(out, f"results/{rid}/imgs", name, {"src": d}))
    doc["n_gallery"] = sum(1 for n, _ in imgs if n.startswith("g"))
    doc["n_desc_imgs"] = sum(1 for n, _ in imgs if n.startswith("d"))
    doc["has_cover"] = bool(cover)
    write(out, "results", rid, doc)            # เขียนทับพร้อมจำนวนรูป
    return rid, doc, files


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
                                     "rating": R.num(it.get("ratingScore")), "review": R.num(it.get("review")),
                                     "sold": it.get("itemSoldCntShow"), "sold_n": R.sold_n(it.get("itemSoldCntShow")) or 0,
                                     "url": BASE + it["itemUrl"] if str(it.get("itemUrl", "")).startswith("/") else it.get("itemUrl"),
                                     **{k: q[k] for k in ("brand", "type", "model", "unknown", "known", "below", "pmin")}})
                    await page.wait_for_timeout(int(a.delay * 1000))
                    if len(its) < 40 or len(rows) >= (total_n or 0):
                        break
                    pg += 1
                deep = sorted(rows, key=lambda r: -r["sold_n"])[:a.max]
                for r in deep:
                    try:
                        rid, doc, files = await deep_one(page, r["url"], ref, req_id, a.delay, out, stamp)
                        manifest += files
                        r["result_id"], r["total"] = rid, doc["total"]
                        results.append({"id": rid, "total": doc["total"], "title": doc["title"]})
                    except Blocked:
                        raise
                    except Exception as e:  # noqa: BLE001
                        r["error"] = str(e)[:200]
                        results.append({"url": r["url"], "error": str(e)[:300]})
                tots = [r["total"] for r in rows if r.get("total") is not None]
                shop = {"platform": "lazada", "slug": slug, "name": sname, "url": f"{BASE}/shop/{slug}/",
                        "request_id": req_id, "osuka_listings": len(rows), "store_total_hits": total_n,
                        "deep_count": len(tots), "avg_total": round(sum(tots) / len(tots)) if tots else None,
                        "title_complete": sum(1 for r in rows if r["brand"] and r["type"] and r["model"]),
                        "unknown_model": sum(1 for r in rows if r["unknown"]), "below_price": sum(1 for r in rows if r["below"]),
                        "rows": rows[:400], "scraped_at": now()}
                manifest.append(write(out, "shops", f"lzd-{slug}-{stamp}", shop))
                summary.update(kind="shop", shop_id=f"lzd-{slug}-{stamp}", shop_name=sname, results=results)
            ok = [r for r in results if r.get("id")]
            summary.update(status="done" if ok or summary.get("kind") == "shop" else "error",
                           result_ids=[r["id"] for r in ok],
                           note="" if ok else "; ".join(r.get("error", "") for r in results)[:300])
        except Blocked as e:
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
    return 0 if summary.get("status") == "done" else 1


def main():
    ap = argparse.ArgumentParser(description="Lazada Listing Scorecard")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", nargs="+", help="ลิงก์หน้าสินค้า Lazada (หลายลิงก์ได้)")
    g.add_argument("--shop", help="ชื่อร้าน หรือ ลิงก์หน้าร้าน Lazada")
    ap.add_argument("--max", type=int, default=10, help="จำนวนหน้าสินค้าที่ตรวจลึกในร้าน (เลือกจากยอดขาย)")
    ap.add_argument("--max-pages", type=int, default=8, help="หน้ารายการในร้านสูงสุด (40 ชิ้น/หน้า)")
    ap.add_argument("--cdp", default="http://localhost:9222")
    ap.add_argument("--delay", type=float, default=4.0)
    ap.add_argument("--request-id")
    ap.add_argument("--out", default=str(HERE / "output" / "scorecard"))
    a = ap.parse_args()
    if a.delay < 3:
        ap.error("--delay ห้ามต่ำกว่า 3 วินาที (กฎเหล็กข้อ 3)")
    sys.exit(asyncio.run(run(a)))


if __name__ == "__main__":
    main()
