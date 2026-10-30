// جمع‌آور کاروان صنعت — اجرای خودکار در کروم صاحب فروشگاه (آی‌پی ایران، حساب‌های خودش).
// هیچ پیامی نمی‌فرستد و عضو هیچ کانالی نمی‌شود؛ فقط جست‌وجو و خواندن صفحه‌ها، آهسته.
// نتیجه با «توکن جمع‌آور» (COLLECTOR_TOKEN) به /api/ext/import پنل فرستاده می‌شود.

const VERSION = chrome.runtime.getManifest().version;
const DEFAULTS = {
  panel: "https://content.caravansanat.com", token: "", torobInstance: "292181",
  crawler: "https://crawler.caravansanat.com", crawlerToken: "",
  tasks: {outreach: true, eitaa: true, telegram: true, torob_prices: true, torob_panel: true, google: false, site_prices: true},
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const jitter = (a, b) => a + Math.random() * (b - a);

async function cfg() {
  const s = await chrome.storage.local.get(["cfg"]);
  return {...DEFAULTS, ...(s.cfg || {}), tasks: {...DEFAULTS.tasks, ...((s.cfg || {}).tasks || {})}};
}
async function log(task, ok, summary) {
  const s = (await chrome.storage.local.get(["status"])).status || {};
  s[task] = {at: new Date().toISOString(), ok, summary: String(summary).slice(0, 300)};
  await chrome.storage.local.set({status: s});
  try { await api("/api/collector/status", {method: "POST", body: JSON.stringify({task, ok, summary, version: VERSION})}); } catch (e) {}
}
async function api(path, opts = {}) {
  const c = await cfg();
  if (!c.token) throw new Error("توکن جمع‌آور در تنظیمات افزونه وارد نشده");
  const r = await fetch(c.panel.replace(/\/$/, "") + path, {...opts, headers: {"Content-Type": "application/json", "X-Collector-Token": c.token}});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
  return d;
}
const send = (source, rows, meta = {}) => api("/api/ext/import", {method: "POST", body: JSON.stringify({source, rows, meta})});

// ---------- تب پس‌زمینه ----------
async function openTab(url, waitMs = 8000) {
  const tab = await chrome.tabs.create({url, active: false});
  for (let i = 0; i < 30; i++) {
    const t = await chrome.tabs.get(tab.id);
    if (t.status === "complete") break;
    await sleep(1000);
  }
  await sleep(waitMs);
  return tab.id;
}
// اگر صفحه وسط کار خودش جابه‌جا شد (ریدایرکت پنل ترب و…)، صبر و دوباره تلاش
async function run(tabId, func, args = []) {
  for (let i = 0; ; i++) {
    try {
      const [res] = await chrome.scripting.executeScript({target: {tabId}, world: "MAIN", func, args});
      return res ? res.result : null;
    } catch (e) {
      if (i >= 2 || !/frame|removed|navigat/i.test(e.message || "")) throw e;
      for (let k = 0; k < 20; k++) { if ((await chrome.tabs.get(tabId)).status === "complete") break; await sleep(1000); }
      await sleep(5000);
    }
  }
}
const closeTab = id => chrome.tabs.remove(id).catch(() => {});
// ایتا و تلگرام وب فقط وقتی صفحه «دیده می‌شود» نتیجه‌ها را می‌سازند (تب/پنجره‌ی پنهان چیزی نشان نمی‌دهد).
// پنجره‌ی جدا پشت پنجره‌ی کاربر باز می‌شود و جلو نمی‌آید. اگر کروم با گزینه‌ی
// --disable-backgrounding-occluded-windows اجرا شده باشد، همان پشت کار می‌کند؛ وگرنه صبر می‌کند تا کاربر
// ۱۰ دقیقه پشت سیستم نباشد و فقط آن موقع پنجره را جلو می‌آورد.
async function openWindow(url, waitMs) {
  const idle = (await chrome.idle.queryState(600)) === "idle";
  const w = await chrome.windows.create({url, focused: idle, type: "normal", width: 1000, height: 760});
  const id = w.tabs[0].id;
  for (let i = 0; i < 30; i++) { if ((await chrome.tabs.get(id)).status === "complete") break; await sleep(1000); }
  await sleep(waitMs);
  return {id, win: w.id};
}
// اگر کاربر تب را به پنجره‌ی دیگری برده بود، خود تب بسته می‌شود
const closeWindow = async w => { try { await chrome.windows.remove(w.win); } catch (e) { /* پنجره دیگر نیست */ }
  chrome.tabs.remove(w.id).catch(() => {}); };
async function waitVisible(w, label) {
  for (let i = 0; i < 360; i++) {                         // حداکثر ۳ ساعت
    if ((await run(w.id, () => document.visibilityState)) === "visible") return;
    if ((await chrome.idle.queryState(600)) === "idle") {   // کاربر نیست ← پنجره جلو بیاید
      await chrome.windows.update(w.win, {focused: true}).catch(() => {});
      await sleep(4000);
      continue;
    }
    await progress(`در انتظار: پنجره‌ی ${label} پشت پنجره‌های شماست؛ وقتی ۱۰ دقیقه پشت سیستم نباشید خودش ادامه می‌دهد (یا همین حالا آن را جلو بیاورید)`);
    await sleep(30000);
  }
  throw new Error(`پنجره‌ی ${label} ۳ ساعت دیده نشد؛ کار نیمه ماند`);
}

// ---------- توابعی که داخل صفحه اجرا می‌شوند ----------
// ایتای وب: جست‌وجوی «سراسری» (داخل متن پست‌ها) + «گفتگوها»؛ هر کانال باز و بازدید پست‌هایش خوانده می‌شود.
async function pageEitaaQuery(q, limit, minMembers, seen) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const fa = s => String(s || "").replace(/[۰-۹]/g, d => "۰۱۲۳۴۵۶۷۸۹".indexOf(d)).replace(/٫/g, ".");
  const num = s => { const t = fa(s).replace(/[,\s٬]/g, ""); const m = t.match(/([\d.]+)\s*([KkMm]|هزار|میلیون)?/); if (!m) return null;
    return Math.round(parseFloat(m[1]) * ({k: 1e3, m: 1e6, "هزار": 1e3, "میلیون": 1e6}[(m[2] || "").toLowerCase()] || 1)); };
  const inp = document.querySelector(".sidebar-header input.input-search-input");
  if (!inp) return {error: "ورود به ایتای وب انجام نشده"};
  inp.focus(); inp.value = q; inp.dispatchEvent(new Event("input", {bubbles: true}));
  await sleep(3500);
  const tab = [...document.querySelectorAll(".search-super-tabs .menu-horizontal-div-item")].find(e => e.textContent.trim() === "سراسری");
  if (tab) tab.click();
  await sleep(4500);
  const pick = sel => [...(document.querySelector(sel)?.querySelectorAll("a.chatlist-chat") || [])].map(a => a.getAttribute("data-peer-id")).filter(x => x && x.startsWith("-"));
  const ids = [...new Set([...pick(".search-super-content-global"), ...pick(".search-super-content-chats")])].filter(x => !seen.includes(x)).slice(0, limit);
  const rows = [];
  for (const id of ids) {
    location.hash = "#" + id; await sleep(3500);
    const c = document.querySelector(".chat.active"); if (!c) continue;
    const members = num(c.querySelector(".chat-info .info")?.innerText);
    if (!members || members < minMembers) continue;
    const sc = c.querySelector(".bubbles .scrollable");
    for (let i = 0; i < 3 && sc; i++) { sc.scrollTop = 0; await sleep(1600); }
    if (sc) { const step = Math.max(300, sc.clientHeight * 0.8), end = sc.scrollHeight;   // حداکثر ۲۵ قدم تا بازدیدها بارگذاری شوند
      for (let i = 0, y = 0; i < 25 && y <= end; i++, y += step) { sc.scrollTop = y; await sleep(650); } await sleep(1200); }
    const user = (location.hash.match(/#@([A-Za-z0-9_]+)/) || [])[1];
    if (!user) continue;
    const posts = [...c.querySelectorAll(".bubble[data-timestamp]")].filter(b => !b.classList.contains("service")).map(b => {
      const v = num(b.querySelector(".post-views")?.textContent);
      return {ts: b.dataset.timestamp, views: v > 1 ? v : null, text: (b.querySelector(".message")?.innerText || "").replace(/\s+/g, " ").slice(0, 600)};
    }).filter(p => p.views != null);
    rows.push({platform: "eitaa", username: user, title: c.querySelector(".chat-info .peer-title")?.textContent || "", members: String(members), posts, query: q});
    await sleep(1500);
  }
  return {ids, rows};
}

// ایتای وب: کانال‌هایی که از پست‌های کانال‌های خوب پیدا شده‌اند (گلوله‌برفی) با آیدی باز و سنجیده می‌شوند.
async function pageEitaaHandles(handles, minMembers) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const fa = s => String(s || "").replace(/[۰-۹]/g, d => "۰۱۲۳۴۵۶۷۸۹".indexOf(d)).replace(/٫/g, ".");
  const num = s => { const t = fa(s).replace(/[,\s٬]/g, ""); const m = t.match(/([\d.]+)\s*([KkMm]|هزار|میلیون)?/); if (!m) return null;
    return Math.round(parseFloat(m[1]) * ({k: 1e3, m: 1e6, "هزار": 1e3, "میلیون": 1e6}[(m[2] || "").toLowerCase()] || 1)); };
  if (!document.querySelector(".sidebar-header input.input-search-input")) return {error: "ورود به ایتای وب انجام نشده"};
  const rows = [], done = [];
  for (const h of handles) {
    location.hash = "#@" + h.username; await sleep(5000);
    if (location.hash.toLowerCase() !== ("#@" + h.username).toLowerCase()) { done.push(["eitaa", h.username, false, "پیدا نشد"]); continue; }
    const c = document.querySelector(".chat.active"); if (!c) { done.push(["eitaa", h.username, false, "باز نشد"]); continue; }
    const info = c.querySelector(".chat-info .info")?.innerText || "";
    const members = num(info);
    if (!/مشترک|subscriber/i.test(info)) { done.push(["eitaa", h.username, false, "کانال نیست"]); continue; }
    if (!members || members < minMembers) { done.push(["eitaa", h.username, false, `عضو کم (${members || 0})`]); continue; }
    const sc = c.querySelector(".bubbles .scrollable");
    for (let i = 0; i < 3 && sc; i++) { sc.scrollTop = 0; await sleep(1600); }
    if (sc) { const step = Math.max(300, sc.clientHeight * 0.8), end = sc.scrollHeight;
      for (let i = 0, y = 0; i < 25 && y <= end; i++, y += step) { sc.scrollTop = y; await sleep(650); } await sleep(1200); }
    const posts = [...c.querySelectorAll(".bubble[data-timestamp]")].filter(b => !b.classList.contains("service")).map(b => {
      const v = num(b.querySelector(".post-views")?.textContent);
      return {ts: b.dataset.timestamp, views: v > 1 ? v : null, text: (b.querySelector(".message")?.innerText || "").replace(/\s+/g, " ").slice(0, 600)};
    }).filter(p => p.views != null);
    rows.push({platform: "eitaa", username: h.username, title: c.querySelector(".chat-info .peer-title")?.textContent || "",
               members: String(members), posts, query: h.via});
    await sleep(1500);
  }
  return {rows, done};
}

// تلگرام وب: فقط بخش «Global Search» (نه گفتگوهای شخصی) خوانده می‌شود.
async function pageTelegramSearch(q) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const inp = document.querySelector("#telegram-search-input");
  if (!inp) return {error: "ورود به تلگرام وب انجام نشده"};
  ["pointerdown", "mousedown", "mouseup", "click"].forEach(t => inp.dispatchEvent(new MouseEvent(t, {bubbles: true})));
  inp.focus(); await sleep(800); inp.select();
  document.execCommand("insertText", false, q);                 // مثل تایپ واقعی (setter ساده را تلگرام نادیده می‌گیرد)
  if (inp.value !== q) { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(inp, q);
    inp.dispatchEvent(new Event("input", {bubbles: true})); }
  await sleep(4500);
  const out = [];
  document.querySelectorAll(".section-heading").forEach(h => {
    if (!/Global Search|جستجوی سراسری|جست‌وجوی سراسری/i.test(h.textContent)) return;
    const box = h.parentElement;
    box.querySelectorAll(".ListItem").forEach(it => {
      const handle = (it.querySelector(".handle")?.textContent || "").replace(/^@/, "").trim();
      const status = it.querySelector(".status")?.textContent || "";
      if (handle && /subscriber|مشترک|دنبال/i.test(status) && !/bot$/i.test(handle)) out.push({handle, status});
    });
  });
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(inp, "");
  inp.dispatchEvent(new Event("input", {bubbles: true}));
  return {found: out};
}

