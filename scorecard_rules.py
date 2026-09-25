"""เกณฑ์ Listing Scorecard — ฟังก์ชันล้วน รับ record จาก extract_pdp.js แล้วคืนคะแนน

ชั้นตามเส้นทางลูกค้า (รวม 100)
  L1 หน้าค้นหา 20        เด่นแค่ไหนเมื่ออยู่ปนร้านอื่น (เทียบค่ากลางของหน้าผลค้นหาเดียวกัน)
  L2 จอแรกหน้าสินค้า 35   ★ รูป ≥ 9 · ★ ชื่อมีแบรนด์/ชื่อสินค้า/รุ่น · วิดีโอ · ตัวเลือก · ราคา
  L3 รายละเอียด 35        ★ ของในกล่อง · ★ สเปก · ★ คีย์ฟีเจอร์ · ★ ประกัน · สเปกไม่ขัดตัวสินค้า
  L4 ความเชื่อมั่น 10      รีวิว

★ = เกณฑ์หลักของเจ้าของงาน (2026-09-25)
"ใช้คำตรงกับ price list" (ของในกล่อง/สเปก/คีย์ฟีเจอร์) ยังตรวจไม่ได้ — price list ใน Postgres
(intel.ref_portal_product) ไม่มี 3 ช่องนี้ -> pending ไม่หักคะแนน

กฎเหล็ก: ข้อที่ไม่มีข้อมูล = ok None (ข้าม ไม่หักคะแนน) ห้ามเดาค่า
"""
from __future__ import annotations

import json
import os
import re
import statistics as st
from datetime import datetime
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "output" / "scorecard" / "_refcache.json"

# ---------------------------------------------------------------- ข้อมูลอ้างอิง

def load_ref() -> dict:
    """ทะเบียนสินค้า + price list จาก Postgres (อ่านอย่างเดียว) — ต่อไม่ได้ = ข้อที่ต้องใช้จะถูกข้าม"""
    ref = {"master": {}, "portal": {}, "error": None}
    os.environ.setdefault("PGSERVICEFILE", r"C:/Users/tada.p/Postgres/.pg_service.conf")
    os.environ.setdefault("PGPASSFILE", r"C:/Users/tada.p/Postgres/pgpass.conf")
    os.environ.setdefault("PGCLIENTENCODING", "UTF8")
    try:
        import psycopg
        # ⚠️ งาน sync price list (db_builder) ล็อกตารางค้างได้เป็นสิบนาที — เคยทำให้การตรวจค้างเงียบ (2026-09-25)
        #    รอสูงสุด 15 วิ แล้วถอยไปใช้ข้อมูลชุดล่าสุดที่บันทึกไว้
        with psycopg.connect("service=osuka", connect_timeout=15,
                             options="-c lock_timeout=15000 -c statement_timeout=30000") as conn, conn.cursor() as cur:
            cur.execute("select model_number, coalesce(status,''), sku, coalesce(product_name,'') "
                        "from intel.osuka_master_data_product where model_number is not null")
            for m, stt, sku, name in cur.fetchall():
                k = m.strip().upper()
                prev = ref["master"].get(k)
                # รุ่นเดียวกันมีหลายแถว (OSID-520: Active 1 + Discontinued 10) — มีแถวไหน Active = ยังขาย
                if prev and prev["status"] == "Active":
                    continue
                ref["master"][k] = {"status": stt, "sku": sku, "name": name}
            cur.execute("select model, product_name, retail_min, retail_max, is_active "
                        "from intel.ref_portal_product where model is not null")
            for m, name, lo, hi, act in cur.fetchall():
                ref["portal"][m.strip().upper()] = {"name": name, "min": float(lo) if lo is not None else None,
                                                    "max": float(hi) if hi is not None else None, "active": bool(act)}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"saved_at": datetime.now().isoformat(timespec="seconds"),
                                     "master": ref["master"], "portal": ref["portal"]}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — ไม่มีฐาน = ใช้ชุดที่บันทึกไว้ ถ้าไม่มีเลยค่อยข้ามข้อที่ต้องใช้
        ref["master"], ref["portal"] = {}, {}
        err = f"{type(e).__name__}: {e}"[:160]
        if CACHE.exists():
            c = json.loads(CACHE.read_text(encoding="utf-8"))
            ref["master"], ref["portal"] = c["master"], c["portal"]
            ref["error"] = f"อ่าน Postgres ไม่ได้ ({err}) — ใช้ทะเบียน/price list ชุดที่บันทึกเมื่อ {c['saved_at']}"
        else:
            ref["error"] = err
    # เทียบรหัสแบบไม่สนขีด: ทะเบียนมีทั้ง OCHD802-D2 และ OSID-520 / OSID-LT520 / OCDS-001
    ref["mnorm"] = {mnorm(k): k for k in ref["master"]}
    ref["pnorm"] = {mnorm(k): k for k in ref["portal"]}
    ref["bases"] = {k.split("-")[0] for k in ref["master"]}
    return ref


