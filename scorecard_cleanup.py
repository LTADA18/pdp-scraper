"""ลบผลตรวจรอบเก่าของร้านเดียวกันออกจาก db ของหน้า Scorecard — เก็บไว้เฉพาะรอบล่าสุดของแต่ละร้าน

    python scorecard_cleanup.py <snapdir> [--versions v.json]      -> พิมพ์แผนลบ (JSON)
    python scorecard_cleanup.py --mark <plan.json> [--only 0,2]     -> จดว่าชุดไหนลบสำเร็จแล้ว

<snapdir> = โฟลเดอร์ที่ ArtifactData "list" collection "shops" (out_dir) บันทึกไว้ -> <snapdir>/shops/*.json
แผน: {"summary": {...}, "batches": [[{op:"delete", collection, doc_id, if_version}, ...], ...]}

ทำไมต้องมี: db ของหน้าเว็บจุ 5,000 เอกสาร ตรวจซ้ำได้เอกสารชุดใหม่ทุกครั้ง (ไม่ทับของเดิม)
3 ร้านแรก (614 หน้า) ใช้ไป 1,431 แล้ว — เจ้าของงานสั่งให้ลบรอบเก่าอัตโนมัติ เก็บเฉพาะรอบล่าสุด (2026-09-26)

⚠️ db ลบแบบไม่ pin ได้เฉพาะเอกสารที่ "ไม่มีอยู่" · ลบเอกสารที่มีอยู่ต้อง pin if_version ตรงเป๊ะ
   และ pin เอกสารที่ไม่มีอยู่ก็ล้มทั้งชุด (ทดสอบจริง 2026-09-26) -> ต้องรู้แน่ว่าเอกสารไหนมีอยู่ เวอร์ชันอะไร
   ทะเบียนจึงเอาจาก manifest.json ของรอบที่ status "done" เท่านั้น (= ชุดที่ส่งขึ้น db จริง)
   ทุกเอกสารเขียนด้วย set ครั้งเดียว = version 1 · ที่ไม่ใช่ 1 ให้ใส่ใน --versions {"collection/doc_id": N}
   ลบแล้วจดใน output/scorecard/_deleted.json ด้วย --mark ไม่งั้นรอบหน้าจะ pin ของที่หายไปแล้วจนล้มทั้งชุด

กติกา (ลบเฉพาะที่แน่ใจว่าถูกแทนแล้ว):
- ร้าน: slug เดียวกันมีหลายรอบ -> เก็บรอบล่าสุด ลบ shops/<sid> + shops/<sid>/thumbs/*
- ผลสินค้า: ลบเมื่อ "สินค้านั้นอยู่ในร้านรอบล่าสุด และรอบล่าสุดชี้ไปผลอื่นแล้ว" เท่านั้น
  -> cards/<rid> + results/<rid> + results/<rid>/imgs/*   ผลที่ตรวจทีละลิงก์ (ไม่อยู่ในร้านไหน) ไม่แตะ
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "scorecard"
LEDGER = OUT / "_deleted.json"


def load(folder):
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))}


def ledger():
    return set(json.loads(LEDGER.read_text(encoding="utf-8"))) if LEDGER.exists() else set()


def registry():
    """(collection, doc_id) ทุกอันที่เคยส่งขึ้น db จริง"""
    reg = set()
    for d in OUT.iterdir():
        sm, mf = d / "summary.json", d / "manifest.json"
        if not (d.is_dir() and sm.exists() and mf.exists()):
            continue
        if json.loads(sm.read_text(encoding="utf-8")).get("status") != "done":
            continue                        # รอบที่ล้ม/partial ไม่เคยถูกส่งขึ้น db
        reg |= {(e["collection"], e["doc_id"]) for e in json.loads(mf.read_text(encoding="utf-8"))}
    # รอบทดสอบแรก ๆ (2026-09-25) เติมการ์ด/รูปย่อด้วยมือ ไม่มี manifest
    reg |= {("cards", p.stem[len("card_"):]) for p in (OUT / "backfill-cards").glob("card_*.json")}
    reg |= {("shops/lzd-tby-toolscenter/thumbs", p.stem.split("_")[1])
            for p in (OUT / "fill-thumbs-tby").glob("thumbs_t*.json")}
    return reg


def plan(snap, versions):
    shops = load(snap / "shops")
    reg, gone = registry(), ledger()

    by_slug = {}
    for sid, s in shops.items():
        by_slug.setdefault(s.get("slug") or sid, []).append(sid)
    keep, old = set(), []
    for sids in by_slug.values():
        sids.sort(key=lambda x: (shops[x].get("scraped_at") or "", x))
        keep.add(sids[-1])
        old += sids[:-1]

    keep_rids, covered = set(), set()
    for sid in keep:
        for r in shops[sid].get("rows") or []:
            covered.add(str(r.get("id")))
            if r.get("result_id"):
                keep_rids.add(r["result_id"])

    rids = {d for c, d in reg if c in ("cards", "results")}
    drop = sorted(r for r in rids if r not in keep_rids and r.split("-")[1] in covered)

    targets = []
    for rid in drop:                                   # การ์ดก่อน — หน้าเว็บเลิกแสดงทันที
        if ("cards", rid) in reg:
            targets.append(("cards", rid))
    for rid in drop:
        targets += sorted(k for k in reg if k[0] == f"results/{rid}/imgs")
        if ("results", rid) in reg:
            targets.append(("results", rid))
    for sid in old:                                    # ร้านเห็นใน snapshot = มีอยู่จริงแน่นอน
        targets += sorted(k for k in reg if k[0] == f"shops/{sid}/thumbs")
        targets.append(("shops", sid))

    ops = [{"op": "delete", "collection": c, "doc_id": d, "if_version": versions.get(f"{c}/{d}", 1)}
           for c, d in targets if f"{c}/{d}" not in gone]
    batches = [ops[i:i + 50] for i in range(0, len(ops), 50)]
    return {"summary": {"shops_kept": sorted(keep), "shops_dropped": old, "results_dropped": len(drop),
                        "delete_ops": len(ops), "batches": len(batches)}, "batches": batches}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("snapdir", nargs="?")
    ap.add_argument("--versions", help='JSON {"collection/doc_id": version} สำหรับเอกสารที่ไม่ใช่ version 1')
    ap.add_argument("--mark", help="plan.json ที่ส่งแล้ว — จดลง _deleted.json")
    ap.add_argument("--only", help="ดัชนี batch ที่สำเร็จ เช่น 0,2 (ไม่ใส่ = ทุกชุด)")
    a = ap.parse_args()
    if a.mark:
        p = json.loads(Path(a.mark).read_text(encoding="utf-8"))
        idx = [int(i) for i in a.only.split(",")] if a.only else range(len(p["batches"]))
        done = ledger() | {f'{o["collection"]}/{o["doc_id"]}' for i in idx for o in p["batches"][i]}
        LEDGER.write_text(json.dumps(sorted(done), ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"จดแล้ว {len(done)} เอกสารใน {LEDGER}")
        return
    versions = json.loads(Path(a.versions).read_text(encoding="utf-8")) if a.versions else {}
    print(json.dumps(plan(Path(a.snapdir), versions), ensure_ascii=False))


if __name__ == "__main__":
    main()