// ---------- پیام به ادمین‌ها (فقط پیام‌هایی که صاحب فروشگاه در پنل تأیید کرده) ----------
// گفتگوی یک آیدی را با جست‌وجو باز می‌کند؛ فقط حساب شخص (نه کانال/گروه/ربات) و فقط با آیدی دقیقاً همان.
async function pageTgOpenUser(handle, peer, allowName) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const inp = document.querySelector("#telegram-search-input");
  if (!inp) return {error: "ورود به تلگرام وب انجام نشده"};
  ["pointerdown", "mousedown", "mouseup", "click"].forEach(t => inp.dispatchEvent(new MouseEvent(t, {bubbles: true})));
  inp.focus(); await sleep(800); inp.select(); document.execCommand("insertText", false, handle);   // حالت جست‌وجو باید فعال شود
  const norm = x => String(x || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const pid = x => x.querySelector("[data-peer-id]")?.getAttribute("data-peer-id") || x.getAttribute("data-peer-id") || "";
  let it = null, how = "";
  for (let i = 0; i < 6 && !it; i++) {                       // نتیجه‌ها کم‌کم می‌آیند
    await sleep(1500);
    const items = [...document.querySelectorAll("[class*='LeftSearch'] .ListItem, .search-result")];
    if (peer) { it = items.find(x => pid(x) === String(peer)); how = "peer"; }
    if (!it) { it = items.find(x => norm(x.querySelector(".handle")?.textContent) === norm(handle)); how = "handle"; }
    // گفتگوی قبلی (مخاطب ذخیره‌شده) در جست‌وجو آیدی ندارد؛ فقط برای «خواندن جواب»، با نام هم‌خوان
    if (!it && allowName && i >= 3) { it = items.find(x => !x.querySelector(".handle") && norm(x.querySelector(".fullName")?.textContent) === norm(handle)); how = "name"; }
  }
  if (!it) return {error: "این آیدی در تلگرام پیدا نشد"};
  const st = (it.querySelector(".status")?.textContent || "") + " " + (it.querySelector(".subtitle")?.textContent || "");
  if (/subscriber|member|مشترک|عضو/i.test(st) || /bot$/i.test(handle)) return {error: "این آیدی کانال/گروه/ربات است، نه شخص"};
  const b = it.querySelector(".ListItem-button") || it;
  b.dispatchEvent(new MouseEvent("mousedown", {bubbles: true})); b.click();
  await sleep(5000);
  if (!document.getElementById("editable-message-text")) return {error: "گفتگو باز نشد (شاید ارسال پیام بسته است)"};
  return {ok: true, how, peer: (location.hash.match(/^#(-?\d+)/) || [])[1] || pid(it),
          title: document.querySelector(".MiddleHeader .fullName")?.textContent || "",
          own: document.querySelectorAll("#MiddleColumn .Message.own").length};
}
async function pageTgSend(lines) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const warn = () => [...document.querySelectorAll(".Notification, .modal, [class*='Toast'], [role='alert']")]
    .map(e => e.innerText).join(" ").slice(0, 300);
  const ed = document.getElementById("editable-message-text");
  if (!ed) return {ok: false, error: "کادر پیام پیدا نشد"};
  ed.focus();
  document.execCommand("selectAll"); document.execCommand("delete");          // پیش‌نویس قبلی پاک شود
  lines.forEach((l, i) => { if (i) document.execCommand("insertLineBreak"); document.execCommand("insertText", false, l); });
  await sleep(800);
  const letters = x => String(x || "").replace(/[^\p{L}\p{N}]/gu, "");     // ایموجی در کادر تصویر است؛ فقط حروف و عددها مقایسه شوند
  if (letters(ed.innerText) !== letters(lines.join(" "))) {
    document.execCommand("selectAll"); document.execCommand("delete");
    return {ok: false, error: "متن درست در کادر ننشست؛ ارسال نشد"};
  }
  const before = document.querySelectorAll("#MiddleColumn .Message.own").length;
  const btn = document.querySelector("#MiddleColumn button.main-button.send");
  if (!btn) return {ok: false, error: "دکمه‌ی ارسال پیدا نشد"};
  btn.click();
  await sleep(5000);
  const w = warn();
  if (/too many|flood|spam|limit|محدود|اسپم|بیش از حد/i.test(w)) return {ok: false, stop: true, error: "هشدار تلگرام: " + w};
  const own = [...document.querySelectorAll("#MiddleColumn .Message.own")];
  const last = own[own.length - 1];
  if (own.length <= before || !(last?.innerText || "").includes(lines[0]))
    return {ok: false, stop: true, error: "رسیدن پیام تأیید نشد" + (w ? ": " + w : "")};
  return {ok: true};
}
// جواب ادمین: پیام‌های طرف مقابل بعد از پیام ما (که خط «first» را دارد)
async function pageTgReadReply(first) {
  const msgs = [...document.querySelectorAll("#MiddleColumn .Message")];
  let k = -1;
  const letters = x => String(x || "").replace(/[^\p{L}\p{N}]/gu, "");
  const f = letters(first).slice(0, 40);
  msgs.forEach((m, i) => { if (m.classList.contains("own") && letters(m.innerText).includes(f)) k = i; });
  if (k < 0) return {text: "", found: false};
  const txt = msgs.slice(k + 1).filter(m => !m.classList.contains("own"))
    .map(m => (m.querySelector(".text-content")?.innerText || "").replace(/\n?\d{1,2}:\d{2}\s*$/, "").trim()).filter(Boolean);
  return {text: txt.join("\n").slice(0, 3000)};
}

// پنل فروشنده‌ی ترب: کلیک و هزینه‌ی هر کالا، نمودار روزانه، موجودی حساب، وضعیت کالاها.
async function pageTorobPanel(from, to, inst) {
  const B = "https://api.torob.com/shop-panel/", Q = `&instance_type=internet_shop&instance_id=${inst}`;
  const j = u => fetch(B + u + Q, {credentials: "include"}).then(r => r.json());
  const c = await j(`click-chart/?from_date=${from}&to_date=${to}&category=&random_key=&span=daily&model=all&display_action=all&click_type=all&cost_model=all&cnt=0`);
  let rows = [], page = 1;
  while (page <= 12) {
    const p = await j(`click-product-list/?from_date=${from}&to_date=${to}&limit=500&category=&model=all&dispaly_action=all&click_type=all&page=${page}&sort_id=-click_count&cost_model=all`);
    const r = p.results || [];
    rows = rows.concat(r.map(x => [x.link, x.click_count, x.amount, x.price, x.name]));
    if (!r.length || rows.length >= p.count) break;
    page++;
    await new Promise(z => setTimeout(z, 1500));
  }
  const d = await j("get-shop-dashboard-data/?");
  const base = "is_force=false&was_accessible=&availability=&is_active=&error_reason=&category=&stock_status=&q=&product_id=&delete_type=&is_deleted=&checked_for_merge=&is_price_valid=&show_as_offline=";
  const status = {};
  for (const k of ["page_deleted_temporary_technical", "page_not_available", "base_not_merged", "page_not_accessible", "in_progress_add", "price_not_valid"]) {
    try { status[k] = (await j(`products-report-count/?${base}&status_key=${k}`)).count; } catch (e) {}
    await new Promise(z => setTimeout(z, 800));
  }
  return {rows, meta: {from, to, daily: (c.daily_report || []).map(r => [r[1], r[2]]), balance: d.balance, status}};
}

// گوگل: جست‌وجوی site:t.me؛ با اولین صفحه‌ی «ربات نیستم» متوقف می‌شود (دور زده نمی‌شود).
async function pageGoogle(queries) {
  const rows = [];
  for (const q of queries) {
    const r = await fetch("/search?num=30&q=" + encodeURIComponent(q), {credentials: "include"});
    const h = await r.text();
    if (r.url.includes("/sorry/") || /unusual traffic/i.test(h)) return {rows, blocked: true};
    [...new Set([...h.matchAll(/https?:\/\/t\.me\/(?:s\/)?([A-Za-z][A-Za-z0-9_]{3,31})/g)].map(m => m[1]))].forEach(u => rows.push(["https://t.me/" + u, q]));
    await new Promise(z => setTimeout(z, 4000 + Math.random() * 3000));
  }
  return {rows, blocked: false};
}

// ---------- کارها ----------
const TASKS = {
  async eitaa(t) {
    const T = await openWindow("https://web.eitaa.com/", 9000), tab = T.id;
    let seen = [], n = 0, sent = 0, snow = 0, found = 0;
    try {
      for (const q of t.eitaa_queries) {
        await waitVisible(T, "ایتا");
        await progress(`جست‌وجوی ${n + 1} از ${t.eitaa_queries.length}: «${q}» — ${sent} کانال تا الان`);
        const r = await run(tab, pageEitaaQuery, [q, t.limits.eitaa_channels_per_query, t.limits.min_members || 800, seen]);
        if (!r || r.error) throw new Error((r && r.error) || "خطای صفحه");
        seen = seen.concat(r.ids); found += r.ids.length;
        if (r.rows.length) { await send("channel_posts", r.rows, {platform: "eitaa"}); sent += r.rows.length; }
        n++;
        await sleep(jitter(5000, 9000));
      }
      const hs = t.eitaa_handles || [];
      for (let i = 0; i < hs.length; i += 5) {                // گلوله‌برفی: ۵تا ۵تا
        await waitVisible(T, "ایتا");
        await progress(`کانال‌های معرفی‌شده: ${i + 1} تا ${Math.min(i + 5, hs.length)} از ${hs.length}`);
        const r = await run(tab, pageEitaaHandles, [hs.slice(i, i + 5), t.limits.min_members || 800]);
        if (!r || r.error) break;
        if (r.rows.length) { await send("channel_posts", r.rows, {platform: "eitaa", source: "snowball"}); snow += r.rows.length; }
        if (r.done.length) await send("candidates_checked", r.done);
        await sleep(jitter(4000, 7000));
      }
    } finally { closeWindow(T); }
    if (n >= 3 && !found) throw new Error("ایتا هیچ نتیجه‌ای نشان نداد؛ ورود به ایتای وب را در همین کروم بررسی کنید");
    return `${n} جست‌وجو، ${found} نتیجه، ${sent} کانال سنجیده؛ ${snow} کانال از معرفی‌ها`;
  },
  async telegram(t) {
    const T = await openWindow("https://web.telegram.org/a/", 9000), tab = T.id;
    const rows = [];
    try {
      for (const [i, q] of t.telegram_queries.entries()) {
        await waitVisible(T, "تلگرام");
        await progress(`جست‌وجوی ${i + 1} از ${t.telegram_queries.length}: «${q}»`);
        const r = await run(tab, pageTelegramSearch, [q]);
        if (!r || r.error) throw new Error((r && r.error) || "خطای صفحه");
        r.found.slice(0, t.limits.telegram_results_per_query).forEach(x => rows.push(["https://t.me/" + x.handle, q]));
        await sleep(jitter(6000, 10000));
      }
    } finally { closeWindow(T); }
    if (rows.length) await send("channel_candidates", rows, {source: "telegram_search"});
    return `${t.telegram_queries.length} جست‌وجو، ${rows.length} کانال نامزد`;
  },
  async outreach(t) {
    const o = t.outreach || {send: [], check: []};
    if (!o.send.length && !o.check.length) return "پیامی برای ارسال یا پیگیری نبود";
    const T = await openWindow("https://web.telegram.org/a/", 9000), tab = T.id;
    let sent = 0, replies = 0, stopped = "";
    const notes = [];
    try {
      for (const [i, m] of o.send.entries()) {
        await waitVisible(T, "تلگرام");
        await progress(`ارسال پیام ${i + 1} از ${o.send.length} به @${m.handle}`);
        const op = await run(tab, pageTgOpenUser, [m.handle, null, false]);
        let r;
        if (!op || !op.ok) r = {id: m.id, ok: false, error: (op && op.error) || "خطای صفحه"};
        else if (op.own > 0) r = {id: m.id, ok: false, skip: true, peer: op.peer, error: "قبلاً با این آیدی گفتگو داشته‌اید؛ دستی پیگیری کنید"};
        else r = {id: m.id, peer: op.peer, ...(await run(tab, pageTgSend, [m.lines]))};
        await send("outreach_result", [r]);
        if (r.ok) sent++;
        if (r.stop) { stopped = r.error; break; }
        if (i < o.send.length - 1) {                       // ۴ تا ۸ دقیقه فاصله
          const until = Date.now() + jitter(o.gap_min[0], o.gap_min[1]) * 60000;
          while (Date.now() < until) { await progress(`پیام بعدی ${Math.ceil((until - Date.now()) / 60000)} دقیقه‌ی دیگر`); await sleep(30000); }
        }
      }
      const rows = [];
      if (!stopped) for (const m of o.check) {
        await waitVisible(T, "تلگرام");
        await progress(`خواندن جواب @${m.handle}`);
        const r = await run(tab, pageTgOpenUser, [m.handle, m.peer, true]);
        if (!r || !r.ok) { notes.push(`@${m.handle}: ${(r && r.error) || "باز نشد"}`); continue; }
        const rep = await run(tab, pageTgReadReply, [m.first]);
        if (rep && rep.text) rows.push({id: m.id, peer: r.peer, text: rep.text});
        else if (rep && rep.found === false) notes.push(`@${m.handle}: پیام ما در گفتگو پیدا نشد`);
        await sleep(jitter(4000, 8000));
      }
      if (rows.length) { await send("outreach_replies", rows); replies = rows.length; }
    } finally { closeWindow(T); }
    if (stopped) throw new Error("ارسال متوقف شد: " + stopped);
    return `${sent} پیام فرستاده شد، ${replies} جواب خوانده شد` + (notes.length ? " — " + notes.slice(0, 3).join("؛ ") : "");
  },
  async torob_prices(t) {
    let batch = [], n = 0;
    for (const p of t.torob_todo) {
      await progress(`کالای ${n + 1} از ${t.torob_todo.length}`);
      const r = await fetch("https://api.torob.com/v4/base-product/search/?page=0&sort=popularity&size=12&source=next_desktop&q=" + encodeURIComponent(p.q || p.name));
      if (r.status !== 200) { if (batch.length) await send("torob_prices", batch); throw new Error("ترب جواب نداد: HTTP " + r.status + " (بعد از " + n + " کالا)"); }
      const d = await r.json();
      batch.push([p.id, (d.results || []).slice(0, 12).map(x => ({name1: x.name1, price: x.price, shop_text: x.shop_text, web_client_absolute_url: x.web_client_absolute_url}))]);
      n++;
      if (batch.length >= 10) { await send("torob_prices", batch); batch = []; }
      await sleep(jitter(30000, 60000));                   // آهسته، تا حساب ترب بسته نشود
    }
    if (batch.length) await send("torob_prices", batch);
    return `${n} کالا با ترب مقایسه شد`;
  },
  async torob_panel(t, c) {
    const tab = await openTab(`https://panel.torob.com/s/shopDashboard?instance_id=${c.torobInstance}`, 6000);
    try {
      const r = await run(tab, pageTorobPanel, [t.torob_panel.from, t.torob_panel.to, c.torobInstance]);
      if (!r || !r.rows) throw new Error("ورود به پنل ترب انجام نشده یا پنل جواب نداد");
      await send("torob_clicks", r.rows, r.meta);
      return `${r.rows.length} کالا، موجودی ${Number(r.meta.balance || 0).toLocaleString("fa-IR")} تومان`;
    } finally { closeTab(tab); }
  },
  async google(t) {
    const tab = await openTab("https://www.google.com/", 4000);
    try {
      const r = await run(tab, pageGoogle, [t.google_queries]);
      if (r.rows.length) await send("channel_candidates", r.rows);
      return `${r.rows.length} لینک کانال${r.blocked ? " — گوگل «ربات نیستم» خواست؛ متوقف شد" : ""}`;
    } finally { closeTab(tab); }
  },
  // قیمت محصولات خود سایت در ترب: کار را از کراولر می‌گیرد (/api/torob/next) و نتیجه را همان‌جا برمی‌گرداند.
  // هر بار حداکثر ۱۵ کالا، بین هر دو کالا ۱ تا ۲.۵ دقیقه؛ سقف روزانه را خود کراولر نگه می‌دارد.
  async site_prices(t, c) {
    const base = c.crawler.replace(/\/$/, "");
    const cr = async (method, path, body) => {
      const r = await fetch(base + path, {method, body: body ? JSON.stringify(body) : undefined,
        headers: {"Content-Type": "application/json", "X-Torob-Token": c.crawlerToken}});
      if (r.status === 401) throw new Error("کلید کراولر نادرست است (از crawler.caravansanat.com/api/torob/token بردارید)");
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || ("کراولر: HTTP " + r.status));
      return d;
    };
    const torob = async (path, params) => {
      const u = new URL("https://api.torob.com/v4/base-product/" + path);
      Object.entries(params).forEach(([k, v]) => u.searchParams.set(k, v));
      const r = await fetch(u);
      if (r.status !== 200) throw new Error("ترب جواب نداد: HTTP " + r.status);
      return r.json();
    };
    let n = 0, high = 0, low = 0, why = "";
    for (let i = 0; i < 15; i++) {
      const job = await cr("GET", "/api/torob/next");
      if (job.wait) { why = job.why || "فعلاً کاری نیست"; break; }
      await progress(`کالای ${i + 1}: ${String(job.q).slice(0, 40)}`);
      const s = await torob("search/", {q: job.q, page: 0, size: 8});
      const items = (s.results || []).map(x => ({name1: x.name1 || x.name || "", price: x.price, random_key: x.random_key}));
      let res = await cr("POST", "/api/torob/result", {id: job.id, items});
      if (res.details) {
        await sleep(jitter(8000, 18000));
        const d = await torob("details/", {prk: res.details});
        const offers = ((d.products_info || {}).result || []).map(o => ({shop: o.shop_text || o.shop_name || "", price: o.price, available: o.availability !== false}));
        res = await cr("POST", "/api/torob/result", {id: job.id, prk: res.details, torob_name: res.torob_name, offers});
      }
      n++; if (res.verdict === "high") high++; if (res.verdict === "low") low++;
      await sleep(jitter(60000, 150000));
    }
    return `${n} کالای سایت با ترب مقایسه شد` + (n ? ` (گران‌تر: ${high}، ارزان‌تر: ${low})` : "") + (why ? " — " + why : "");
  },
};