def mnorm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


MODEL_TOKEN = re.compile(r"(?<![A-Z0-9])[A-Z]{2,6}(?:-?[A-Z]{1,3})?-?\d{2,4}(?:-?[A-Z0-9]{1,3}){0,2}(?![A-Z0-9])")


def find_models(text, ref):
    """รหัสรุ่นในข้อความ -> [(token, รุ่นในทะเบียน|None, known)] · known = ตรงเป๊ะ หรือเป็นรหัสฐานของรุ่นในทะเบียน"""
    out = []
    for tok in dict.fromkeys(MODEL_TOKEN.findall((text or "").upper())):
        if not re.search(r"\d{3}", tok) or re.fullmatch(r"[A-Z]{1,2}\d{2,4}", tok):
            continue                     # ตัดของที่ไม่ใช่รหัสรุ่น เช่น V20 / AH400
        n = mnorm(tok)
        exact = ref["mnorm"].get(n)
        pref = exact or next((v for k, v in ref["mnorm"].items() if k.startswith(n)), None)
        out.append((tok, exact or pref, bool(pref)))
    return out


def portal_of(model, ref):
    k = ref["pnorm"].get(mnorm(model)) if model else None
    return ref["portal"].get(k) if k else None


# ---------------------------------------------------------------- ตัวช่วย

def C(label, pts, ok, note="", ratio=None, main=False, pending=False):
    return {"label": label, "pts": pts, "ok": ok, "note": note, "ratio": ratio, "main": main, "pending": pending}


def layer(checks, weight):
    appl = [c for c in checks if c["ok"] is not None]
    got = sum(c["pts"] * (c["ratio"] if c["ratio"] is not None else (1 if c["ok"] else 0)) for c in appl)
    poss = sum(c["pts"] for c in appl)
    return {"score": round(got / poss * weight, 1) if poss else None, "max": weight, "checks": checks,
            "pending_pts": sum(c["pts"] for c in checks if c.get("pending"))}


CODE = re.compile(r"\b([A-Z]{2,6}\d{3,4})(?:-([A-Z0-9]{1,3}))?\b")
SPECV = re.compile(r"\d+(\.\d+)?\s?(v|w|n\.?m|nm|ah)\b", re.I)
NOBRAND = {"no brand", "oem", ""}


def codes_in(text):
    return [(m.group(1), m.group(0)) for m in CODE.finditer((text or "").upper())]


def ptype(name):
    """คำบอกประเภทสินค้าจากชื่อใน price list เช่น 'สว่านกระแทกไร้สาย…' -> 'สว่านกระแทก'"""
    m = re.match(r"^(\S+?)(?:ไร้สาย|ไร้แปรงถ่าน|\s|$)", name or "")
    return m.group(1) if m else None


def months(txt):
    """ระยะเวลาประกันทุกค่าที่เจอ (เดือน) — 'ประกัน OSUKA 1 ปี + ร้านอีก 1 ปี' นับเป็น 24 ค่าเดียว"""
    out, txt = [], txt or ""
    combo = r"ประกัน[^\n\d]{0,15}?(\d+)\s*(ปี|เดือน)\s*\+\s*ร้าน\S*?อีก\s*(\d+)\s*(ปี|เดือน)"
    for m in re.finditer(combo, txt):
        out.append(int(m.group(1)) * (12 if m.group(2) == "ปี" else 1) + int(m.group(3)) * (12 if m.group(4) == "ปี" else 1))
    txt = re.sub(combo, " ", txt)
    for m in re.finditer(r"(?:ประกัน|รับประกัน)[^\n\d]{0,25}?(\d+)\s*(?:\+\s*(\d+))?\s*(ปี|เดือน)", txt):
        out.append((int(m.group(1)) + int(m.group(2) or 0)) * (12 if m.group(3) == "ปี" else 1))
    return out


