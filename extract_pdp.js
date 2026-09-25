/* ============================================================
   extract_pdp.js  —  Universal PDP extractor (Lazada / Shopee / TikTok Shop, TH)
   รันผ่าน Claude in Chrome -> javascript_tool บนหน้า product detail page
   คืนค่า JSON เดียวกันทุกแพลตฟอร์ม:
     product_name, price, original_price, spec, variation, platform
     + images / image_count / video_count / rating / review_count / rating_breakdown (เพิ่ม 2026-09-25)

   วิธีใช้:
     1) navigate ไปหน้าสินค้า
     2) javascript_tool: วางไฟล์นี้ทั้งไฟล์  ->  ได้ Promise ที่ resolve เป็น object
     3) ถ้า tool ไม่ await ให้เรียกซ้ำอีกครั้งด้วย:  window.__PDP_RESULT__
   ============================================================ */
(() => {
  'use strict';

  // ---------- utils ----------
  const num = (v) => {
    if (v === null || v === undefined) return null;
    if (typeof v === 'number') return isFinite(v) ? v : null;
    let s = String(v).trim().replace(/[฿$,\s]/g, '').replace(/THB/i, '');
    const m = s.match(/(-?\d+(?:\.\d+)?)\s*([KkMm])?/);
    if (!m) return null;
    let n = parseFloat(m[1]);
    if (m[2]) n *= (m[2].toUpperCase() === 'K') ? 1e3 : 1e6;
    return isFinite(n) ? n : null;
  };

  const txt = (sel) => {
    const el = document.querySelector(sel);
    return el ? el.textContent.trim().replace(/\s+/g, ' ') : null;
  };

  // DOM/CSSOM object ไม่ใช่ data — และการอ่าน property บางตัว (เช่น cssRules ของ stylesheet ข้าม origin)
  // throw SecurityError ทันที ต้องกันไม่ให้ deepFind เดินเข้าไป
  const isDomLike = (v) => {
    try {
      return (typeof Node === 'function' && v instanceof Node)
          || (typeof Window === 'function' && v instanceof Window)
          || (typeof CSSStyleSheet === 'function' && v instanceof CSSStyleSheet)
          || (typeof StyleSheetList === 'function' && v instanceof StyleSheetList)
          || (typeof CSSRuleList === 'function' && v instanceof CSSRuleList);
    } catch (e) { return true; }        // เช็คไม่ได้ = ถือว่าอันตราย ข้ามไป
  };

  // เดินลง object แบบ recursive หา node แรกที่ผ่านเงื่อนไข (ใช้กับ state ที่ schema เปลี่ยนบ่อย)
  const deepFind = (root, pred, maxDepth = 12) => {
    const seen = new Set();
    const stack = [[root, 0]];
    while (stack.length) {
      const [node, d] = stack.pop();
      if (!node || typeof node !== 'object' || d > maxDepth) continue;
      if (seen.has(node)) continue;
      seen.add(node);
      try { if (pred(node)) return node; } catch (e) { /* ignore */ }
      let keys;
      try { keys = Object.keys(node); } catch (e) { continue; }
      for (const k of keys) {
        let v;
        try { v = node[k]; } catch (e) { continue; }   // getter ที่ throw (SecurityError ฯลฯ)
        if (v && typeof v === 'object' && !isDomLike(v)) stack.push([v, d + 1]);
      }
    }
    return null;
  };

  // รูปของ Lazada/TikTok บางตัวมาเป็น //host/path ไม่มี scheme
  const absUrl = (u) => (typeof u === 'string' && u) ? (u.startsWith('//') ? 'https:' + u : u) : null;

  const sleep = (ms) => new Promise(res => setTimeout(res, ms));

  // คำอธิบายสินค้าเป็น HTML — แปลงเป็นข้อความโดยเก็บการขึ้นบรรทัดไว้ (ร้านมักเขียนสเปกเป็นรายการ)
  // และคืนรูปในคำอธิบายแยกออกมา (หลายร้านใส่ตารางสเปกเป็นรูป)
  const htmlToText = (html) => {
    if (typeof html !== 'string' || !html.trim()) return { text: null, images: [] };
    const images = [...html.matchAll(/<img[^>]+src\s*=\s*["']([^"']+)["']/gi)].map(m => absUrl(m[1])).filter(Boolean);
    let s = html.replace(/<(br|\/p|\/li|\/div|\/h\d|\/tr)\b[^>]*>/gi, '\n').replace(/<li\b[^>]*>/gi, '• ')
                .replace(/<[^>]+>/g, '');
    try {
      const ta = document.createElement('textarea'); ta.innerHTML = s; s = ta.value;   // ถอด &amp; &nbsp; ฯลฯ
    } catch (e) { s = s.replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&'); }
    const text = s.split('\n').map(x => x.replace(/[ \t ]+/g, ' ').trim()).filter(Boolean).join('\n');
    return { text: text || null, images: [...new Set(images)] };
  };

  // จำนวนรีวิวแยกดาว 5 ช่อง — แต่ละเว็บเรียงไม่เหมือนกัน (1→5 หรือ 5→1)
  // ไม่เดาลำดับ: ลองทั้งสองแบบ แล้วเลือกแบบที่คิดค่าเฉลี่ยออกมาใกล้คะแนนที่เว็บแสดง
  // ถ้าเว็บไม่ให้คะแนนเฉลี่ยมาเทียบ หรือทั้งสองแบบห่างเกิน 0.3 ดาว -> คืน null ไม่ใส่มั่ว
  const starBreakdown = (counts, avg) => {
    if (!Array.isArray(counts) || counts.length !== 5) return null;
    const c = counts.map(num);
    if (c.some(x => x === null)) return null;
    const total = c.reduce((a, b) => a + b, 0);
    if (!total) return { 5: 0, 4: 0, 3: 0, 2: 0, 1: 0 };
    if (avg === null || avg === undefined) return null;
    const asc = c.reduce((a, n, i) => a + n * (i + 1), 0) / total;    // [1★..5★]
    const desc = c.reduce((a, n, i) => a + n * (5 - i), 0) / total;   // [5★..1★]
    const da = Math.abs(asc - avg), dd = Math.abs(desc - avg);
    if (Math.min(da, dd) > 0.3) return null;
    return dd <= da
      ? { 5: c[0], 4: c[1], 3: c[2], 2: c[3], 1: c[4] }
      : { 5: c[4], 4: c[3], 3: c[2], 2: c[1], 1: c[0] };
  };

  const jsonLd = () => {
    const out = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
      try {
        const j = JSON.parse(s.textContent);
        (Array.isArray(j) ? j : [j]).forEach(x => out.push(x));
      } catch (e) { /* ignore */ }
    });
    return out.find(x => x && (x['@type'] === 'Product' || x['@type'] === 'product')) || null;
  };

  // ตรวจว่าที่โหลดมาเป็นหน้า anti-bot / CAPTCHA แทนหน้าสินค้า (TikTok เจอบ่อยสุด)
  const captchaReason = () => {
    const title = document.title || '';
    if (/security check|captcha|ยืนยันตัวตน|punish/i.test(title)) return 'หน้า anti-bot: "' + title + '"';
    // element: TikTok captcha + Lazada/Alibaba slider (nc_) + Google reCAPTCHA
    if (document.querySelector('#captcha-verify-image, .captcha_verify_container, [class*="captcha_verify"], [id^="captcha"], .nc_iconfont, .nc-lang-cnt, [id*="nc_1_"], [class*="slidecaptcha"], [class*="nocaptcha"], .g-recaptcha, [class*="g-recaptcha"], iframe[src*="recaptcha"]'))
      return 'พบ CAPTCHA element ในหน้า';
    const body = ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ').trim();
    // ข้อความ reCAPTCHA เจาะจง (ตามที่ Lazada เด้ง) — ไม่จำกัดความยาว body
    if (/ตรวจสอบว่าคุณเป็นหุ่นยนต์|ไม่ใช่โปรแกรมอัตโนมัติ|เป็นหุ่นยนต์หรือไม่|i'?m not a robot|recaptcha/i.test(body))
      return 'หน้า reCAPTCHA (ยืนยันไม่ใช่บอท)';
    if (body.length < 600 && /verify to continue|drag the puzzle|ลากชิ้นส่วน|ยืนยันเพื่อดำเนินการต่อ|slide to verify|เลื่อนเพื่อยืนยัน|please slide/i.test(body))
      return 'หน้ายืนยันตัวตน (slider): "' + body.slice(0, 80) + '"';
    return null;
  };

  const base = () => ({
    platform: null,
    product_id: null,
    shop_id: null,
    shop_name: null,        // ชื่อร้านค้า
    url: location.href,
    product_name: null,
    currency: 'THB',
    price: null,            // ราคาขายปัจจุบัน (variant ต่ำสุด)
    price_max: null,
    original_price: null,
    spec: [],               // [{name, value}]
    variation: [],          // [{name, options:[..]}]
    skus: [],               // [{sku_id, option_path, price, original_price, stock}]
    sold_count: null,       // ยอดขายสะสม (เลขเป๊ะ) — ใช้ diff รายวัน; Lazada PDP ไม่มี
    sold_band: null,        // ข้อความยอดขายแบบช่วง เช่น "3k+" (ไว้อ้างอิง)
    // ---- ใช้กับ Listing Scorecard ----
    // null = หา field ไม่เจอ/ยังไม่โหลด (ไม่รู้)  ≠  0 = รู้แน่ว่าไม่มี — ห้ามเอา null ไปนับเป็น 0
    images: [],             // URL รูปหลักของสินค้า เรียงตามหน้าเว็บ ตัวแรก = รูปปก (ไม่รวมรูปแยกตามตัวเลือก)
    image_count: null,
    video_count: null,      // วิดีโอในแกลเลอรีสินค้า (ไม่นับคลิปครีเอเตอร์/ไลฟ์)
    rating: null,           // คะแนนเฉลี่ย 0–5 — ยังไม่มีรีวิวเลย = null ไม่ใช่ 0
    review_count: null,     // จำนวนเรตติ้งทั้งหมด
    rating_breakdown: null, // {5: n, 4: n, 3: n, 2: n, 1: n}
    description: null,      // ข้อความคำอธิบายสินค้า (ตัด HTML แล้ว เก็บการขึ้นบรรทัด) — ไม่พบ = null
    description_images: [], // รูปในคำอธิบาย (บางร้านใส่ตารางสเปกเป็นรูป)
    source: null,           // state | api | jsonld | dom
    scraped_at: new Date().toISOString(),
    warnings: []
  });

  // extractor พังกลางทาง (เช่น TikTok patch fetch/CSSOM แล้ว throw SecurityError)
  // -> คืน record พร้อมเหตุผล ดีกว่าปล่อยให้ทั้งรายการหายไปจาก Excel
  const hardFail = (e) => {
    const r = base();
    const h = location.hostname;
    r.platform = /lazada\./.test(h) ? 'lazada' : /shopee\./.test(h) ? 'shopee'
              : /tiktok/.test(h) ? 'tiktok' : null;
    const m = location.href.match(/\/product\/(\d{6,})|-i(\d+)/);
    r.product_id = m ? (m[1] || m[2]) : null;
    r.source = 'blocked';
    r.warnings.push('extractor พังกลางทาง: ' + ((e && e.message) ? e.message : String(e)));
    try { const c = captchaReason(); if (c) r.warnings.push(c); } catch (e2) { /* ignore */ }
    return r;
  };

  // ============================================================
  // LAZADA  — window.__moduleData__ .data.root.fields
  // ============================================================
  const lazada = async () => {
    const r = base();
    r.platform = 'lazada';
    const mid = location.href.match(/-i(\d+)(?:-s(\d+))?\.html/);
    r.product_id = mid ? mid[1] : null;

    // โดน CAPTCHA/slider ของ Lazada = ไม่มี state ให้แกะ ตรวจก่อน ไม่งั้นจะนึกว่า schema เปลี่ยน/สินค้าถูกลบ
    if (!window.__moduleData__) {
      const lzBlocked = captchaReason();
      if (lzBlocked) {
        r.source = 'blocked';
        r.warnings.push('lazada: ' + lzBlocked + ' — โดน CAPTCHA (พัก/ผ่านเองก่อน)');
        return r;
      }
    }

    const md = window.__moduleData__ || window.__INITIAL_STATE__ || null;
    const findFields = () => md && deepFind(md, o => o.product && (o.skuBase || o.skuInfos));
    let f = findFields();

    // review / seller / specifications มาทีหลังด้วย CSR (ยืนยัน 2026-09-25: ตอนหน้าเพิ่งโหลด
    // root.fields มีแค่ product/skuGalleries/skuInfos) — รอสูงสุด 8 วิ ไม่งั้น rating เป็น null ทั้งแผง
    if (f && !f.review) {
      try { window.scrollTo(0, (document.body.scrollHeight || 2000) / 2); } catch (e) { /* ignore */ }
      for (let i = 0; i < 16 && !(f && f.review); i++) { await sleep(500); f = findFields() || f; }
    }

    if (f) {
      r.source = 'state';
      r.product_name = (f.product && (f.product.title || f.product.name)) || null;

      // ---- ชื่อร้าน ----
      if (f.seller) {
        r.shop_name = f.seller.name || null;
        r.shop_id = String(f.seller.sellerId || f.seller.shopId || r.shop_id || '') || null;
      }
      // ยอดขายรายสินค้าไม่มีใน PDP — scrape_pdp.py เติมให้จากหน้า search (--lazada-sold)

      // skuBase ย้ายไปอยู่ใต้ productOption แล้ว และคีย์ชื่อ properties (ของเดิมคือ props)
      const skuBase = (f.productOption && f.productOption.skuBase) || f.skuBase || {};
      const props = skuBase.properties || skuBase.props || [];

      // ---- map pid:vid -> ชื่อ option ที่อ่านออก ----
      const vidMap = {};
      props.forEach(p =>
        (p.values || []).forEach(v => { vidMap[`${p.pid}:${v.vid}`] = v.name; }));
      const readPath = (pp) => pp
        ? pp.split(';').map(x => vidMap[x] || x).filter(Boolean).join(' / ')
        : null;

      // ---- variation ----
      r.variation = props.map(p => ({
        name: p.name,
        options: (p.values || []).map(v => v.name).filter(Boolean)
      })).filter(v => v.name);

      // ---- ราคา: propPath อยู่ใน skuBase.skus ส่วนราคาอยู่ใน skuInfos ต้อง join ด้วย skuId ----
      const infos = f.skuInfos || {};
      const infoOf = (id) => {
        const v = infos[id];
        return (Array.isArray(v) ? v[0] : v) || null;
      };
      // skuInfos มีคีย์ "0" เป็นค่า default ของหน้า ไม่ใช่ sku จริง — ใช้ skuBase.skus เป็นตัวตั้ง
      let list = (skuBase.skus || []).map(s => ({ id: String(s.skuId), path: readPath(s.propPath) }));
      if (!list.length) {
        list = Object.keys(infos).filter(k => k !== '0').map(k => ({ id: k, path: null }));
      }

      const prices = [];
      r.skus = list.map(({ id, path }) => {
        const p = (infoOf(id) || {}).price || {};
        const sp = num(p.salePrice && (p.salePrice.value ?? p.salePrice.text));
        const op = num(p.originalPrice && (p.originalPrice.value ?? p.originalPrice.text));
        if (sp !== null) prices.push(sp);
        return {
          sku_id: id,
          option_path: path,
          price: sp,
          original_price: op,
          // quantity ในหน้านี้คือลิมิตการสั่งซื้อ ({limit:{max,min}}) ไม่ใช่สต็อกคงเหลือ
          // state ไม่มีจำนวนคงเหลือจริง -> เว้นว่างตามกฎข้อ 1 ห้ามเดาตัวเลข
          stock: null
        };
      });

      // price/original_price ต้องมาจาก sku ตัวเดียวกัน (ตัวถูกสุด) ไม่งั้น discount_pct เพี้ยน
      let cheapest = null;
      r.skus.forEach(s => {
        if (s.price !== null && (cheapest === null || s.price < cheapest.price)) cheapest = s;
      });
      if (cheapest) { r.price = cheapest.price; r.original_price = cheapest.original_price; }
      if (prices.length) r.price_max = Math.max(...prices);

      // ---- spec: { <skuId>: { features: {ชื่อ: ค่า} } } — รวมเป็น spec ระดับสินค้า ----
      const specSrc = f.specifications || (f.productDesc && f.productDesc.attributes) || f.attributes;
      if (Array.isArray(specSrc)) {
        r.spec = specSrc.map(x => ({ name: x.name || x.key, value: x.value ?? x.values })).filter(x => x.name);
      } else if (specSrc && typeof specSrc === 'object') {
        const seenName = new Set();
        Object.values(specSrc).forEach(entry => {
          const feats = (entry && entry.features) || null;
          if (!feats || typeof feats !== 'object') return;
          Object.keys(feats).forEach(k => {
            const v = feats[k];
            if (k === 'SKU') return;                       // ต่างกันทุก variant ไม่ใช่ spec ของสินค้า
            if (v === null || v === undefined || typeof v === 'object') return;
            if (seenName.has(k)) return;
            seenName.add(k);
            r.spec.push({ name: k, value: String(v) });
          });
        });
      }

      // ---- รูป / วิดีโอ: skuGalleries["0"] = แกลเลอรีระดับสินค้า ----
      // คีย์อื่นเป็นแกลเลอรีต่อ sku (ซ้ำกับตัวหลัก) ไม่นับ
      // ยืนยันกับหน้าจริง 2026-09-25: รูป = {type:"img", src}, วิดีโอ = {type:"video", src:YouTube} หรือ {type:"video", videoID}
      const gal = f.skuGalleries || null;
      const itemGal = gal && (Array.isArray(gal['0']) ? gal['0'] : Object.values(gal).find(Array.isArray));
      if (itemGal) {
        r.images = [...new Set(itemGal.filter(x => x && x.type === 'img' && x.src).map(x => absUrl(x.src)))];
        r.image_count = r.images.length;
        r.video_count = itemGal.filter(x => x && /video/i.test(x.type || '')).length;
      } else {
        r.warnings.push('lazada: ไม่พบ skuGalleries — images/video_count เป็น null');
      }

      // ---- คำอธิบาย: product.desc (HTML) + highlights (จุดเด่นแบบรายการ) — มากับ SSR ไม่ต้องรอ ----
      if (f.product && (f.product.desc || f.product.highlights)) {
        const hl = htmlToText(f.product.highlights), ds = htmlToText(f.product.desc);
        r.description = [hl.text, ds.text].filter(Boolean).join('\n\n') || null;
        r.description_images = [...new Set([...hl.images, ...ds.images])];
      } else {
        r.warnings.push('lazada: ไม่พบคำอธิบายสินค้า (product.desc) — description เป็น null');
      }

      // ---- รีวิว: {averageRating, reviews, scores:[5★..1★]} (ยืนยัน 2026-09-25: [72,2,0,1,1] เฉลี่ย 4.9) ----
      const rv = f.review || null;
      if (rv && (rv.averageRating !== undefined || rv.reviews !== undefined)) {
        r.review_count = num(rv.reviews ?? rv.ratings ?? rv.contentedNum);
        r.rating = r.review_count ? num(rv.averageRating) : null;
        r.rating_breakdown = starBreakdown(rv.scores, r.rating);
      } else {
        r.warnings.push('lazada: รีวิวยังไม่โหลด (มาทีหลังด้วย CSR) — rating/review_count เป็น null');
      }
    }

    // สินค้าถูกลบ/ปิดการขาย: Lazada เสิร์ฟหน้า "no longer available" ไม่มี state — บอกให้ชัด ไม่ใช่บั๊ก
    // ต้องรองรับ title ภาษาไทยด้วย ("ขออภัยค่ะ! ไม่พบรายการสินค้าชิ้นนี้") ไม่งั้นจะตกไปเป็น
    // source=dom แล้วถูกส่งเข้า redo วนเก็บซ้ำฟรีทุกรอบทั้งที่สินค้าหายถาวรแล้ว
    if (!r.product_name && /no longer available|product is no longer|ไม่พร้อมจำหน่าย|ไม่พบรายการสินค้า|ขออภัย.*ไม่พบ|page not found/i.test(document.title || '')) {
      r.source = 'blocked';
      r.warnings.push('lazada: สินค้าถูกลบ/ปิดการขายแล้ว ("' + (document.title || '').slice(0, 50) + '")');
      return r;
    }

    // ---- fallback DOM ----
    if (!r.product_name) {
      r.source = r.source || 'dom';
      r.product_name = txt('.pdp-mod-product-badge-title') || txt('h1');
      r.price = r.price ?? num(txt('.pdp-price_type_normal') || txt('.pdp-price'));
      r.original_price = r.original_price ?? num(txt('.pdp-price_type_deleted'));
      if (!r.variation.length) {
        document.querySelectorAll('.sku-prop').forEach(g => {
          const name = (g.querySelector('.section-title') || {}).textContent;
          const options = [...g.querySelectorAll('.sku-variable-name, .sku-variable-img-wrap img')]
            .map(e => (e.textContent || e.alt || '').trim()).filter(Boolean);
          if (name) r.variation.push({ name: name.replace(/[:：]\s*$/, '').trim(), options });
        });
      }
      if (!r.spec.length) {
        document.querySelectorAll('.pdp-mod-specification .key-li').forEach(li => {
          const k = li.querySelector('.key-title'), v = li.querySelector('.key-value');
          if (k && v) r.spec.push({ name: k.textContent.trim(), value: v.textContent.trim() });
        });
      }
      // ลิงก์สั้น s.lazada.co.th บางตัวพาไปหน้าไลฟ์/แคมเปญ ไม่ใช่หน้าสินค้า — บอกให้ชัด ไม่ใช่ selector พัง
      if (!/-i\d+/.test(location.href)) {
        r.warnings.push('lazada: ปลายทางหลัง redirect ไม่ใช่หน้าสินค้า (' + location.pathname.slice(0, 60) + ')');
      } else {
        r.warnings.push('lazada: อ่านจาก DOM (state ไม่พบ) — ตรวจค่าก่อนใช้');
      }
    }
    return r;
  };

  // ============================================================
  // SHOPEE — เรียก internal API แบบ same-origin (ใช้ cookie ของ session ที่ล็อกอินอยู่)
  // ============================================================
  const shopee = async () => {
    const r = base();
    r.platform = 'shopee';

    // โหมดยิง API อย่างเดียว: สคริปต์ตั้ง window.__PDP_TARGET__ ให้ แล้วเรียกจากหน้าไหนก็ได้
    // (ไม่ต้องเปิดหน้า PDP ทีละตัว = ตัด request ของหน้าเว็บทิ้งทั้งหมด กันโดน anti-bot)
    const T = (typeof window !== 'undefined' && window.__PDP_TARGET__) || null;
    let m = null;
    if (T && T.shop_id && T.item_id) {
      r.shop_id = String(T.shop_id); r.product_id = String(T.item_id); m = true;
      r.url = 'https://shopee.co.th/product/' + r.shop_id + '/' + r.product_id;
    } else {
      m = location.href.match(/i\.(\d+)\.(\d+)/) || location.href.match(/\/product\/(\d+)\/(\d+)/);
      if (m) { r.shop_id = m[1]; r.product_id = m[2]; }
    }
    if (!m) { r.warnings.push('shopee: อ่าน shop_id/item_id จาก URL ไม่ได้'); return r; }

    const D = 100000; // Shopee เก็บราคาเป็นหน่วย 1/100000
    const endpoints = [
      `/api/v4/pdp/get_pc?shop_id=${r.shop_id}&item_id=${r.product_id}&detail_level=0`,
      `/api/v4/item/get?itemid=${r.product_id}&shopid=${r.shop_id}`
    ];

    // จำสถานะการเรียก API ครั้งล่าสุดไว้ตัดสินว่า "สินค้าถูกลบ" หรือ "โดน anti-bot"
    // (API ตอบ 200 + error code + data:null = สินค้าหายแล้ว ไม่ใช่โดนกัน — ต่างกันมาก
    //  เพราะโดนกัน = พักแล้วลองใหม่ได้ ส่วนถูกลบ = ใส่ deadlist เลย)
    // NOT_FOUND: code ที่ยืนยันแล้วว่าแปลว่า "ไม่มีสินค้านี้" (ไม่ใช่โดนกัน)
    //   266900002 = get_pc ตอบว่าไม่พบ (ยืนยัน: item_id ปลอมได้ code นี้ + หน้าเว็บไม่มีชื่อ/ราคา)
    // ต้องดู **ทุก endpoint** ไม่ใช่แค่ตัวสุดท้าย: get_pc ตอบ 266900002 แล้ว item/get ตอบ 4
    //   ถ้าจำแค่ตัวท้าย จะเห็นเป็น error 4 -> ตีว่าโดน anti-bot -> สั่งพักยาวฟรีกับลิงก์ตาย
    const NOT_FOUND = [266900002];
    const lastApi = { answered: false, error: null, notFound: false };
    // เก็บ data ทั้งก้อนของทุก endpoint ไว้ด้วย — get_pc วางรูป/รีวิวไว้ข้าง ๆ data.item ไม่ใช่ข้างใน
    const apiData = [];
    const getItem = async (url) => {
      try {
        const res = await fetch(url, { credentials: 'include', headers: { 'x-api-source': 'pc', 'af-ac-enc-dat': '' } });
        if (!res.ok) { lastApi.answered = false; return null; }
        const j = await res.json();
        if (j && j.data) apiData.push(j.data);
        lastApi.answered = true;
        lastApi.error = (j && j.error != null) ? j.error : null;
        if (lastApi.error != null && NOT_FOUND.indexOf(lastApi.error) >= 0) lastApi.notFound = true;
        return (j.data && (j.data.item || j.data)) || null;
      } catch (e) { lastApi.answered = false; return null; }
    };

    let item = null;
    for (const url of endpoints) {
      item = await getItem(url);
      if (item && (item.title || item.name)) { r.source = 'api'; break; }
    }

    // get_pc ไม่มียอดขาย (มีแค่ display_similar_sold) — ยอดขายจริงอยู่ใน item/get เท่านั้น
    // ถ้าที่ได้มาไม่มี historical_sold ให้ดึง item/get เสริมเฉพาะ field ยอดขาย
    if (item && item.historical_sold === undefined) {
      const alt = await getItem(`/api/v4/item/get?itemid=${r.product_id}&shopid=${r.shop_id}`);
      if (alt) {
        ['historical_sold', 'sold', 'global_sold', 'historical_sold_display',
         'images', 'video_info_list', 'item_rating', 'cmt_count', 'description', 'rich_text_description'].forEach(k => {
          if (alt[k] !== undefined && item[k] === undefined) item[k] = alt[k];
        });
      }
    }

    if (item) {
      r.product_name = item.title || item.name;
      r.price = num(item.price_min ?? item.price) !== null ? num(item.price_min ?? item.price) / D : null;
      r.price_max = num(item.price_max) !== null ? num(item.price_max) / D : null;
      r.original_price = num(item.price_before_discount) !== null ? num(item.price_before_discount) / D : null;
      if (r.original_price === 0) r.original_price = null;

      r.variation = (item.tier_variations || []).map(tv => ({
        name: tv.name,
        options: tv.options || []
      }));

      const tiers = r.variation.map(v => v.options);
      r.skus = (item.models || []).map(mo => ({
        sku_id: mo.modelid || mo.model_id,
        option_path: (mo.extinfo && mo.extinfo.tier_index)
          ? mo.extinfo.tier_index.map((idx, i) => (tiers[i] || [])[idx]).filter(Boolean).join(' / ')
          : mo.name,
        price: num(mo.price) !== null ? num(mo.price) / D : null,
        original_price: num(mo.price_before_discount) !== null ? num(mo.price_before_discount) / D : null,
        stock: mo.stock ?? null
      }));

      const attrs = item.attributes || item.product_attributes || [];
      r.spec = attrs.map(a => ({ name: a.name, value: a.value ?? a.values })).filter(a => a.name);
      if (item.brand) r.spec.unshift({ name: 'Brand', value: item.brand });
      if (item.categories) r.spec.push({ name: 'Category', value: item.categories.map(c => c.display_name).join(' > ') });

      // ชื่อร้าน: get_pc มักไม่ส่ง shop_name มา — ถ้าไม่มีให้เรียก endpoint ร้านแยก
      r.shop_name = item.shop_name || (item.shop_detailed && item.shop_detailed.name) || null;
      // cache ต่อ shop_id: 3274 สินค้ามาจากแค่ 924 ร้าน ถ้าไม่ cache จะยิง get_shop_detail ซ้ำ 2350 ครั้งฟรี
      // (ยิงเยอะ = โดน anti-bot เร็ว) — cache อยู่บน window อยู่ได้ตลอดอายุหน้าที่เปิดค้าง
      if (typeof window !== 'undefined' && !window.__SHOP_CACHE__) window.__SHOP_CACHE__ = {};
      const shopCache = (typeof window !== 'undefined' && window.__SHOP_CACHE__) || {};
      if (!r.shop_name && r.shop_id && shopCache[r.shop_id] !== undefined) {
        r.shop_name = shopCache[r.shop_id];          // เคยถามร้านนี้แล้ว ไม่ต้องยิงซ้ำ
      } else if (!r.shop_name && r.shop_id) {
        for (const su of [`/api/v4/shop/get_shop_detail?shopid=${r.shop_id}`,
                          `/api/v4/product/get_shop_info?shopid=${r.shop_id}`]) {
          try {
            const sr = await fetch(su, { credentials: 'include', headers: { 'x-api-source': 'pc' } });
            if (!sr.ok) continue;
            const sj = await sr.json();
            const sd = (sj.data && (sj.data.shop_detailed || sj.data)) || {};
            r.shop_name = sd.name || sd.shop_name || sd.username || null;
            if (r.shop_name) break;
          } catch (e) { /* ไม่ critical ปล่อยว่าง */ }
        }
        shopCache[r.shop_id] = r.shop_name || null;  // จำไว้ ทั้งที่เจอและไม่เจอ
      }

      // ยอดขาย: historical_sold = ยอดสะสมเลขเป๊ะ (ใช้ diff รายวัน), *_display เป็นช่วง "3k+" ไว้อ้างอิง
      // NB: get_pc ไม่ส่ง sold มา ต้องพึ่ง item/get (เติมไว้ด้านบนแล้ว) — field คือ global_sold ไม่ใช่ global_sold_count
      r.sold_count = num(item.historical_sold ?? item.global_sold ?? item.sold);
      r.sold_band = item.historical_sold_display || (r.sold_count != null ? String(r.sold_count) : null);
      if (r.sold_count === null) r.warnings.push('shopee: API ไม่มี historical_sold — ตรวจ endpoint');

      // ---- รูป / วิดีโอ / รีวิว ----
      // ⚠️ ยังไม่ได้ยืนยันกับ API จริง: 2026-09-25 ทดสอบไม่ได้ (API ตอบ 90309999 เมื่อไม่มี cookie ล็อกอิน)
      //    ชื่อ field ตาม item/get v4 + get_pc (product_images/product_review) — ถ้าหาไม่เจอจะเป็น null
      //    พร้อม warning บอกคีย์ที่มีจริง ให้ดูรอบเก็บจริงครั้งแรกก่อนเอาไปคิดคะแนน
      const pcImg = (apiData.find(d => d && d.product_images) || {}).product_images || null;
      const pcRev = (apiData.find(d => d && d.product_review) || {}).product_review || null;
      const IMG_HOST = 'https://down-th.img.susercontent.com/file/';

      const imgs = (pcImg && Array.isArray(pcImg.images) && pcImg.images) || (Array.isArray(item.images) && item.images) || null;
      if (imgs) {
        r.images = imgs.filter(h => typeof h === 'string' && h).map(h => /^https?:|^\/\//.test(h) ? absUrl(h) : IMG_HOST + h);
        r.image_count = r.images.length;
      } else {
        r.warnings.push('shopee: ไม่พบรายการรูป (images) — image_count เป็น null');
      }

      if (Array.isArray(item.video_info_list)) {
        r.video_count = item.video_info_list.filter(Boolean).length;
      } else if (pcImg && Array.isArray(pcImg.videos)) {
        r.video_count = pcImg.videos.filter(Boolean).length;
      } else if (pcImg && 'video' in pcImg) {
        r.video_count = pcImg.video ? 1 : 0;
      } else {
        r.warnings.push('shopee: ไม่พบ field วิดีโอ — video_count เป็น null');
      }

      // คำอธิบาย: item/get มี description เป็นข้อความล้วน (มีขึ้นบรรทัด) — ⚠️ ยังไม่ได้ยืนยันกับ API จริง
      const pcDesc = (apiData.find(d => d && d.product_description) || {}).product_description || null;
      if (typeof item.description === 'string' && item.description.trim()) {
        r.description = item.description.trim();
      } else if (pcDesc && Array.isArray(pcDesc.paragraph_list)) {
        // get_pc: [{type:1 ข้อความ, text} | {type:2 รูป, img_id}] (ชื่อ field ตามที่คาด ต้องตรวจรอบแรก)
        r.description = pcDesc.paragraph_list.filter(p => p && p.text).map(p => String(p.text).trim()).join('\n') || null;
        r.description_images = pcDesc.paragraph_list.filter(p => p && p.img_id).map(p => IMG_HOST + p.img_id);
      } else {
        r.warnings.push('shopee: ไม่พบคำอธิบายสินค้า — description เป็น null');
      }

      // item_rating.rating_count = [ทั้งหมด, 1★, 2★, 3★, 4★, 5★]
      const ir = item.item_rating || pcRev || null;
      if (ir) {
        const rc = Array.isArray(ir.rating_count) ? ir.rating_count : null;
        r.review_count = num(ir.total_rating_count ?? (rc && rc.length === 6 ? rc[0] : null) ?? ir.rating_total);
        const avg = num(ir.rating_star);
        r.rating = r.review_count ? avg : null;
        const five = rc ? (rc.length === 6 ? rc.slice(1) : rc) : null;
        r.rating_breakdown = starBreakdown(five, r.rating);
        if (r.review_count === null && five && five.length === 5) {
          const s = five.map(num).reduce((a, b) => a + (b || 0), 0);
          r.review_count = s; r.rating = s ? avg : null;
        }
      } else {
        r.warnings.push('shopee: ไม่พบ item_rating/product_review — rating เป็น null');
      }
    } else if (lastApi.answered && lastApi.error != null) {
      // API ตอบ 200 แต่มี error code — ต้องแยกให้ออกว่า "สินค้าหาย" หรือ "โดนกัน"
      // ผิดพลาดแล้วราคาแพง: ถ้าตีว่าหายทั้งที่โดนกัน สินค้าที่ยังขายอยู่จะถูกทิ้งถาวร
      // จึงใช้ whitelist: เฉพาะ code ที่ยืนยันแล้วเท่านั้นที่นับว่าหาย นอกนั้นถือว่าโดนกัน (ลองใหม่ได้)
      //   266900002 = ไม่พบสินค้า (ยืนยัน: id ปลอมได้ code นี้, หน้าเว็บไม่มีชื่อ/ราคา)
      //   90309999  = anti-bot ปฏิเสธ (ยืนยัน: สินค้าที่เพิ่งดึงได้ 63KB พอโดนบล็อกก็ได้ code นี้)
      if (lastApi.notFound) {
        r.source = 'blocked';
        r.warnings.push('shopee: สินค้าถูกลบ/ไม่พบสินค้า (API error ' + lastApi.error + ')');
        return r;
      }
      r.source = 'blocked';
      r.warnings.push('shopee: API ปฏิเสธ — โดน anti-bot (error ' + lastApi.error + ') พักแล้วลองใหม่ได้');
      return r;
    } else if (/ไม่พบสินค้า|product not found|page not found|no longer|ไม่พร้อมจำหน่าย/i
                 .test(((document.body && document.body.innerText) || '').replace(/\s+/g, ' ').slice(0, 500))) {
      // หน้าโหลดได้แต่ขึ้น "ไม่พบสินค้า" = สินค้าถูกลบ (Shopee ไม่ redirect url ยังเป็นหน้าสินค้า)
      // แยกจาก "API ไม่ตอบ" (anti-bot) ที่ลองใหม่ได้ — อันนี้ตายจริง ใส่ deadlist ได้
      r.source = 'blocked';
      r.warnings.push('shopee: สินค้าถูกลบ/ไม่พบสินค้า (หน้าโหลดได้แต่ไม่มีสินค้า)');
      return r;
    } else {
      r.source = 'dom';
      r.warnings.push('shopee: API ไม่ตอบ (อาจโดน anti-bot) — fallback DOM/JSON-LD');
      const ld = jsonLd();
      if (ld) {
        r.product_name = ld.name;
        r.price = num(ld.offers && (ld.offers.price || ld.offers.lowPrice));
        r.price_max = num(ld.offers && ld.offers.highPrice);
      } else {
        r.product_name = txt('h1') || txt('[class*="product-briefing"] span');
      }
    }
    return r;
  };

  // ============================================================
  // TIKTOK SHOP — schema เปลี่ยนบ่อย ใช้ deepFind หา node ที่มี sale_props/skus
  // ============================================================
  const tiktok = () => {
    const r = base();
    r.platform = 'tiktok';
    // /view/product/<id> ถูก redirect ไป shop.tiktok.com/th/pdp/<slug>/<id> ต้องจับได้ทั้งสองแบบ
    const m = location.href.match(/\/product\/(\d{6,})/) || location.pathname.match(/\/(\d{9,})(?:\/|$)/);
    r.product_id = m ? m[1] : null;

    // โดน CAPTCHA = ในหน้าไม่มีข้อมูลสินค้าเลย ไม่ต้องแกะต่อ เขียนเหตุผลไว้แทนการคืนค่าว่างเงียบ ๆ
    const blocked = captchaReason();
    if (blocked) {
      r.source = 'blocked';
      r.warnings.push('tiktok: ' + blocked + ' — ต้องผ่าน CAPTCHA เองก่อนด้วย scrape_pdp.py --login');
      return r;
    }
    // หน้า /pdp/ โหลดได้จริง แต่ขึ้น empty state "สินค้าไม่พร้อมใช้งาน" = ถูกลบ/ปิดการขาย (ไม่ใช่ CAPTCHA) -> ตี dead
    const naBody = ((document.body && document.body.innerText) || '');
    if (/สินค้าไม่พร้อมใช้งาน|ไม่พร้อมใช้งานในประเทศหรือภูมิภาค|not available in this (country|region)/i.test(naBody)) {
      r.source = 'blocked';
      r.warnings.push('tiktok: สินค้าไม่พร้อมใช้งานในภูมิภาคนี้ — สินค้าถูกลบ/ปิดการขาย');
      return r;
    }
    if (window.__ac_intercepted_fetch || window.__ac_intercepted_open) {
      r.warnings.push('tiktok: anti-bot patch fetch/XHR อยู่ (__ac_intercepted_*) — ค่าที่ได้อาจไม่ครบ');
    }

    const roots = [
      window.__UNIVERSAL_DATA_FOR_REHYDRATION__,
      window.__MODERN_ROUTER_DATA__,
      window._ROUTER_DATA,
      window.__INITIAL_STATE__
    ].filter(Boolean);

    // เผื่อ state ถูกฝังใน <script id="...">
    document.querySelectorAll('script[type="application/json"]').forEach(s => {
      try { roots.push(JSON.parse(s.textContent)); } catch (e) { /* ignore */ }
    });

    // สคีมาปี 2026: product_info = { product_model (ตัวสินค้า), promotion_model (ราคา) }
    // ราคา *ไม่ได้* อยู่ใน product_model ต้องไปหยิบจาก promotion_product_price.skus_price[sku_id]
    let info = null, pm = null;
    for (const root of roots) {
      info = deepFind(root, o => o.product_model && Array.isArray(o.product_model.skus));
      if (info) break;
    }
    if (info) pm = info.product_model;
    if (!pm) {                                   // เผื่อเจอ product_model ลอย ๆ ไม่มีตัวห่อ
      for (const root of roots) {
        pm = deepFind(root, o => Array.isArray(o.skus) && o.skus.length && (o.sale_properties || o.name));
        if (pm) break;
      }
    }

    if (pm) {
      r.source = 'state';
      r.product_name = pm.name || pm.title || null;
      if (pm.product_id) r.product_id = String(pm.product_id);   // string เสมอ (19 หลัก)
      if (pm.seller_id) r.shop_id = String(pm.seller_id);
      if (pm.sold_count !== undefined && pm.sold_count !== null) r.sold_count = num(pm.sold_count);

      // ---- ชื่อร้าน: อยู่ที่ seller_model.shop_name / shop_info.shop_name (ไม่มี id คู่ในตัว) ----
      for (const root of roots) {
        const sm = deepFind(root, o =>
          (o.seller_model && o.seller_model.shop_name) || (o.shop_info && o.shop_info.shop_name));
        if (sm) {
          r.shop_name = (sm.seller_model && sm.seller_model.shop_name)
                     || (sm.shop_info && sm.shop_info.shop_name) || null;
          break;
        }
      }
      // สำรอง: node ใดก็ได้ที่มี shop_name/seller_name (ไม่ใช่ product_model)
      if (!r.shop_name) {
        for (const root of roots) {
          const sn = deepFind(root, o => {
            const nk = ['shop_name', 'seller_name', 'store_name'].find(k => typeof o[k] === 'string' && o[k]);
            return nk && !Array.isArray(o.skus);
          });
          if (sn) { r.shop_name = sn.shop_name || sn.seller_name || sn.store_name || null; break; }
        }
      }

      let pp = info && info.promotion_model && info.promotion_model.promotion_product_price;
      if (!pp) {
        for (const root of roots) {
          pp = deepFind(root, o => o.skus_price && typeof o.skus_price === 'object');
          if (pp) break;
        }
      }
      const byId = (pp && pp.skus_price) || {};

      const prices = [];
      r.skus = (pm.skus || []).map(s => {
        const q = byId[s.sku_id] || {};
        const sp = num(q.sale_price_decimal ?? q.sale_price_format);
        const op = num(q.origin_price_decimal ?? q.origin_price_format);
        if (sp !== null) prices.push(sp);
        return {
          sku_id: s.sku_id ? String(s.sku_id) : null,
          option_path: (s.property_pairs || []).map(x => x.sku_property_value_name)
            .filter(Boolean).join(' / ') || s.sku_name || null,
          price: sp,
          original_price: op,
          stock: (s.sku_quantity && s.sku_quantity.available_quantity !== undefined)
            ? s.sku_quantity.available_quantity : null
        };
      });
      // price/original_price ต้องมาจาก sku *ตัวเดียวกัน* (ตัวที่ถูกที่สุด) ไม่งั้น discount_pct เพี้ยน
      let cheapest = null;
      r.skus.forEach(s => {
        if (s.price !== null && (cheapest === null || s.price < cheapest.price)) cheapest = s;
      });
      if (cheapest) {
        r.price = cheapest.price;
        r.original_price = cheapest.original_price;
      }
      if (prices.length) r.price_max = Math.max(...prices);
      if (r.price === null && pp && pp.min_price) {          // สำรอง: ราคาต่ำสุดของหน้า
        r.price = num(pp.min_price.sale_price_decimal);
        r.original_price = num(pp.min_price.origin_price_decimal);
      }
      if (pp && pp.range_price && pp.range_price.currency_name) r.currency = pp.range_price.currency_name;

      r.variation = (pm.sale_properties || pm.sale_props || []).map(p => ({
        name: p.property_name || p.prop_name,
        options: (p.property_values || p.sale_prop_values || [])
          .map(v => v.property_value_name || v.value_name).filter(Boolean)
      })).filter(v => v.name);

      r.spec = (pm.product_properties || []).map(p => ({
        name: p.property_name,
        value: (p.property_values || []).map(v => v.property_value_name).filter(Boolean).join(', ')
      })).filter(x => x.name && x.value);

      // ---- รูป / วิดีโอ / รีวิว ----
      // ⚠️ ยังไม่ได้ยืนยันกับหน้าจริง: 2026-09-25 เบราว์เซอร์ที่ไม่ได้ผ่าน CAPTCHA เจอ Security Check
      //    (ห้ามพยายามผ่านเอง) — ชื่อ field เป็นตัวเลือกที่น่าจะใช่ หาไม่เจอ = null + warning บอกคีย์ที่มีจริง
      //    วิดีโอดูเฉพาะใน product_model เท่านั้น ห้าม deepFind ทั้งหน้า: หน้า TikTok มีคลิปครีเอเตอร์/ไลฟ์
      //    ปนอยู่ ถ้านับรวมจะได้ว่าทุกหน้ามีวิดีโอ
      const pick = (o, keys) => { const k = keys.find(x => o && o[x] !== undefined && o[x] !== null); return k ? o[k] : undefined; };
      const keysLike = (o, re) => Object.keys(o || {}).filter(k => re.test(k)).join(',') || '-';

      const imgArr = pick(pm, ['images', 'main_images', 'product_images', 'image_list']);
      if (Array.isArray(imgArr)) {
        r.images = imgArr.map(im => typeof im === 'string' ? absUrl(im)
          : absUrl(im && ((Array.isArray(im.url_list) && im.url_list[0]) || im.url || im.thumb_url || im.uri)))
          .filter(Boolean);
        r.image_count = r.images.length;
      } else {
        r.warnings.push('tiktok: ไม่พบรายการรูปใน product_model (คีย์ที่มี: ' + keysLike(pm, /image|img|pic/i) + ')');
      }

      const VKEYS = ['video', 'product_video', 'videos', 'video_info', 'main_video'];
      const vk = VKEYS.find(k => k in pm);
      if (vk) {
        const v = pm[vk];
        r.video_count = Array.isArray(v) ? v.filter(Boolean).length : (v ? 1 : 0);
      } else {
        r.warnings.push('tiktok: ไม่พบ field วิดีโอใน product_model (คีย์ที่มี: ' + keysLike(pm, /video|vid/i) + ')');
      }

      // คำอธิบาย — ⚠️ ยังไม่ได้ยืนยันกับหน้าจริง: TikTok มักเก็บเป็น JSON string ของ rich text
      //   [{type:"text", text} | {type:"image", image:{url_list}}] หรือเป็น HTML/ข้อความล้วน รองรับทั้งสามแบบ
      const rawDesc = pick(pm, ['description', 'desc', 'product_description', 'desc_detail']);
      if (typeof rawDesc === 'string' && rawDesc.trim()) {
        let blocks = null;
        try { const j = JSON.parse(rawDesc); if (Array.isArray(j)) blocks = j; } catch (e) { /* ไม่ใช่ JSON */ }
        if (blocks) {
          r.description = blocks.filter(b => b && typeof b.text === 'string').map(b => b.text.trim()).filter(Boolean).join('\n') || null;
          r.description_images = blocks.map(b => b && b.image && absUrl((Array.isArray(b.image.url_list) && b.image.url_list[0]) || b.image.url))
            .filter(Boolean);
        } else if (/<[a-z][^>]*>/i.test(rawDesc)) {
          const h = htmlToText(rawDesc); r.description = h.text; r.description_images = h.images;
        } else {
          r.description = rawDesc.trim();
        }
      } else {
        r.warnings.push('tiktok: ไม่พบคำอธิบายใน product_model (คีย์ที่มี: ' + keysLike(pm, /desc/i) + ')');
      }

      // สรุปรีวิวระดับสินค้า — ค้นใน product_info ก่อน กันไปเจอคะแนนร้าน (ตัวที่มี shop_name ข้าม)
      const RATE = ['product_overall_score', 'overall_score', 'product_rating', 'avg_rating', 'rating'];
      const CNT = ['product_review_count', 'review_count', 'total_review_count', 'rating_count'];
      const isRev = o => !o.shop_name && !o.seller_name
        && RATE.some(k => num(o[k]) !== null) && CNT.some(k => num(o[k]) !== null);
      let rvn = info ? deepFind(info, isRev, 6) : null;
      if (!rvn) for (const root of roots) { rvn = deepFind(root, isRev); if (rvn) break; }
      if (rvn) {
        r.review_count = num(pick(rvn, CNT));
        r.rating = r.review_count ? num(pick(rvn, RATE)) : null;
        const dist = pick(rvn, ['star_distribution', 'rating_distribution', 'score_distribution']);
        if (Array.isArray(dist)) r.rating_breakdown = starBreakdown(dist.map(x => (x && typeof x === 'object') ? (x.count ?? x.num) : x), r.rating);
      } else {
        r.warnings.push('tiktok: ไม่พบสรุปรีวิวสินค้า (คีย์ใน product_info: ' + keysLike(info, /review|rating|score/i) + ')');
      }
    }

    if (!r.product_name) {
      r.source = r.source || 'dom';
      r.product_name = txt('h1') || txt('[data-e2e="product-title"]');
      r.price = r.price ?? num(txt('[data-e2e="product-price"]'));
      r.warnings.push('tiktok: state ไม่พบ/โครงสร้างเปลี่ยน — ได้แค่ DOM ให้ตรวจก่อนใช้');
    }
    return r;
  };

  // ---------- router ----------
  const host = location.hostname;
  let promise;
  try {
    if (/lazada\./.test(host))            promise = lazada();
    else if (/shopee\./.test(host))       promise = shopee();
    else if (/tiktok\.com|shop\.tiktok/.test(host)) promise = Promise.resolve(tiktok());
    else promise = Promise.resolve({ error: 'ไม่รู้จักโดเมนนี้: ' + host, url: location.href });
  } catch (e) {
    promise = Promise.resolve(hardFail(e));   // throw แบบ sync
  }

  return promise.catch(e => hardFail(e)).then(res => {
    window.__PDP_RESULT__ = res;          // เผื่อ tool ไม่ await -> เรียกซ้ำอ่านตัวแปรนี้
    // console ของ TikTok ถูก patch ทับ ถ้า throw ตรงนี้จะเสียผลที่แกะมาได้ทั้งหมด
    try { console.log(JSON.stringify(res, null, 2)); } catch (e) { /* ignore */ }
    return res;
  });
})();
