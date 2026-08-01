#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_short.py — เช็คลิงก์สั้น TikTok (vt.tiktok.com) ด้วย HTTP redirect ว่ายังพาไปหน้าสินค้าไหม

เร็วกว่าเปิดเบราว์เซอร์มาก (~50 เท่า) ใช้คัดลิงก์ตายออกก่อน scrape จริง
- ปลายทางเป็นหน้าสินค้า (/pdp/, /view/product/) -> เก็บไว้ + คืน url จริงที่คลายแล้ว
- เด้งหน้าแรก (www.tiktok.com/?_r=1) หรือที่อื่น -> ลิงก์ตาย ใส่ deadlist

ใช้:
    python check_short.py --urls urls_tiktok_all.txt --raw data/raw_tiktok_all.ndjson
      -> เขียน data/dead_links.txt (ต่อท้าย) + urls_tiktok_live.txt (ลิงก์ที่ยังดี, url จริง)
"""
import argparse
import json
import re
import random
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from scrape_pdp import PRODUCT_URL
except Exception:
    PRODUCT_URL = re.compile(r"/pdp/|/view/product/\d+|/product/\d{6,}", re.I)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


DELAY = 0.0          # หน่วงต่อ request (ตั้งจาก --delay) — ยิงถี่ TikTok ตอบ 403 หมด


def resolve(url, timeout=15, retries=3):
    """คืน (final_url, None) หรือ (None, เหตุผล error) — ตาม redirect ให้สุดทาง
    403 = โดน rate limit **ไม่ใช่ลิงก์ตาย** ต้อง backoff แล้วลองใหม่ ห้ามตีเป็นตาย"""
    last = "ไม่ได้ทำ"
    for k in range(retries):
        if DELAY:
            time.sleep(DELAY * (1 + random.random()))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.geturl(), None
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (403, 429, 503):        # โดนกัน -> พักนานขึ้นแล้วลองใหม่
                time.sleep(3 * (k + 1) + random.random() * 2)
                continue
            return None, last                    # 404 ฯลฯ = ตอบชัดแล้ว ไม่ต้องลองซ้ำ
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:40]}"
    return None, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default="urls_tiktok_all.txt")
    ap.add_argument("--raw", default="data/raw_tiktok_all.ndjson")
    ap.add_argument("--deadlist", default="data/dead_links.txt")
    ap.add_argument("--out-live", default="urls_tiktok_live.txt")
    ap.add_argument("--workers", type=int, default=4,
                    help="ยิงพร้อมกันกี่เส้น (มากไป TikTok ตอบ 403 หมด, default 4)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="หน่วงต่อ request วินาที (default 1.0)")
    ap.add_argument("--limit", type=int, default=0, help="ทดสอบเฉพาะ N ตัวแรก (0=ทั้งหมด)")
    args = ap.parse_args()
    global DELAY
    DELAY = args.delay

    urls = [u.strip() for u in Path(args.urls).read_text(encoding="utf-8").splitlines()
            if u.strip() and not u.startswith("#")]
    done = set()
    if Path(args.raw).exists():
        for line in Path(args.raw).read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    done.add(r.get("url_requested") or r.get("url"))
                except json.JSONDecodeError:
                    pass
    dead_now = {l.strip() for l in Path(args.deadlist).read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")} if Path(args.deadlist).exists() else set()

    todo = [u for u in urls if u not in done and u not in dead_now]
    if args.limit:
        todo = todo[:args.limit]
    print(f"ต้องเช็ค {len(todo)} ลิงก์ (ข้ามที่ทำแล้ว {len(done)} + deadlist {len(dead_now)})",
          file=sys.stderr)

    live, dead, err = [], [], []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (url, (final, e)) in enumerate(zip(todo, ex.map(resolve, todo)), 1):
            if final and PRODUCT_URL.search(final):
                live.append((url, final))
            elif final:
                dead.append((url, final))
            else:
                err.append((url, e))
            if i % 100 == 0:
                print(f"  {i}/{len(todo)} | ดี {len(live)} ตาย {len(dead)} error {len(err)}",
                      file=sys.stderr)

    with open(args.deadlist, "a", encoding="utf-8") as fh:
        for url, final in dead:
            fh.write(url + "\n")
    Path(args.out_live).write_text(
        "# ลิงก์ TikTok ที่ยังเปิดถึงหน้าสินค้าจริง (คลายลิงก์สั้นแล้ว) — จาก check_short.py\n"
        + "\n".join(f for _, f in live) + ("\n" if live else ""), encoding="utf-8")

    print(f"\nยังดี  : {len(live)}  -> {args.out_live}")
    print(f"ตาย    : {len(dead)}  -> เพิ่มเข้า {args.deadlist}")
    print(f"error  : {len(err)}   (ไม่ตัดสิน ปล่อยไว้ลองใหม่)")
    if err[:3]:
        for u, e in err[:3]:
            print(f"   เช่น {u} -> {e}")


if __name__ == "__main__":
    main()
