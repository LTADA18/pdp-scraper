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
    r"ถูกลบ|no longer available|product is no longer|ไม่พร้อมจำหน่าย|ไม่พร้อมใช้งาน|"
    r"not available in this (country|region)|"
    r"404|ไม่ใช่หน้าสินค้า|ลิงก์ค้นหา|อ่าน shop_id/item_id",
    re.I)
# CAPTCHA = ไม่ตาย (ลองใหม่ได้) อย่าเพิ่งใส่ deadlist
ALIVE_PATTERNS = re.compile(r"CAPTCHA|Security Check|anti-bot", re.I)

# url ปลายทางเป็น "หน้าสินค้า" จริงไหม (ทุกแพลตฟอร์ม)
# ใช้ตัวเดียวกับ scrape_pdp.py (ที่นั่นใช้ตัดจบทันทีตอนเปิดแล้วเจอว่าปลายทางไม่ใช่หน้าสินค้า)
# มีสำเนาสำรองเผื่อ import ไม่ได้ (เช่นเครื่องที่ไม่มี playwright แต่รัน build อย่างเดียว)
try:
    from scrape_pdp import PRODUCT_URL
except Exception:
    PRODUCT_URL = re.compile(
        r"-i\d{6,}"                          # lazada  ...-i1234567890
        r"|i\.\d+\.\d+|/product/\d+/\d+"     # shopee  i.shop.item / /product/shop/item
        r"|/pdp/|/view/product/\d+|/product/\d{6,}",   # tiktok  /pdp/.. /view/product/..
        re.I)


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
                final = str(r.get("url") or "")     # ปลายทางจริงหลัง redirect (ลิงก์สั้นคลายแล้ว)
                text = " ".join([str(r.get("error") or "")] + [str(w) for w in (r.get("warnings") or [])])
                # เปิดลิงก์ได้ แต่ปลายทางไม่ใช่หน้าสินค้า (เด้งหน้าแรก/ไลฟ์/แคมเปญ) = ตายทุกแพลตฟอร์ม
                # เช็คก่อน ALIVE_PATTERNS เพราะหน้าที่เด้งไปก็มี anti-bot ได้ แต่ประเด็นคือ "ลิงก์ไม่พาไปหน้าสินค้าแล้ว"
                # หมายเหตุ: ถ้าปลายทางยังเป็น url สินค้าอยู่แต่ไม่มีชื่อ = อาจ CAPTCHA/hydrate ช้า -> ไม่ตี ตาย (retry ได้)
                if final.startswith("http") and not PRODUCT_URL.search(final) and not r.get("product_name"):
                    dead[url] = "ปลายทางไม่ใช่หน้าสินค้า"
                    continue
                if ALIVE_PATTERNS.search(text):        # CAPTCHA ฯลฯ — ข้าม ไม่ใส่ deadlist
                    continue
                # /search โดยตรง หรือ note บอกว่าตาย
                if "/search" in url or "keyword=" in url or DEAD_PATTERNS.search(text):
                    m = DEAD_PATTERNS.search(text)
                    dead[url] = (m.group(0) if m else "ลิงก์ค้นหา")

    # **ต้องรวมกับของเดิม ห้ามเขียนทับ** — deadlist มีลิงก์ที่มาจากทางอื่นด้วย
    # (check_short.py เช็คลิงก์สั้นด้วย HTTP / scrape_pdp เติมเองตอนเจอปลายทางไม่ใช่หน้าสินค้า)
    # ลิงก์พวกนั้นไม่มีใน raw_*.ndjson ถ้าเขียนทับจะหายหมดแล้วกลับไปเปิดซ้ำใหม่ทุกรอบ
    # (เคยพลาดมาแล้ว: 1937 -> 1126 หาย tiktok short link 1098 ตัว)
    keep_old = 0
    outp = Path(args.out)
    if outp.exists():
        for line in outp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in dead:
                dead[line] = "มีอยู่เดิมใน deadlist"
                keep_old += 1

    outp.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("# ลิงก์ตายที่ยืนยันแล้ว — scrape_pdp.py ข้ามอัตโนมัติ (--skip-dead)\n")
        for url in sorted(dead):
            fh.write(url + "\n")
    print(f"ลิงก์ตายที่ยืนยัน: {len(dead)} -> {args.out}")
    if keep_old:
        print(f"   (รวมของเดิมที่มาจากทางอื่นไว้ด้วย {keep_old} ลิงก์)")
    from collections import Counter
    for reason, c in Counter(dead.values()).most_common():
        print(f"   - {reason}: {c}")


if __name__ == "__main__":
    main()
