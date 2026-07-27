#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_deadlist.py — รวบรวม "ลิงก์ตาย" ที่ยืนยันแล้วจากผล scrape เก่า -> data/dead_links.txt
scrape_pdp.py จะอ่านไฟล์นี้ (--skip-dead) แล้วข้ามลิงก์พวกนี้ ไม่เปิดซ้ำ ไม่เสียเวลา

"ตาย" = source=blocked + เหตุผลถาวร (สินค้าถูกลบ / no longer available / 404 /
         ปลายทางไม่ใช่หน้าสินค้า / ลิงก์ค้นหา) — ไม่รวม CAPTCHA (อันนั้นลองใหม่ได้)

ใช้:
    python build_deadlist.py --raw "data/raw_*.ndjson"
"""
import argparse
import glob
import json
import re
from pathlib import Path

# เหตุผลที่ถือว่าตายถาวร (regex บน note/error)
DEAD_PATTERNS = re.compile(
    r"ถูกลบ|no longer available|product is no longer|ไม่พร้อมจำหน่าย|"
    r"404|ไม่ใช่หน้าสินค้า|ลิงก์ค้นหา|อ่าน shop_id/item_id",
    re.I)
# CAPTCHA = ไม่ตาย (ลองใหม่ได้) อย่าเพิ่งใส่ deadlist
ALIVE_PATTERNS = re.compile(r"CAPTCHA|Security Check|anti-bot", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", nargs="+", default=["data/raw_*.ndjson"])
    ap.add_argument("--out", default="data/dead_links.txt")
    args = ap.parse_args()

    dead = {}          # url -> เหตุผล
    for g in args.raw:
        for fn in glob.glob(g):
            for line in Path(fn).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = r.get("url_requested") or r.get("url")
                if not url:
                    continue
                text = " ".join([str(r.get("error") or "")] + [str(w) for w in (r.get("warnings") or [])])
                if ALIVE_PATTERNS.search(text):        # CAPTCHA ฯลฯ — ข้าม ไม่ใส่ deadlist
                    continue
                # /search โดยตรง หรือ note บอกว่าตาย
                if "/search" in url or "keyword=" in url or DEAD_PATTERNS.search(text):
                    m = DEAD_PATTERNS.search(text)
                    dead[url] = (m.group(0) if m else "ลิงก์ค้นหา")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("# ลิงก์ตายที่ยืนยันแล้ว — scrape_pdp.py ข้ามอัตโนมัติ (--skip-dead)\n")
        for url in sorted(dead):
            fh.write(url + "\n")
    print(f"ลิงก์ตายที่ยืนยัน: {len(dead)} -> {args.out}")
    from collections import Counter
    for reason, c in Counter(dead.values()).most_common():
        print(f"   - {reason}: {c}")


if __name__ == "__main__":
    main()