def num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0)) if m else None


def sold_n(s):
    m = re.search(r"([\d.]+)\s*([Kk])?", str(s or ""))
    return float(m.group(1)) * (1000 if m.group(2) else 1) if m else None


def model_base(model):
    """OCHD802-D2 -> OCHD802 · OSID-LT520 -> OSIDLT520 (ส่วนหน้าถึงตัวเลขชุดแรก, ไม่มีขีด)"""
    m = re.match(r"([A-Z]{2,6}(?:-?[A-Z]{1,3})?-?\d{2,4})", (model or "").upper())
    return mnorm(m.group(1)) if m else mnorm(model)


def resolve_sku(opt, title_models, ref):
    """ชื่อตัวเลือก -> รุ่นในทะเบียน (อัตโนมัติ ไม่แน่ใจ = None)
    title_models = ผลของ find_models(ชื่อสินค้า) ใช้เดารุ่นเมื่อชื่อตัวเลือกเขียนย่อ เช่น "802-D1" / "แบต 2 ก้อน" """
    exact = [ref["mnorm"][mnorm(t)] for t, _, _ in find_models(opt, ref) if mnorm(t) in ref["mnorm"]]
    if exact:
        return exact[0]
    bases = list(dict.fromkeys(model_base(v) for _, v, ok in title_models if ok and v))
    m = re.search(r"(\d{3})-?([NDMKEP]\d?)(?![A-Z0-9])", (opt or "").upper())
    if m:
        for b in [b for b in bases if b.endswith(m.group(1))]:
            hit = ref["mnorm"].get(b + m.group(2))
            if hit:
                return hit
    if len(bases) == 1:
        suf = ("N" if re.search("เปล่า|เฉพาะ", opt or "") else "D2" if re.search(r"2\s*ก้อน", opt or "")
               else "D1" if re.search(r"1\s*ก้อน", opt or "") else None)
        if suf:
            return ref["mnorm"].get(bases[0] + suf)
    return None


# ---------------------------------------------------------------- ชั้น 1: การ์ดบนหน้าค้นหา

def page_medians(items):
    disc = [num(i.get("discount")) or 0 for i in items]
    rev = [int(i["review"]) for i in items if str(i.get("review") or "").isdigit()]
    sold = [x for x in (sold_n(i.get("itemSoldCntShow")) for i in items) if x is not None]
    return {"disc": st.median(disc) if disc else 0, "review": st.median(rev) if rev else 0,
            "sold": st.median(sold) if sold else 0, "n": len(items)}


def score_card(item, pos, med, cover_white=None, query=""):
    """item = รายการจาก catalog/shop AJAX ของ Lazada · pos = อันดับ (None = ไม่ติดในหน้าที่ดู)"""
    name = item.get("name") or ""
    head = name[:45]
    brand = (item.get("brandName") or "").strip()
    d = num(item.get("discount")) or 0
    r = int(item["review"]) if str(item.get("review") or "").isdigit() else 0
    s = sold_n(item.get("itemSoldCntShow")) or 0
    icons = [i.get("bizType") for i in (item.get("icons") or [])]
    rank_note = f"อันดับ {pos}" if pos else f"ไม่ติด {med['n']} อันดับแรก"
    if query:
        rank_note += f" เมื่อค้น “{query}”"
    return layer([
        C("ขึ้นใน 20 อันดับแรก", 6, bool(pos and pos <= 20), rank_note,
          ratio=(1 if pos and pos <= 10 else .67 if pos and pos <= 20 else .33 if pos and pos <= 40 else 0)),
        C("มีแบรนด์ในช่วงแรกของชื่อที่การ์ดโชว์", 3, brand.lower() not in NOBRAND and brand.lower() in head.lower(),
          "ไม่มีแบรนด์ในระบบ" if brand.lower() in NOBRAND else ""),
        C("มีรุ่น/สเปกหลักในช่วงแรกของชื่อ", 3, bool(CODE.search(head.upper()) or SPECV.search(head))),
        C("รูปปกพื้นหลังสะอาด", 4, (cover_white >= 0.6) if cover_white is not None else None,
          f"ขอบรูปสีขาว {round(cover_white * 100)}%" if cover_white is not None else ""),
        C(f"ป้ายส่วนลดไม่น้อยกว่าค่ากลางหน้า ({med['disc']:.0f}%)", 4, d >= med["disc"],
          f"{d:.0f}%" if d else "ไม่มีป้ายส่วนลด", ratio=1 if d >= med["disc"] else (.5 if d >= 10 else 0)),
        C(f"จำนวนรีวิวไม่น้อยกว่าค่ากลางหน้า ({med['review']:.0f})", 3, r >= med["review"], f"{r:,} รีวิว"),
        C("ดาว ≥ 4.8", 2, (num(item.get("ratingScore")) >= 4.8) if item.get("ratingScore") else None,
          f"{num(item.get('ratingScore')):.2f}" if item.get("ratingScore") else ""),
        C(f"ยอดขายที่โชว์ไม่น้อยกว่าค่ากลางหน้า ({med['sold']:,.0f})", 3, s >= med["sold"], item.get("itemSoldCntShow") or ""),
        C("มีป้ายส่งฟรี", 2, "Freeshipping" in icons),
    ], 20)


