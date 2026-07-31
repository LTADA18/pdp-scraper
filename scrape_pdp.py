#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_pdp.py — เปิดหน้าสินค้าด้วย Playwright แล้วฉีด extract_pdp.js เข้าไปดึงข้อมูล
ออกแบบให้รันบน Claude Code / Terminal (ต่างจาก Claude in Chrome ตรงที่ทำ batch + cron ได้)

ติดตั้งครั้งแรก:
    pip install playwright
    playwright install chromium

ล็อกอินครั้งแรก (จำเป็นสำหรับ Shopee — cookie จะถูกเก็บใน profile):
    python scrape_pdp.py --login

รันเก็บข้อมูล:
    python scrape_pdp.py --urls urls.txt --out raw_2026-07-23.ndjson
    python normalize_pdp.py --inputs raw_2026-07-23.ndjson --out Sales_Tracker_PDP_2026-07-23.xlsx

หมายเหตุ: เขียนผลแบบ NDJSON ต่อท้ายทีละรายการ ถ้าหลุดกลางทางรันซ้ำได้ (--resume ข้าม URL ที่ทำแล้ว)
"""

import argparse
import asyncio
import json
import os
import random
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import async_playwright

TH = timezone(timedelta(hours=7))
HERE = Path(__file__).resolve().parent
JS_PATH = HERE / "extract_pdp.js"
PROFILE = HERE / ".browser_profile"          # persistent profile เก็บ cookie/login

# ไม่ override user-agent — UA ปลอมที่ไม่ตรงกับ OS/เบราว์เซอร์จริงทำให้ fingerprint ขัดกันเอง
# แล้วโดน anti-bot จับง่ายกว่าเดิม ปล่อยให้ Chromium ส่ง UA ของตัวเองไป


def load_js():
    if not JS_PATH.exists():
        sys.exit(f"ไม่พบ {JS_PATH} — ต้องวางไว้โฟลเดอร์เดียวกับสคริปต์นี้")
    src = JS_PATH.read_text(encoding="utf-8").strip().rstrip(";")
    # ห่อเป็น arrow function เพื่อให้ Playwright เรียกและ await ผลลัพธ์ให้แน่นอน
    return "async () => { return await (%s); }" % src


def read_urls(path):
    urls = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line.split()[0])
    return list(dict.fromkeys(urls))       # ตัดซ้ำ คงลำดับเดิม


DEFAULT_CDP = "http://localhost:9222"


def probe_cdp(url, timeout=1.5):
    """มี Chrome เปิด debug port ค้างอยู่ไหม — ถ้ามีให้เกาะตัวนั้นแทนเปิดหน้าต่างใหม่"""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=timeout):
            return url
    except Exception:
        return None


def platform_of(url):
    h = urlsplit(url).netloc
    if "lazada." in h:
        return "lazada"
    if "shopee." in h:
        return "shopee"
    if "tiktok" in h:
        return "tiktok"
    return None


def clean_url(url):
    """ตัด query ของลิงก์แชร์จากแอปทิ้ง (enable_shop_tab_popup, share_*, utm_*, timestamp เก่า)
    เหลือแค่ /view/product/<id> — พารามิเตอร์พวกนี้บอก TikTok ตรง ๆ ว่ามาจากการแชร์ ทำให้โดน CAPTCHA ง่ายขึ้น
    ใช้เฉพาะตอน navigate ส่วน url_requested ยังเก็บลิงก์เดิมไว้ให้ --resume เทียบได้"""
    try:
        u = urlsplit(url)
    except ValueError:
        return url
    if "tiktok.com" in u.netloc and "/product/" in u.path:
        return urlunsplit((u.scheme, u.netloc, u.path, "", ""))
    return url


def done_urls(out_path):
    seen = set()
    p = Path(out_path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line).get("url_requested"))
            except Exception:
                pass
    seen.discard(None)
    return seen


# Lazada PDP ไม่มี sold รายสินค้า แต่หน้า search (catalog ajax) มี — ยิง fetch แบบ same-origin
# บนหน้า PDP ที่โหลดอยู่แล้ว (same origin กับ /catalog) แล้ว match ด้วย nid (= product_id)
LAZADA_SOLD_JS = r"""
async ([queries, nid]) => {
  const grab = (j) => {
    const find = (o, d = 0) => {
      if (!o || typeof o !== 'object' || d > 8) return null;
      if (Array.isArray(o) && o.length && o[0] && (o[0].nid || o[0].itemId)) return o;
      for (const k of Object.keys(o)) { const r = find(o[k], d + 1); if (r) return r; }
      return null;
    };
    return find(j) || [];
  };
  // ลองหลาย query (ชื่อเต็ม -> แบรนด์+รหัสรุ่น -> รหัสรุ่น) หยุดทันทีที่เจอ nid
  // ชื่อสินค้าบางตัวมีคำเยอะจนกลบรหัสรุ่น ค้นไม่ติดอันดับ ต้องลอง query ที่เจาะจงกว่าด้วย
  for (const query of queries) {
    for (let pg = 1; pg <= 3; pg++) {
      const url = `/catalog/?ajax=true&page=${pg}&q=${encodeURIComponent(query)}`;
      try {
        // กัน fetch ค้าง (Lazada โดน CAPTCHA อาจไม่ตอบ) — abort ใน 8 วิ
        const ctrl = new AbortController();
        const to = setTimeout(() => ctrl.abort(), 8000);
        let res;
        try { res = await fetch(url, { credentials: 'include', headers: { 'x-requested-with': 'XMLHttpRequest' }, signal: ctrl.signal }); }
        finally { clearTimeout(to); }
        if (!res.ok) continue;
        const items = grab(await res.json());
        const hit = items.find(x => String(x.nid || x.itemId) === String(nid));
        if (hit) return { found: true, sold: hit.itemSoldCntShow || null, query, page: pg };
      } catch (e) { return { err: String(e).slice(0, 80) }; }
    }
  }
  return { found: false, sold: null };
}
"""


def _lazada_queries(name):
    """สร้าง query หลายแบบเรียงจากกว้างไปเจาะจง ให้ search เจอ nid มากที่สุด:
      1) ชื่อเต็ม (ตรงกับที่ Lazada index หัวข้อ)
      2) แบรนด์ + รหัสรุ่น (เจาะจง เมื่อชื่อเต็มมีคำเยอะจนกลบ)
      3) รหัสรุ่นล้วน (สำรอง)
    รหัสรุ่น = token ที่มีทั้งตัวอักษร+ตัวเลข เช่น Osid-520, Scd-110"""
    name = re.sub(r"[()\[\]|/]", " ", str(name or ""))
    words = [w for w in name.split() if w]
    if not words:
        return [""]
    full = " ".join(words[:12])
    models = [w for w in words if re.search(r"[A-Za-z]", w) and re.search(r"\d", w) and len(w) >= 4]
    brand = words[0]
    queries = [full]
    if models:
        for q in (brand + " " + " ".join(models[:3]), " ".join(models[:2])):
            q = q.strip()
            if q and q not in queries:
                queries.append(q)
    return queries


def parse_sold_show(s):
    """'546 sold' -> (546, False) ; '2.0K sold' -> (2000, True) ; คืน (จำนวน, เป็นเลขกลมไหม)"""
    if not s:
        return None, False
    m = re.search(r"([\d.,]+)\s*([KkMm]?)", str(s))
    if not m:
        return None, False
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, False
    mult = {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)
    approx = mult > 1                       # มี K/M = ถูกปัด ไม่เป๊ะ
    return int(n * mult), approx


async def enrich_lazada_sold(page, rec):
    """เติม sold_count/sold_band ให้ record ของ Lazada จากหน้า search (เรียกตอนอยู่บนหน้า PDP)"""
    pid = str(rec.get("product_id") or "")
    if not pid or not rec.get("product_name"):
        return
    try:
        # asyncio.wait_for = กันแข็ง: ถ้า page.evaluate/fetch ค้าง (โดน CAPTCHA) ให้ยอมแพ้ใน 30 วิ ไม่ค้างทั้งคืน
        r = await asyncio.wait_for(
            page.evaluate(LAZADA_SOLD_JS, [_lazada_queries(rec.get("product_name")), pid]),
            timeout=30)
    except Exception as e:
        rec.setdefault("warnings", []).append(f"lazada sold: fetch พลาด/timeout ({str(e)[:60]})")
        return
    show = r.get("sold")
    if not show:
        if r.get("found"):
            rec.setdefault("warnings", []).append("lazada sold: Lazada ไม่แสดงยอดขายสินค้านี้ (อาจขายน้อย/ใหม่)")
        else:
            rec.setdefault("warnings", []).append("lazada sold: หาใน search ไม่เจอ (อันดับเกินหน้า 3 / ชื่อไม่ตรง)")
        return
    n, approx = parse_sold_show(show)
    rec["sold_band"] = show
    rec["sold_count"] = n
    if approx:
        rec.setdefault("warnings", []).append(
            "lazada sold เป็นเลขกลม (>1พัน Lazada ปัดเป็น K) — diff รายวันอาจไม่ละเอียด")


async def wait_for_state(page, timeout_ms=8000):
    """รอให้ state object โผล่ (บางหน้า hydrate ช้า) — ไม่ error ถ้าไม่มา ปล่อยให้ fallback DOM ทำงาน"""
    try:
        await page.wait_for_function(
            """() => !!(window.__moduleData__ || window.__UNIVERSAL_DATA_FOR_REHYDRATION__
                        || window.__MODERN_ROUTER_DATA__ || window.__INITIAL_STATE__
                        || /shopee\\./.test(location.hostname))""",
            timeout=timeout_ms,
        )
    except Exception:
        pass


async def scrape_one(page, url, js, retries=2, lazada_sold=False):
    last_err = None
    target = clean_url(url)
    for attempt in range(retries + 1):
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=45000)
            await wait_for_state(page)
            await page.wait_for_timeout(1200)          # ให้ SPA render variation ให้เสร็จ
            rec = await page.evaluate(js)
            if isinstance(rec, dict):
                rec["url_requested"] = url
                rec["attempt"] = attempt + 1
                # Lazada: เติมยอดขายจากหน้า search (ยังอยู่บนหน้า PDP = same-origin)
                # ห้ามให้ error ตรงนี้ทำให้ record หลักพัง — เป็นแค่ข้อมูลเสริม
                if lazada_sold and rec.get("platform") == "lazada" and not rec.get("error"):
                    try:
                        await enrich_lazada_sold(page, rec)
                    except Exception as e:
                        rec.setdefault("warnings", []).append(f"lazada sold: ข้ามเพราะ error ({str(e)[:50]})")
                return rec
            last_err = "extractor ไม่คืน object"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        await asyncio.sleep(2 + attempt * 3)
    return {"error": last_err, "url_requested": url, "url": url,
            "scraped_at": datetime.now(TH).isoformat()}


def unusable_reason(url):
    """ลิงก์ที่เปิดไม่ได้แน่ ๆ — ข้ามเลย ไม่ต้องเสียเวลาเปิด (คืนเหตุผล หรือ None ถ้าใช้ได้)"""
    u = (url or "").strip()
    if not u:
        return "ไม่มีลิงก์"
    if "/search" in u or "keyword=" in u:
        return "ลิงก์ค้นหา ไม่ใช่หน้าสินค้า"
    return None


def load_skip_set(path):
    """รายชื่อลิงก์ตายที่ยืนยันแล้ว (data/dead_links.txt) — ข้ามถาวร"""
    s = set()
    if path and os.path.exists(path):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                s.add(line)
    return s


# จับเฉพาะ CAPTCHA/Security Check "จริง" ที่รอผ่านได้ — ไม่รวม anti-bot patch (__ac_intercepted)
# เพราะหน้า "สินค้าไม่พร้อมใช้งาน"/ลิงก์ตาย ก็มี __ac_intercepted ติดมาด้วย ถ้านับเป็น CAPTCHA จะพักยาวฟรีกับลิงก์ตาย
_CAPTCHA_RE = re.compile(r"Security Check|CAPTCHA|ยืนยันตัวตน|ลากชิ้นส่วน|verify to continue|drag the puzzle", re.I)


def is_security_check(rec):
    """True เฉพาะ 'Security Check/CAPTCHA จริงบนหน้าสินค้า' ที่รอผ่านได้
    — ไม่นับลิงก์เด้ง homepage (นั่น = ตายจริง จะได้ไม่ไปเสียเวลาคูลดาวน์กับลิงก์ตาย)"""
    final = str(rec.get("url") or "")
    if re.search(r"tiktok\.com", final, re.I) and not re.search(r"/pdp/|/view/product/|/product/", final, re.I):
        return False
    txt = " ".join([str(rec.get("error") or "")] + [str(w) for w in (rec.get("warnings") or [])])
    return bool(_CAPTCHA_RE.search(txt))


def alert_beep(times=1):
    """เสียงเตือน (เผื่อมีคนอยู่หน้าจอ) — เงียบไปเองถ้าเครื่องไม่รองรับ
    times = จำนวนครั้งที่บี๊บ (2 = เตือนหนัก ตอน CAPTCHA ค้างนานเกินกำหนด ต้องมีคนมาลาก)"""
    for j in range(max(1, times)):
        try:
            sys.stderr.write("\a"); sys.stderr.flush()
        except Exception:
            pass
        try:
            import winsound
            winsound.Beep(880, 300)
        except Exception:
            pass
        if j < times - 1:
            try:
                import time
                time.sleep(0.25)   # เว้นจังหวะให้ได้ยินเป็น 2 ที ชัด ๆ
            except Exception:
                pass


async def read_current_page(page, url, js):
    """อ่านข้อมูลจากหน้าที่เปิดค้างอยู่ *โดยไม่ goto ใหม่*
    — สำคัญ: ถ้า reload จะไปล้าง CAPTCHA ที่คนกำลังลากอยู่ ต้องอ่านหน้าเดิมเท่านั้น"""
    try:
        await wait_for_state(page, timeout_ms=3000)
        rec = await page.evaluate(js)
        if isinstance(rec, dict):
            rec["url_requested"] = url
            return rec
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "url_requested": url, "url": url,
                "scraped_at": datetime.now(TH).isoformat()}
    return orig_none_rec(url)


def orig_none_rec(url):
    return {"error": "extractor ไม่คืน object", "url_requested": url, "url": url,
            "scraped_at": datetime.now(TH).isoformat()}


async def handle_security_check(page, url, js, args, i, n, in_streak, orig):
    """เจอ Security Check -> **ค้างอยู่ที่ลิงก์นี้** รอคนมากดผ่าน ไม่เด้งไปลิงก์ใหม่

    วน poll ทุก --captcha-wait วินาที (default 30) สูงสุด --captcha-rounds รอบ (default 8):
    - poll = อ่านหน้าเดิม ไม่ reload (reload จะล้าง CAPTCHA ที่คนกำลังลาก)
    - กดผ่านเมื่อไหร่ รอบถัดไปเจอข้อมูลเลย -> เก็บแล้วไปต่อทันที
    - ครบทุกรอบยังไม่กด -> ข้ามลิงก์ พร้อม note "ติด CAPTCHA" ไว้เก็บซ้ำรอบหลัง
    ไม่มีการตีเป็นลิงก์ตาย: ที่ยังไม่ผ่าน = ไปกองเก็บซ้ำ"""
    alert_beep()
    rec = orig
    rounds = max(1, int(args.captcha_rounds))
    for k in range(1, rounds + 1):
        print(f"[{i}/{n}] 🔒 CAPTCHA — ค้างรออยู่ที่ลิงก์นี้ ({k}/{rounds}) "
              f"รอ {args.captcha_wait:.0f}s [ลากจิ๊กซอว์ในหน้าต่าง Chrome ได้เลย]", file=sys.stderr)
        await asyncio.sleep(args.captcha_wait)
        probe = await read_current_page(page, url, js)      # อ่านหน้าเดิม ไม่ reload
        if not is_security_check(probe) and probe.get("product_name"):
            print(f"[{i}/{n}] ✓ ผ่าน CAPTCHA แล้ว — เก็บข้อมูลต่อ", file=sys.stderr)
            return probe
        rec = probe if probe.get("product_name") else rec
        if k == rounds // 2:                                # ครึ่งทางยังไม่กด เตือนอีกที
            alert_beep(2)

    # ครบทุกรอบยังไม่มีคนกด -> ข้าม แต่ note ไว้ให้ชัดว่าติด CAPTCHA (ไว้ redo รอบหลัง)
    total = args.captcha_wait * rounds
    print(f"[{i}/{n}] ⏭ ไม่มีคนกด CAPTCHA ครบ {rounds}/{rounds} รอบ ({total:.0f}s) — ข้ามไว้ก่อน "
          f"(note ไว้แล้ว เก็บซ้ำรอบหลังได้)", file=sys.stderr)
    alert_beep(2)
    rec = dict(rec or {})
    rec.setdefault("url_requested", url)
    rec.setdefault("url", url)
    rec["source"] = "blocked"
    rec["captcha_skipped"] = True                            # ธงให้ redo.py/กรองหาได้ง่าย
    rec.setdefault("warnings", []).append(
        f"ติด CAPTCHA: รอคนกดครบ {rounds} รอบ ({total:.0f}s) ไม่ผ่าน — ข้ามไว้ เก็บซ้ำรอบหลัง")
    rec["scraped_at"] = datetime.now(TH).isoformat()
    return rec


async def scrape_loop(page, urls, js, args):
    """วนเก็บทีละ URL เขียน NDJSON ต่อท้าย — ใช้ร่วมกันทั้งโหมด launch เองและโหมด --cdp"""
    out = open(args.out, "a", encoding="utf-8") if args.out else None
    skip_set = load_skip_set(getattr(args, "skip_dead", None))
    ok = fail = skipped = 0
    consec_captcha = 0
    try:
        for i, url in enumerate(urls, 1):
            # ลิงก์เปิดไม่ได้ / ตายแล้ว -> ใส่ record blocked (= Null + เหตุผล) ไม่เปิดหน้า ไม่เสียเวลา
            dead = unusable_reason(url) or ("อยู่ในรายชื่อลิงก์ตาย (dead_links)" if url in skip_set else None)
            if dead:
                skipped += 1
                rec = {"source": "blocked", "platform": platform_of(url),
                       "url_requested": url, "url": url,
                       "warnings": [f"ข้ามไม่สแกน: {dead}"],
                       "scraped_at": datetime.now(TH).isoformat()}
                print(f"[{i}/{len(urls)}] SKIP {dead}", file=sys.stderr)
                if out:
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
                else:
                    print(json.dumps(rec, ensure_ascii=False))
                continue

            rec = await scrape_one(page, url, js, retries=args.retries,
                                   lazada_sold=getattr(args, "lazada_sold", False))
            # เจอ CAPTCHA -> ค้างรอคนกดที่ลิงก์นี้ (ไม่เด้งลิงก์ใหม่) ครบรอบแล้วค่อยข้าม
            if is_security_check(rec):
                rec = await handle_security_check(page, url, js, args, i, len(urls),
                                                  consec_captcha >= 1, rec)

            if rec.get("error") or not rec.get("product_name"):
                fail += 1
                reason = rec.get("error") or (rec.get("warnings") or ["ไม่พบชื่อสินค้า"])[0]
                status = "FAIL " + str(reason)[:70]
            else:
                ok += 1
                status = f"OK   {rec.get('source')} | {str(rec.get('product_name'))[:40]}"
            print(f"[{i}/{len(urls)}] {status}", file=sys.stderr)

            line = json.dumps(rec, ensure_ascii=False)
            if out:
                out.write(line + "\n")
                out.flush()
            else:
                print(line)

            # ยังติด CAPTCHA อยู่หลังรอคนกด = session อาจโดนแบนยาว -> พักก้อนใหญ่ให้รีเซ็ต
            # (ตัวที่ข้ามเพราะไม่มีคนกดก็นับด้วย — ติดกันหลายตัว = ไม่มีคนเฝ้าแล้ว พักยาวคุ้มกว่าไล่ยิงต่อ)
            if is_security_check(rec) or rec.get("captcha_skipped"):
                consec_captcha += 1
                if args.platform in ("tiktok", "lazada") and consec_captcha >= args.captcha_streak:
                    print(f"[captcha] โดนติดกัน {consec_captcha} ตัว (เกินกำหนด {args.captcha_streak}) "
                          f"— บี๊บ 2 ที เรียกคนมาลาก แล้วพักยาว {args.long_cooldown:.0f}s ให้ session รีเซ็ต",
                          file=sys.stderr)
                    alert_beep(2)   # ค้างนานเกินกำหนด = เตือนหนัก 2 ที
                    await asyncio.sleep(args.long_cooldown)
                    consec_captcha = 0
            else:
                consec_captcha = 0

            if i < len(urls):
                # พักเบรกเป็นก้อนทุก ๆ N ตัว (เฉพาะ tiktok) กัน CAPTCHA สะสม — ตัวการหลักตาม CLAUDE.md
                if (args.platform in ("tiktok", "lazada") and args.batch_cooldown > 0
                        and args.batch_size > 0 and i % args.batch_size == 0):
                    print(f"[batch] ครบ {args.batch_size} ตัว — พักเบรก {args.batch_cooldown:.0f}s กัน CAPTCHA สะสม",
                          file=sys.stderr)
                    await asyncio.sleep(args.batch_cooldown)
                await asyncio.sleep(random.uniform(args.delay, args.delay * 1.8))
    finally:
        if out:
            out.close()

    print(json.dumps({"total": len(urls), "ok": ok, "fail": fail, "skipped": skipped, "out": args.out},
                     ensure_ascii=False), file=sys.stderr)


async def run(args):
    js = load_js()
    urls = read_urls(args.urls) if args.urls else []
    if args.platform:
        before = len(urls)
        urls = [u for u in urls if platform_of(u) == args.platform]
        print(f"[platform] ทำเฉพาะ {args.platform}: {len(urls)}/{before} URL", file=sys.stderr)
    if args.resume and args.out:
        skip = done_urls(args.out)
        before = len(urls)
        urls = [u for u in urls if u not in skip]
        print(f"[resume] ข้าม {before - len(urls)} URL ที่ทำไปแล้ว", file=sys.stderr)

    PROFILE.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        # ---- โหมด CDP: เกาะ Chrome ที่ผู้ใช้เปิดค้างไว้เอง (ผ่าน CAPTCHA/ล็อกอินมาแล้วด้วยมือ) ----
        # เบราว์เซอร์ตัวนี้ผู้ใช้เป็นคนเปิด anti-bot จึงไม่เห็นร่องรอย automation แบบ launch เอง
        # --login ต้องเปิดหน้าต่างใหม่เสมอ (จุดประสงค์คือไปล็อกอิน) จึงไม่ auto-detect
        cdp = args.cdp
        if not cdp and not args.no_cdp and not args.login:
            cdp = probe_cdp(DEFAULT_CDP)
            if cdp:
                print(f"[cdp] เจอ Chrome เปิดอยู่ที่ {cdp} — ใช้ตัวนั้น (ไม่เปิดหน้าต่างใหม่)", file=sys.stderr)

        if cdp:
            browser = await pw.chromium.connect_over_cdp(cdp)
            if not browser.contexts:
                sys.exit(f"ต่อ {cdp} ได้ แต่ไม่พบ context — เปิดแท็บใน Chrome ก่อน")
            ctx = browser.contexts[0]
            page = await ctx.new_page()
            print(f"[cdp] เกาะ Chrome ที่ {cdp} แล้ว ({len(ctx.pages)} แท็บ)", file=sys.stderr)
            try:
                await scrape_loop(page, urls, js, args)
            finally:
                await page.close()          # ปิดแค่แท็บที่เราเปิด ไม่แตะเบราว์เซอร์ของผู้ใช้
            return

        opts = dict(
            user_data_dir=str(PROFILE),
            headless=args.headless,
            locale="th-TH",
            timezone_id="Asia/Bangkok",
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Chrome ตัวจริงมีร่องรอยต่างจาก Chromium ที่ Playwright bundle มา (codec, build flags, brand)
        # anti-bot ของ TikTok ดูจุดพวกนี้ — ถ้าเครื่องมี Chrome ให้ใช้ตัวจริงก่อน
        try:
            ctx = await pw.chromium.launch_persistent_context(channel=args.channel, **opts)
        except Exception as e:
            if args.channel == "chromium":
                raise
            print(f"[warn] เปิด channel={args.channel} ไม่ได้ ({e}) — ถอยไปใช้ chromium", file=sys.stderr)
            ctx = await pw.chromium.launch_persistent_context(**opts)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        if args.login:
            want = args.platform            # None = เปิดให้ครบทุกแพลตฟอร์ม
            tabs = []
            if want in (None, "shopee"):
                tabs.append(("Shopee", "https://shopee.co.th/buyer/login",
                             "ล็อกอินให้เรียบร้อย (ต้องมี cookie ถึงจะเรียก API ได้)"))
            if want in (None, "tiktok"):
                tabs.append(("TikTok", next((clean_url(u) for u in urls if platform_of(u) == "tiktok"),
                                            "https://www.tiktok.com/shop"),
                             "ถ้าเจอ Security Check ให้ลากจิ๊กซอว์ผ่านเอง แล้วรอจนเห็นหน้าสินค้า"))

            print(f"เปิดเบราว์เซอร์แล้ว — ทำให้ครบ {len(tabs)} แท็บ:")
            for i, (name, url, how) in enumerate(tabs, 1):
                print(f"  แท็บ {i} {name}: {how}")
                p = page if i == 1 else await ctx.new_page()
                try:
                    await p.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print(f"[warn] เปิดแท็บ {name} ไม่สำเร็จ: {e}", file=sys.stderr)
            print("เสร็จแล้วกด Enter ในเทอร์มินัลนี้เพื่อบันทึก session")
            await asyncio.to_thread(input)
            await ctx.close()
            print(f"บันทึก profile ไว้ที่ {PROFILE}")
            return

        try:
            await scrape_loop(page, urls, js, args)
        finally:
            await ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", help="ไฟล์ .txt ลิงก์สินค้า บรรทัดละ 1 ลิงก์ (# = คอมเมนต์)")
    ap.add_argument("--out", help="ไฟล์ .ndjson ปลายทาง (ไม่ใส่ = พิมพ์ออก stdout)")
    ap.add_argument("--login", action="store_true", help="เปิดเบราว์เซอร์ให้ล็อกอินครั้งแรก")
    ap.add_argument("--headless", action="store_true", help="รันแบบไม่มีหน้าต่าง (โดนตรวจจับง่ายกว่า)")
    ap.add_argument("--resume", action="store_true", help="ข้าม URL ที่มีอยู่แล้วในไฟล์ --out")
    ap.add_argument("--platform", choices=["lazada", "shopee", "tiktok"],
                    help="ทำเฉพาะแพลตฟอร์มนี้ (ใช้กับ --login ด้วย = เปิดแค่แท็บนั้น)")
    ap.add_argument("--channel", default="chrome", choices=["chrome", "msedge", "chromium"],
                    help="เบราว์เซอร์ที่ใช้ (default chrome = Chrome ตัวจริง โดนตรวจจับน้อยกว่า)")
    ap.add_argument("--cdp", nargs="?", const=DEFAULT_CDP, default=None,
                    help=f"เกาะ Chrome ที่เปิดค้างไว้เอง (default {DEFAULT_CDP}) "
                         "ใช้เมื่อ TikTok บังคับ CAPTCHA — ผู้ใช้กดผ่านเอง แล้วสคริปต์ทำงานบนแท็บนั้น "
                         "ถ้าไม่ใส่ จะตรวจให้อัตโนมัติว่ามี Chrome เปิดอยู่ไหม")
    ap.add_argument("--no-cdp", action="store_true",
                    help="บังคับเปิดเบราว์เซอร์ใหม่ ไม่เกาะตัวที่เปิดอยู่")
    ap.add_argument("--lazada-sold", action="store_true",
                    help="เติมยอดขาย Lazada จากหน้า search (PDP ไม่มี) — ช้าขึ้นเล็กน้อย, sold เกิน 1พันเป็นเลขกลม")
    ap.add_argument("--skip-dead", default="data/dead_links.txt",
                    help="ไฟล์รายชื่อลิงก์ตายที่ข้ามถาวร (สร้างด้วย build_deadlist.py)")
    ap.add_argument("--delay", type=float, default=4.0, help="หน่วงระหว่างสินค้า (วินาที, default 4)")
    ap.add_argument("--retries", type=int, default=2)
    # --- กัน/กู้ CAPTCHA ของ TikTok (รันข้ามคืนไม่ต้องมีคน) ---
    ap.add_argument("--captcha-cooldown", type=float, default=60.0,
                    help="(ไม่ใช้แล้วในโหมดรอคน) เก็บไว้เพื่อความเข้ากันได้กับสคริปต์เดิม")
    ap.add_argument("--captcha-retries", type=int, default=1,
                    help="(ไม่ใช้แล้วในโหมดรอคน) เก็บไว้เพื่อความเข้ากันได้กับสคริปต์เดิม")
    ap.add_argument("--captcha-wait", type=float, default=30.0,
                    help="เจอ CAPTCHA: ค้างรอคนกดรอบละกี่วินาที (default 30)")
    ap.add_argument("--captcha-rounds", type=int, default=8,
                    help="รอคนกด CAPTCHA กี่รอบก่อนข้ามลิงก์ (default 8 = 8x30s = 4 นาที)")
    ap.add_argument("--captcha-streak", type=int, default=3,
                    help="โดน CAPTCHA ติดกันกี่ตัว = session พัง -> พักยาว (default 3)")
    ap.add_argument("--long-cooldown", type=float, default=600.0,
                    help="พักยาวรีเซ็ต session เมื่อโดนถล่มติดกัน (วินาที, default 600=10นาที)")
    ap.add_argument("--batch-size", type=int, default=40,
                    help="พักเบรกทุก ๆ กี่ตัว เฉพาะ tiktok (default 40)")
    ap.add_argument("--batch-cooldown", type=float, default=90.0,
                    help="ความยาวเบรกแต่ละก้อน วินาที (0=ปิด, default 90)")
    args = ap.parse_args()
    if not args.login and not args.urls:
        ap.error("ต้องใส่ --urls หรือ --login")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