// قفل اجرا: هر کار در هر مرحله «ضربان» می‌زند؛ اگر ۱۰ دقیقه ضربان نیاید (مثلاً کروم بسته شد) قفل آزاد می‌شود.
const STALE_MS = 10 * 60 * 1000;
async function progress(step) {
  const r = (await chrome.storage.local.get(["running"])).running;
  if (r) await chrome.storage.local.set({running: {...r, step, beat: Date.now()}});
}
async function busy() {
  const r = (await chrome.storage.local.get(["running"])).running;
  return r && Date.now() - (r.beat || r.at) < STALE_MS ? r : null;
}
async function runTask(name) {
  const cur = await busy();
  if (cur) {
    const q = (await chrome.storage.local.get(["queue"])).queue || [];
    if (cur.name !== name && !q.includes(name)) q.push(name);
    await chrome.storage.local.set({queue: q});
    return cur.name === name ? "همین کار الان در حال اجراست" : "در صف قرار گرفت؛ بعد از کار فعلی خودش اجرا می‌شود";
  }
  const c = await cfg();
  await chrome.storage.local.set({running: {name, at: Date.now(), beat: Date.now(), step: "شروع…"}});
  const keep = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);  // سرویس‌ورکر خاموش نشود
  let out;
  try {
    const t = name === "site_prices" ? {} : await api("/api/collector/tasks");
    const summary = await TASKS[name](t, c);
    await log(name, true, summary);
    out = summary;
  } catch (e) {
    await log(name, false, e.message || String(e));
    out = "خطا: " + (e.message || e);
  } finally {
    clearInterval(keep);
    await chrome.storage.local.remove("running");
  }
  const q = (await chrome.storage.local.get(["queue"])).queue || [];
  if (q.length) { const next = q.shift(); await chrome.storage.local.set({queue: q}); runTask(next); }
  return out;
}

