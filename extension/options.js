const NAMES = {eitaa: "ایتا: جست‌وجوی سراسری و سنجش کانال‌ها", telegram: "تلگرام: جست‌وجوی کانال", torob_prices: "ترب: مقایسه‌ی قیمت کالاهای کمپین",
  torob_panel: "پنل ترب: کلیک و هزینه (هفتگی)", google: "گوگل: پیدا کردن کانال تلگرام (کم‌اولویت)"};
const $ = s => document.querySelector(s);
const DEF = {panel: "https://content.caravansanat.com", token: "", torobInstance: "292181",
  tasks: {eitaa: true, telegram: true, torob_prices: true, torob_panel: true, google: false}};
const fa = iso => iso ? new Date(iso).toLocaleString("fa-IR") : "هنوز اجرا نشده";
async function showRunning() {
  const {running, queue} = await chrome.storage.local.get(["running", "queue"]);
  const mins = running ? Math.round((Date.now() - running.at) / 60000) : 0;
  $("#running").innerHTML = running
    ? `⏳ الان در حال اجرا: <b>${NAMES[running.name] || running.name}</b> (${mins} دقیقه)<br><span class="small">${running.step || ""}</span>`
    : "✓ الان کاری در حال اجرا نیست";
  $("#queue").textContent = (queue || []).length ? "در صف: " + queue.map(k => NAMES[k] || k).join("، ") : "";
}
setInterval(showRunning, 3000);
async function load() {
  showRunning();
  const {cfg, status} = await chrome.storage.local.get(["cfg", "status"]);
  const c = {...DEF, ...(cfg || {}), tasks: {...DEF.tasks, ...((cfg || {}).tasks || {})}};
  $("#panel").value = c.panel; $("#token").value = c.token; $("#torob").value = c.torobInstance;
  const st = status || {};
  $("#tasks").innerHTML = Object.entries(NAMES).map(([k, n]) => `<div class="row"><div><label style="margin:0;color:inherit"><input type="checkbox" data-t="${k}" ${c.tasks[k] ? "checked" : ""}> ${n}</label>
    <div class="small ${st[k] && !st[k].ok ? "bad" : ""}">${fa(st[k] && st[k].at)}${st[k] ? " — " + st[k].summary : ""}</div></div>
    <button class="ghost" data-run="${k}">اجرای الان</button></div>`).join("");
  document.querySelectorAll("[data-run]").forEach(b => b.onclick = () => { b.disabled = true; b.textContent = "ثبت شد";
    chrome.runtime.sendMessage({type: "run", task: b.dataset.run}, r => { $("#runmsg").textContent = r || ""; load(); });
    setTimeout(() => { $("#runmsg").textContent = $("#runmsg").textContent || "ثبت شد"; showRunning(); }, 800); });
  document.querySelectorAll("[data-t]").forEach(i => i.onchange = save);
}
async function save() {
  const tasks = {}; document.querySelectorAll("[data-t]").forEach(i => tasks[i.dataset.t] = i.checked);
  await chrome.storage.local.set({cfg: {panel: $("#panel").value.trim() || DEF.panel, token: $("#token").value.trim(), torobInstance: $("#torob").value.trim() || DEF.torobInstance, tasks}});
  $("#msg").textContent = "ذخیره شد";
}
$("#save").onclick = save;
$("#test").onclick = async () => { await save(); chrome.runtime.sendMessage({type: "test"}, r => $("#msg").textContent = r); };
load();
