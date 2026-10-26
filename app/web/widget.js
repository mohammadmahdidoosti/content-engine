/* پنجره‌ی گفت‌وگوی «دستیار کاروان صنعت» برای سایت.
   نصب: <script src="https://SERVICE/widget.js" defer></script> را در فوتر وردپرس اضافه کنید. */
(function () {
  if (window.__ceWidget) return; window.__ceWidget = 1;
  var BASE = (document.currentScript && document.currentScript.src || "").replace(/\/widget\.js.*$/, "");
  var css = "#ce-btn{position:fixed;bottom:20px;left:20px;z-index:99999;width:58px;height:58px;border-radius:50%;border:0;background:#2c6a4d;color:#fff;font-size:26px;box-shadow:0 6px 20px rgba(0,0,0,.25);cursor:pointer}"
    + "#ce-box{position:fixed;bottom:88px;left:20px;z-index:99999;width:340px;max-width:calc(100vw - 32px);height:460px;max-height:calc(100vh - 120px);background:#fff;color:#1d2621;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.25);display:none;flex-direction:column;overflow:hidden;font-family:Vazirmatn,Tahoma,sans-serif;direction:rtl}"
    + "#ce-box.on{display:flex}#ce-h{background:#2c6a4d;color:#fff;padding:12px 14px;font-weight:700;font-size:14px;display:flex;justify-content:space-between;align-items:center}"
    + "#ce-h button{background:none;border:0;color:#fff;font-size:18px;cursor:pointer}#ce-m{flex:1;overflow-y:auto;padding:12px;background:#f3f1ec;font-size:13.5px;line-height:1.8}"
    + ".ce-q,.ce-a{margin:0 0 10px;padding:8px 11px;border-radius:12px;max-width:88%;white-space:pre-wrap;word-wrap:break-word}.ce-q{background:#2c6a4d;color:#fff;margin-right:auto;border-bottom-left-radius:4px}"
    + ".ce-a{background:#fff;border:1px solid #e1dccf;border-bottom-right-radius:4px}.ce-p{display:block;margin-top:6px;padding:6px 8px;border-radius:9px;background:#e1eee6;color:#1d4634;text-decoration:none;font-size:12.5px}"
    + "#ce-f{display:flex;gap:6px;padding:8px;border-top:1px solid #e1dccf;background:#fff}#ce-i{flex:1;border:1px solid #e1dccf;border-radius:10px;padding:8px 10px;font:inherit;font-size:13.5px;outline:none}"
    + "#ce-s{background:#2c6a4d;color:#fff;border:0;border-radius:10px;padding:0 14px;font:inherit;font-weight:700;cursor:pointer}#ce-s:disabled{opacity:.5}";
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  var btn = document.createElement("button"); btn.id = "ce-btn"; btn.setAttribute("aria-label", "پرسش قیمت"); btn.textContent = "💬";
  var box = document.createElement("div"); box.id = "ce-box";
  box.innerHTML = '<div id="ce-h"><span>پرسش قیمت و مشاوره</span><button aria-label="بستن">×</button></div><div id="ce-m"></div>'
    + '<form id="ce-f"><input id="ce-i" maxlength="300" placeholder="اسم یا مدل کالا…" autocomplete="off"><button id="ce-s">ارسال</button></form>';
  document.body.appendChild(btn); document.body.appendChild(box);
  var m = box.querySelector("#ce-m"), f = box.querySelector("#ce-f"), inp = box.querySelector("#ce-i"), send = box.querySelector("#ce-s");
  var sid = (function () { try { var s = localStorage.getItem("ce_sid"); if (!s) { s = Math.random().toString(36).slice(2); localStorage.setItem("ce_sid", s); } return s; } catch (e) { return ""; } })();
  function add(cls, text) { var d = document.createElement("div"); d.className = cls; d.textContent = text; m.appendChild(d); m.scrollTop = m.scrollHeight; return d; }
  function toggle() { box.classList.toggle("on"); if (box.classList.contains("on")) { if (!m.childNodes.length) add("ce-a", "سلام 👋 اسم یا مدل کالایی که می‌خواهید را بنویسید تا قیمت روز و لینک ثبت سفارش را بفرستم."); inp.focus(); } }
  btn.onclick = toggle; box.querySelector("#ce-h button").onclick = toggle;
  f.onsubmit = function (e) {
    e.preventDefault(); var q = inp.value.trim(); if (!q) return;
    add("ce-q", q); inp.value = ""; send.disabled = true; var w = add("ce-a", "در حال جست‌وجو…");
    fetch(BASE + "/public/ask", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({q: q, session: sid, page: location.pathname})})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        w.textContent = d.answer || d.error || "خطایی رخ داد.";
        (d.products || []).forEach(function (p) { if (!p.url) return; var a = document.createElement("a"); a.className = "ce-p"; a.href = p.url;
          a.textContent = "🛒 " + p.name + (p.price ? " — " + Number(p.price).toLocaleString("fa-IR") + " تومان" : ""); w.appendChild(a); });
        m.scrollTop = m.scrollHeight;
      })
      .catch(function () { w.textContent = "ارتباط برقرار نشد؛ لطفاً دوباره امتحان کنید."; })
      .then(function () { send.disabled = false; });
  };
})();