# ---------------------------------------------------------------- ชั้น 2-4: หน้าสินค้า

def score_pdp(rec, ref):
    """rec = ผลจาก extract_pdp.js (Lazada) · คืน {L2, L3, L4, skus, flags}"""
    master = ref["master"]
    have_ref = bool(master)
    t = rec.get("product_name") or ""
    d = rec.get("description") or ""
    spec = {s.get("name"): str(s.get("value") or "") for s in rec.get("spec") or [] if s.get("name")}
    tm = find_models(t, ref)
    known = [(tok, v) for tok, v, ok in tm if ok]
    unknown = [tok for tok, v, ok in tm if not ok]
    pname = next((p["name"] for p in (portal_of(v, ref) for _, v in known) if p), None) or \
        next((ref["portal"][k]["name"] for n, k in ref["pnorm"].items()
              if any(n.startswith(model_base(v)) for _, v in known)), None)
    ty = ptype(pname) if pname else None
    has_type = bool(ty and (ty in t or (len(ty) > 8 and ty[:6] in t))) or (not ty and bool(re.search("สว่าน|เลื่อย|เจียร|ไขควง|บล็อก|เครื่อง|ปั๊ม|ค้อน|แบตเตอรี่|แท่นชาร์จ", t)))

    rows, var_bad = [], []
    for s in rec.get("skus") or []:
        opt, price, orig = s.get("option_path") or "", s.get("price"), s.get("original_price")
        code = resolve_sku(opt, tm, ref) if have_ref else None
        mi = master.get(code) if code else None
        po = portal_of(code, ref)
        flag = None
        if po and po["min"] and price and price < po["min"]:
            flag = "below"
        elif po and po["max"] and price and price > po["max"]:
            flag = "above"
        rows.append({"opt": opt, "price": price, "orig": orig, "code": code,
                     "status": mi["status"] if mi else None, "pmin": po["min"] if po else None,
                     "pmax": po["max"] if po else None, "flag": flag})
        if mi and mi["status"] == "Discontinued":
            var_bad.append(f"{code} เลิกผลิตแล้ว")
        if mi and re.search("สว่าน", t) and not re.search("สว่าน", mi["name"]):
            var_bad.append(f"{code} ไม่ใช่สว่าน ({mi['name'][:24]}…)")

    imgs = rec.get("images") or []
    uniq = len(dict.fromkeys(imgs)) if rec.get("image_count") is not None else None
    priced = [r for r in rows if r["price"]]
    cheapest = min(priced, key=lambda r: r["price"]) if priced else None
    disc = (1 - cheapest["price"] / cheapest["orig"]) if cheapest and cheapest["orig"] and cheapest["orig"] > cheapest["price"] else 0
    vc = rec.get("video_count")
    L2 = layer([
        C("รูปสินค้า ≥ 9 รูป (ขาดหักตามสัดส่วน)", 12, (uniq >= 9) if uniq is not None else None,
          (f"{uniq} รูป" + ("" if uniq >= 9 else f" · ขาด {9 - uniq}")) if uniq is not None else "เก็บรูปไม่ได้",
          ratio=min(uniq / 9, 1) if uniq is not None else None, main=True),
        C("ชื่อมีแบรนด์", 4, "osuka" in t.lower(), main=True),
        C("ชื่อมีชื่อสินค้า" + (f" (“{ty}” ตาม price list)" if ty else ""), 4, has_type, main=True),
        C("ชื่อมีรุ่น และรุ่นอยู่ในทะเบียนสินค้า", 4, (bool(known) and not unknown) if have_ref else None,
          ("ไม่พบในทะเบียน: " + ", ".join(unknown)) if unknown else (", ".join(tok for tok, _ in known) if known else "ไม่มีรหัสรุ่น")
          if have_ref else "ต่อฐานทะเบียนสินค้าไม่ได้", main=True),
        C("มีวิดีโอสินค้า", 4, (vc > 0) if vc is not None else None, f"{vc} คลิป" if vc is not None else ""),
        C("มีตัวเลือก ≥ 2 แบบ", 2, len(rows) >= 2, f"{len(rows)} แบบ"),
        C("ชื่อตัวเลือกบอกชุด/แบตชัด", 1, all(re.search(r"แบต|เปล่า|ครบ|ชุด|เซ็ท|เฉพาะ|-N|-D|-M|-K", r["opt"]) for r in rows) if rows else None),
        C("แสดงราคาเต็มคู่ราคาขาย", 2, disc > 0,
          (f"ตัวถูกสุด ฿{cheapest['price']:,.0f}" + (f" จาก ฿{cheapest['orig']:,.0f}" if cheapest['orig'] else "")) if cheapest else ""),
        C("ตัวเลือกเป็นรุ่นที่ยังขาย ไม่มีสินค้าอื่นปน", 2, (not var_bad) if have_ref else None, " · ".join(var_bad)),
    ], 35)

    has_d = bool(d.strip())
    has_box = bool(re.search(r"ภายในกล่อง|ในกล่อง|อุปกรณ์ในชุด|ของในชุด|ของแถม", d)) if has_d else None
    spec_lines = len(re.findall(r"^.{0,40}[:：]\s*[\d,.]+", d, re.M))
    has_feat = (bool(re.search(r"คุณสมบัติเด่น|key\s*feature", d, re.I)) or len(re.findall(r"^\s*(🔺|•)\s*\D", d, re.M)) >= 3) if has_d else None
    pend = "รอ price list (ใน Postgres ยังไม่มีช่องนี้)"
    sw = spec.get("ระยะเวลาการรับประกัน", "")
    tm, sm, dm = months(t), months("ประกัน " + sw if sw else ""), months(d)
    allm = sorted(set(tm + sm + dm))
    wnote = " · ".join(x for x in [f"ชื่อ {tm[0]} ด." if tm else "", f"สเปก {sm[0]} ด." if sm else "",
                                  ("คำอธิบาย " + "/".join(f"{v} ด." for v in sorted(set(dm)))) if dm else ""] if x)
    cond = re.search(r"ลงทะเบียน|ศูนย์|เคลม|เงื่อนไข|ใบเสร็จ|บัตรรับประกัน|ประกัน\s*OSUKA|โดยผู้ขาย|โดยผู้ผลิต|ร้าน\S*อีก\s*\d",
                     d + " " + spec.get("ประเภทการรับประกัน", ""), re.I)
    cordless = bool(re.search("ไร้สาย|cordless|แบต", t, re.I))
    bad = []
    if cordless and re.search(r"100\s*-\s*240|กระแสสลับ", spec.get("แรงดันไฟฟ้าที่ใช้", "")):
        bad.append("ช่องสเปกใส่แรงดัน 100–240V AC" + (" (คำอธิบายบอก 20V)" if "20V" in d.upper() else " ในเครื่องไร้สาย"))
    if cordless and "ประเภทปลั๊ก" in spec:
        bad.append("มีช่องประเภทปลั๊ก")
    if re.search("แปรงถ่าน", spec.get("ประเภทแกนแบตเตอรี่", "")):
        bad.append("ใส่ “ไร้แปรงถ่าน” ในช่องชนิดแบตเตอรี่")
    if re.search("การตัด", spec.get("เครื่องมือช่างประเภทอุปกรณ์เสริม", "")) and re.search("สว่าน", t):
        bad.append("สว่านแต่ระบุงาน “การตัด”")
    if re.search(r"ระบบ\s*:\s*2\s*ระบบ\s*\(ขัน,\s*เจาะ,\s*กระแทก\)", d):
        bad.append("คำอธิบายเขียน “2 ระบบ (ขัน, เจาะ, กระแทก)”")
    L3 = layer([
        C("มีรายการของในกล่อง", 3, has_box, "" if has_d else "เก็บคำอธิบายไม่ได้", main=True),
        C("ของในกล่องใช้คำตรงกับ price list", 4, None, pend, main=True, pending=True),
        C("มีสเปกในคำอธิบาย", 3, (spec_lines >= 3) if has_d else None, f"{spec_lines} บรรทัดที่เป็นค่าตัวเลข" if has_d else "", main=True),
        C("สเปกใช้คำตรงกับ price list", 4, None, pend, main=True, pending=True),
        C("มีคีย์ฟีเจอร์", 3, has_feat, main=True),
        C("คีย์ฟีเจอร์ใช้คำตรงกับ price list", 4, None, pend, main=True, pending=True),
        C("ระบุระยะเวลารับประกัน", 3, bool(allm), wnote or "ไม่พบ", main=True),
        C("ระยะเวลารับประกันตรงกันทุกจุด", 3, (len(allm) == 1) if allm else None, wnote, main=True),
        C("บอกเงื่อนไข/ผู้รับประกัน/วิธีเคลม", 2, bool(cond), cond.group(0) if cond else "ไม่พบ", main=True),
        C("สเปกไม่ขัดกับตัวสินค้า", 3, not bad, " · ".join(bad)),
        C("กรอกช่องสเปก ≥ 8 ช่อง", 3, len(spec) >= 8, f"{len(spec)} ช่อง", ratio=min(len(spec) / 8, 1)),
    ], 35)

    rb = rec.get("rating_breakdown") or {}
    n = rec.get("review_count")
    low = ((rb.get("1", rb.get(1, 0)) or 0) + (rb.get("2", rb.get(2, 0)) or 0)) / n if n and rb else None
    rt = rec.get("rating")
    L4 = layer([
        C("คะแนนรีวิว ≥ 4.8", 3, (rt >= 4.8) if rt is not None else None, f"{rt} ดาว" if rt is not None else "ยังไม่มีรีวิว/เก็บไม่ได้"),
        C("รีวิว ≥ 100 รายการ", 3, (n >= 100) if n is not None else None, f"{n:,} รีวิว" if n is not None else "",
          ratio=min(n / 100, 1) if n else None),
        C("รีวิว 1–2 ดาว ≤ 2%", 2, (low <= .02) if low is not None else None, f"{low:.1%}" if low is not None else ""),
    ], 10)
    return {"L2": L2, "L3": L3, "L4": L4, "skus": rows, "below": sum(1 for r in rows if r["flag"] == "below"),
            "portal_name": pname, "unique_images": uniq}