// هر ۳۰ دقیقه: اولین کاری که وقتش رسیده (یکی‌یکی، تا مرورگر شلوغ نشود)
async function tick() {
  const c = await cfg();
  if (c.tasks.site_prices && c.crawlerToken) {      // جدا از پنل محتوا؛ ساعتی یک بار
    const st = ((await chrome.storage.local.get(["status"])).status || {}).site_prices;
    if (!st || Date.now() - Date.parse(st.at) > 3600 * 1000) { await runTask("site_prices"); return; }
  }
  if (!c.token) return;
  let t;
  try { t = await api("/api/collector/tasks"); } catch (e) { return; }
  const status = (await chrome.storage.local.get(["status"])).status || {};
  for (const name of Object.keys(TASKS)) {
    if (!c.tasks[name] || name === "site_prices") continue;
    const last = status[name] ? Date.parse(status[name].at) : 0;
    if (Date.now() - last < (t.intervals_hours[name] || 24) * 3600 * 1000) continue;
    if (name === "outreach") {                    // پنجره فقط وقتی کاری هست: پیام تأییدشده، یا هر ۳ ساعت خواندن جواب
      const o = t.outreach || {send: [], check: []};
      if (!o.send.length && (!o.check.length || Date.now() - last < 3 * 3600 * 1000)) continue;
    }
    // ایتا و تلگرام پنجره‌ی جلو لازم دارند؛ خودکار فقط وقتی کاربر ۱۰ دقیقه پشت سیستم نبوده (و صفحه قفل نیست)
    if ((name === "eitaa" || name === "telegram" || name === "outreach") && (await chrome.idle.queryState(600)) !== "idle") continue;
    await runTask(name); return;
  }
}

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("tick", {periodInMinutes: 30, delayInMinutes: 2}));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("tick", {periodInMinutes: 30, delayInMinutes: 2}));
chrome.alarms.onAlarm.addListener(a => { if (a.name === "tick") tick(); });
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg && msg.type === "run") { runTask(msg.task).then(reply); return true; }
  if (msg && msg.type === "cancel_queue") { chrome.storage.local.set({queue: []}).then(() => reply("صف خالی شد")); return true; }
  if (msg && msg.type === "test") { api("/api/collector/tasks").then(t => reply("اتصال درست است؛ " + t.eitaa_queries.length + " جست‌وجوی ایتا آماده"), e => reply("خطا: " + e.message)); return true; }
});
