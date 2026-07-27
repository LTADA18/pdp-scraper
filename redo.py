#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
redo.py — เก็บซ้ำเฉพาะ record ที่พลาด แล้ว merge กลับโดยไม่ทำข้อมูลดีหาย

ใช้:
    python redo.py pick  data/raw_2026-07-24.ndjson          # -> สร้าง redo list + สรุปเหตุผล
    #  รัน scrape ด้วย list นั้น (ดูคำสั่งที่ pick พิมพ์ให้)
    python redo.py merge data/raw_2026-07-24.ndjson data/redo_2026-07-24.ndjson

"pick"  = หา record ที่ยังไม่สมบูรณ์ เขียน url_requested ลงไฟล์ .txt
"merge" = รวม raw เดิม + ผลเก็บซ้ำ เก็บอันที่ดีกว่าต่อสินค้า (state/api + มี sold ชนะ blocked/error)
"""
import json
import sys
from pathlib import Path


def load(path):
    recs = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return recs


def is_search_junk(r):
    """ลิงก์ /search หรือลิงก์ที่อ่าน id ไม่ได้ — เก็บซ้ำไปก็ไม่ได้ผล ควรลบออกจาก urls.txt"""
    u = (r.get("url_requested") or r.get("url") or "")
    if "/search" in u:
        return True
    return any("อ่าน shop_id/item_id" in w for w in (r.get("warnings") or []))


def why_bad(r):
    """คืนเหตุผลว่าทำไมต้องเก็บซ้ำ (None = ดีแล้ว ไม่ต้องทำ)"""
    if not isinstance(r, dict):
        return "ไม่ใช่ object"
    if r.get("error"):
        return "error"
    src = r.get("source")
    if src in (None, "blocked"):
        return "blocked/CAPTCHA" if src == "blocked" else "ไม่ได้ข้อมูล"
    if src in ("dom", "jsonld"):
        return f"source={src} (state พัง)"
    if not r.get("product_name"):
        return "ไม่มีชื่อสินค้า"
    plat, sold = r.get("platform"), r.get("sold_count")
    if plat in ("tiktok", "shopee") and sold is None:
        return f"{plat} ไม่มี sold_count"
    if plat == "lazada" and sold is None:
        # ถ้า enrich เคยรันแล้ว (มี warning เรื่อง sold) = Lazada ไม่โชว์จริง ไม่ต้องทำซ้ำ
        w = " ".join(r.get("warnings") or [])
        if "lazada sold" not in w:
            return "lazada เก็บก่อนมีฟีเจอร์ sold"
    return None


def score(r):
    """คะแนนความสมบูรณ์ — ใช้เลือกอันที่ดีกว่าตอน merge"""
    s = 0
    if not r.get("error"):
        s += 1
    s += {"state": 4, "api": 4, "dom": 1, "jsonld": 1}.get(r.get("source"), 0)
    if r.get("product_name"):
        s += 2
    if r.get("sold_count") is not None:
        s += 2
    return s


def key_of(r):
    pid = r.get("product_id")
    return (r.get("platform"), str(pid)) if pid else ("url", r.get("url_requested") or r.get("url"))


def cmd_pick(raw_path):
    recs = load(raw_path)
    redo, junk, reasons = [], [], {}
    for r in recs:
        if is_search_junk(r):
            junk.append(r.get("url_requested") or r.get("url"))
            continue
        reason = why_bad(r)
        if reason:
            url = r.get("url_requested") or r.get("url")
            if url:
                redo.append(url)
                reasons[reason] = reasons.get(reason, 0) + 1

    stem = Path(raw_path).stem.replace("raw_", "")
    out = Path(raw_path).with_name(f"redo_{stem}.txt")
    out.write_text("\n".join(dict.fromkeys(redo)) + ("\n" if redo else ""), encoding="utf-8")

    print(f"อ่าน {len(recs)} records จาก {raw_path}")
    print(f"ต้องเก็บซ้ำ: {len(set(redo))} (เขียนลง {out})")
    for reason, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"   - {reason}: {n}")
    if junk:
        print(f"ลิงก์ใช้ไม่ได้ (ลบจาก urls.txt): {len(junk)} — เช่น /search หรืออ่าน id ไม่ได้")
    if redo:
        redo_out = Path(raw_path).with_name(f"redo_{stem}.ndjson")
        print("\nรันเก็บซ้ำ (Chrome CDP + login/CAPTCHA พร้อมแล้ว):")
        print(f'  .\\.venv\\Scripts\\python.exe scrape_pdp.py --urls "{out}" --lazada-sold --cdp --delay 8 --out "{redo_out}"')
        print("แล้ว merge กลับ:")
        print(f'  .\\.venv\\Scripts\\python.exe redo.py merge "{raw_path}" "{redo_out}"')


def cmd_merge(raw_path, redo_path):
    best = {}
    for r in load(raw_path) + load(redo_path):     # redo ทีหลัง = ชนะเมื่อคะแนนเท่ากัน
        k = key_of(r)
        if k not in best or score(r) >= score(best[k]):
            best[k] = r

    merged = list(best.values())
    bak = Path(raw_path).with_suffix(".ndjson.bak")
    Path(raw_path).rename(bak)
    with open(raw_path, "w", encoding="utf-8") as fh:
        for r in merged:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    improved = sum(1 for r in load(redo_path) if not why_bad(r))
    print(f"merge เสร็จ: {len(merged)} records -> {raw_path}")
    print(f"   สำรองไฟล์เดิมไว้ที่ {bak}")
    print(f"   เก็บซ้ำสำเร็จ (สมบูรณ์แล้ว): {improved}/{len(load(redo_path))}")
    print(f"\nแปลง Excel: .\\.venv\\Scripts\\python.exe normalize_pdp.py --inputs \"{raw_path}\" --out output\\Sales_Tracker_PDP_{Path(raw_path).stem.replace('raw_','')}.xlsx")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "pick":
        cmd_pick(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "merge":
        cmd_merge(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