def total(L1, L2, L3, L4):
    parts = [x["score"] for x in (L1, L2, L3, L4) if x and x["score"] is not None]
    return round(sum(parts))


def quick_title_check(name, price, ref):
    """ตรวจเร็วจากการ์ดในร้าน (ไม่เปิดหน้าสินค้า): ชื่อครบ 3 อย่าง + ราคาเทียบ price list"""
    tm = find_models(name, ref)
    known = [tok for tok, v, ok in tm if ok]
    unknown = [tok for tok, v, ok in tm if not ok]
    # ราคาการ์ด = ตัวเลือกที่ถูกสุด -> เทียบกับ "ขั้นต่ำที่ถูกที่สุด" ของทุกรุ่นย่อยที่ชื่อพูดถึง (ธงนี้จึงไม่ฟ้องเกิน)
    bases = {model_base(v) for tok, v, ok in tm if ok}
    ports = [ref["portal"][k] for n, k in ref["pnorm"].items() if any(n.startswith(b) for b in bases)]
    pn = next((p["name"] for p in ports), None)
    ty = ptype(pn) if pn else None
    mins = [p["min"] for p in ports if p["min"]]
    return {"brand": "osuka" in (name or "").lower(),
            "type": bool(ty and (ty in name or (len(ty) > 8 and ty[:6] in name))) or (not ty and bool(re.search("สว่าน|เลื่อย|เจียร|ไขควง|บล็อก|เครื่อง|ปั๊ม|ค้อน|แบต", name or ""))),
            "model": bool(known) and not unknown, "unknown": unknown, "known": known,
            "below": bool(mins and price and price < min(mins)), "pmin": min(mins) if mins else None}
