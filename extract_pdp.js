/* ============================================================
   extract_pdp.js  —  Universal PDP extractor (Lazada / Shopee / TikTok Shop, TH)
   รันผ่าน Claude in Chrome -> javascript_tool บนหน้า product detail page
   คืนค่า JSON เดียวกันทุกแพลตฟอร์ม:
     product_name, price, original_price, spec, variation, platform

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
  const lazada = () => {
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
    const f = md && deepFind(md, o => o.product && (o.skuBase || o.skuInfos));

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
    }

    // สินค้าถูกลบ/ปิดการขาย: Lazada เสิร์ฟหน้า "no longer available" ไม่มี state — บอกให้ชัด ไม่ใช่บั๊ก
    if (!r.product_name && /no longer available|product is no longer|ไม่พร้อมจำหน่าย/i.test(document.title || '')) {
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

    const getItem = async (url) => {
      try {
        const res = await fetch(url, { credentials: 'include', headers: { 'x-api-source': 'pc', 'af-ac-enc-dat': '' } });
        if (!res.ok) return null;
        const j = await res.json();
        return (j.data && (j.data.item || j.data)) || null;
      } catch (e) { return null; }
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
        ['historical_sold', 'sold', 'global_sold', 'historical_sold_display'].forEach(k => {
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
    if (/lazada\./.test(host))            promise = Promise.resolve(lazada());
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
