"""แปลงผลของ scorecard.py -> ชุดคำสั่งเขียน db ของหน้า Scorecard (ArtifactData batch ครั้งละ ≤ 50)

    python scorecard_batches.py output/scorecard/<request-id> [--input "<ลิงก์/ชื่อร้าน>" --kind url|shop --created-at ISO]

เขียน <dir>/request.json (สถานะคำขอ) แล้วพิมพ์ batches เป็น JSON:
  [[{op, collection, doc_id, file_path}, ...], ...]
ลำดับ: รูปก่อน -> ผลสินค้า -> ร้าน -> คำขอ (หน้าเว็บเห็นคำว่า "เสร็จ" เมื่อข้อมูลครบแล้วเท่านั้น)
"""
import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--input")
    ap.add_argument("--kind")
    ap.add_argument("--created-at")
    ap.add_argument("--request-version", type=int, help="version ของเอกสารคำขอที่อ่านมา — db ไม่ยอมเขียนทับเอกสารเดิมถ้าไม่ pin")
    a = ap.parse_args()
    d = Path(a.dir).resolve()
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))

    seen, imgs, results, shops = set(), [], [], []
    for e in m:
        k = (e["collection"], e["doc_id"])
        if k in seen:
            continue
        seen.add(k)
        w = {"op": "set", "collection": e["collection"], "doc_id": e["doc_id"], "file_path": Path(e["file"]).as_posix()}
        (imgs if "/imgs" in e["collection"] else shops if e["collection"] == "shops" else results).append(w)

    totals = [r.get("total") for r in s.get("results") or [] if r.get("id")]
    req = {"status": s.get("status", "error"), "started_at": s.get("started_at"), "finished_at": s.get("finished_at"),
           "result_ids": s.get("result_ids") or [], "note": s.get("note") or "", "platform": "lazada"}
    if s.get("shop_id"):
        req["shop_id"] = s["shop_id"]
    if len(totals) == 1 and s.get("kind") != "shop":
        req["total"] = totals[0]
    if a.input:
        req["input"] = a.input
    if a.kind:
        req["kind"] = a.kind
    if a.created_at:
        req["created_at"] = a.created_at
    (d / "request.json").write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    reqw = {"op": "update" if not a.input else "set", "collection": "requests", "doc_id": s["request_id"],
            "file_path": (d / "request.json").as_posix()}
    if a.request_version:
        reqw["if_version"] = a.request_version

    seq = imgs + results + shops + [reqw]
    # ArtifactData batch: ≤ 50 รายการ และ ≤ 1 MiB ต่อคำขอ (รูปคำอธิบายก้อนละ ~50 KB) — ตัดที่ 850 KB กันขอบ
    batches, cur, size = [], [], 0
    for w in seq:
        sz = Path(w["file_path"]).stat().st_size + 300
        if cur and (len(cur) >= 50 or size + sz > 850_000):
            batches.append(cur)
            cur, size = [], 0
        cur.append(w)
        size += sz
    if cur:
        batches.append(cur)
    print(json.dumps(batches, ensure_ascii=False))


if __name__ == "__main__":
    main()
