"""
CRM کاروان صنعت — سرور.
اجرا: python -m crm.server   (پورت PORT، پیش‌فرض 10001)

متغیرها (همه در Coolify، فقط صاحب فروشگاه واردشان می‌کند):
  CRM_DATABASE_URL     دیتابیس جدا (Postgres)
  CRM_SECRET           کلید امضای نشست‌ها (حداقل ۳۲ حرف)
  CRM_ADMIN_USER / CRM_ADMIN_PASSWORD   اولین کاربر (فقط وقتی هنوز کاربری نیست)
  CRM_STATS_TOKEN      برای پنل بازاریابی (فقط عدد، بدون نام و شماره)
  BALE_BOT_TOKEN       ربات بله برای یادآور صبحگاهی
  CRM_PUBLIC_URL       آدرس خود CRM (برای لینک در یادآور)
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from crm import bale, core
from crm.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("crm")
WEB = Path(__file__).parent / "web"
SECRET = (os.environ.get("CRM_SECRET") or "").encode()
SESSION_DAYS = 30
_fails: dict[str, list[float]] = {}


class HttpError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def _sign(payload: str) -> str:
    return hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()


def make_session(username: str) -> str:
    p = base64.urlsafe_b64encode(json.dumps({"u": username, "exp": int(time.time()) + SESSION_DAYS * 86400}).encode()).decode()
    return f"{p}.{_sign(p)}"


def read_session(cookie: str) -> str | None:
    if not SECRET or not cookie or "." not in cookie:
        return None
    p, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(_sign(p), sig):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(p.encode()))
    except Exception:  # noqa: BLE001
        return None
    return d["u"] if d.get("exp", 0) > time.time() else None


def _int(v, name):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise HttpError(400, f"{name} نامعتبر") from None


def _val(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ValueError as e:
        raise HttpError(400, str(e)) from None


# ---------------- مسیرها ----------------
def r_meta(req, m):
    from crm.db import get_setting
    from crm.db import User, session
    with session() as db:
        u = db.query(User).filter(User.username == req.user).first()
    return {"user": req.user, "name": (u.name if u else "") or req.user, "role": u.role if u else "sales", "stages": core.STAGES, "lost_reasons": core.LOST_REASONS, "sources": core.SOURCES,
            "bale": {"token": bool(bale._token()), "chats": get_setting("bale.chats") or [], "code": bale.link_code()},
            "categories": _categories()}


def _categories():
    from sqlalchemy import func
    from crm.db import Deal, session
    with session() as db:
        rows = db.query(Deal.category, func.count()).filter(Deal.category.isnot(None)).group_by(Deal.category).all()
    return [c for c, _ in sorted(rows, key=lambda x: -x[1])][:60]


ROUTES = [
    ("GET", r"/api/meta", r_meta),
    ("GET", r"/api/today", lambda req, m: core.today_view(min(_int(req.q("n") or 100, "n"), 2000))),
    ("GET", r"/api/deals", lambda req, m: core.pipeline(req.q("stage"), req.q("q") or "")),
    ("POST", r"/api/deals", lambda req, m: _val(core.quick_add, req.body(), req.user)),
    ("GET", r"/api/deals/(\d+)", lambda req, m: _val(core.deal_detail, _int(m.group(1), "id"))),
    ("POST", r"/api/deals/(\d+)", lambda req, m: _val(core.update_deal, _int(m.group(1), "id"), req.body(), req.user)),
    ("POST", r"/api/deals/(\d+)/delete", lambda req, m: core.delete_deal(_int(m.group(1), "id"))),
    ("GET", r"/api/contacts/lookup", lambda req, m: core.find_contact(req.q("phone") or "")),
    ("GET", r"/api/contacts", lambda req, m: core.search_contacts(req.q("q") or "", req.q("tag") or "", req.q("category") or "",
                                                                  [int(x) for x in (req.q("months") or "").split(",") if x.isdigit()])),
    ("POST", r"/api/contacts/(\d+)", lambda req, m: _val(core.update_contact, _int(m.group(1), "id"), req.body())),
    ("GET", r"/api/report", lambda req, m: core.report(_int(req.q("days") or 90, "days"), req.q("import") == "1")),
    ("POST", r"/api/bale/link", lambda req, m: _val(bale.link)),
    ("POST", r"/api/bale/unlink", lambda req, m: bale.unlink(req.body().get("id"))),
    ("POST", r"/api/bale/test", lambda req, m: {"sent": _val(bale.send_morning, os.environ.get("CRM_PUBLIC_URL", ""))}),
    ("POST", r"/api/users", lambda req, m: req.admin() and _val(core.add_user, **{k: req.body().get(k) for k in ("username", "password", "name", "role")})),
    ("POST", r"/api/password", lambda req, m: _val(core.change_password, req.user, req.body().get("old"), req.body().get("new"))),
]
_COMPILED = [(meth, re.compile("^" + p + "$"), fn) for meth, p, fn in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "caravan-crm"
    user = None
    _body = None

    def q(self, k):
        v = parse_qs(urlparse(self.path).query).get(k)
        return v[0] if v else None

    def raw(self, limit=25 * 1024 * 1024) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise HttpError(413, "فایل خیلی بزرگ است")
        return self.rfile.read(n) if n else b""

    def body(self) -> dict:
        if self._body is None:
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise HttpError(415, "JSON لازم است")
            try:
                self._body = json.loads(self.raw(1024 * 1024) or b"{}")
            except json.JSONDecodeError:
                raise HttpError(400, "JSON نامعتبر") from None
        return self._body

    def admin(self):
        from crm.db import User, session
        with session() as db:
            u = db.query(User).filter(User.username == self.user).first()
        if not u or u.role != "admin":
            raise HttpError(403, "فقط مدیر")
        return True

    def client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _send(self, code, data: bytes, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8", extra)

    def log_message(self, fmt, *args):
        if self.command == "POST":
            log.info("%s %s", self.command, urlparse(self.path).path)

    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _set_cookie(self, val, max_age):
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") == "https") else ""
        return {"Set-Cookie": f"crm={val}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"}

    def _dispatch(self, method):
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path in ("/", "/index.html") and method in ("GET", "HEAD"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/manifest.webmanifest":
                return self._send(200, (WEB / "manifest.webmanifest").read_bytes(), "application/manifest+json")
            fm = re.fullmatch(r"/fonts/([A-Za-z0-9-]+\.woff2)", path)
            if fm and (WEB / "fonts" / fm.group(1)).is_file():
                return self._send(200, (WEB / "fonts" / fm.group(1)).read_bytes(), "font/woff2",
                                  {"Cache-Control": "public, max-age=31536000, immutable"})
            if path == "/health":
                return self._json(200, {"ok": True})
            if path == "/api/stats" and method == "GET":         # برای پنل بازاریابی: فقط عدد
                tok = os.environ.get("CRM_STATS_TOKEN", "")
                if len(tok) < 16 or not hmac.compare_digest(self.headers.get("X-CRM-Token", ""), tok):
                    raise HttpError(401, "unauthorized")
                return self._json(200, core.stats_for_marketing(int(self.q("days") or 180)))
            if path == "/api/login" and method == "POST":
                ip = self.client_ip()
                fails = [t for t in _fails.get(ip, []) if time.time() - t < 900]
                if len(fails) >= 8:
                    raise HttpError(429, "تلاش زیاد؛ ۱۵ دقیقه‌ی دیگر")
                b = self.body()
                u = core.login(b.get("username"), b.get("password"))
                if not u or not SECRET:
                    _fails[ip] = fails + [time.time()]
                    raise HttpError(401, "نام کاربری یا رمز درست نیست" if SECRET else "CRM_SECRET تنظیم نشده")
                _fails.pop(ip, None)
                return self._json(200, {"ok": True, "user": u.username}, self._set_cookie(make_session(u.username), SESSION_DAYS * 86400))
            if path == "/api/logout" and method == "POST":
                return self._json(200, {"ok": True}, self._set_cookie("", 0))
            self.user = read_session(self._cookie("crm") or "")
            if path.startswith("/api/") and not self.user:
                raise HttpError(401, "وارد نشده‌اید")
            if path == "/api/import/leads" and method == "POST":
                self.admin()
                return self._json(200, _val(core.import_leads, self.raw()))
            for meth, rx, fn in _COMPILED:
                mm = rx.match(path)
                if mm and meth == method:
                    return self._json(200, fn(self, mm))
            raise HttpError(404, "پیدا نشد")
        except HttpError as e:
            return self._json(e.code, {"error": e.msg})
        except Exception:  # noqa: BLE001
            log.exception("خطا در %s", path)
            return self._json(500, {"error": "خطای داخلی"})

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        if self.headers.get("Origin") and self.headers.get("Host") and \
                urlparse(self.headers["Origin"]).netloc != self.headers["Host"]:
            return self._json(403, {"error": "origin"})
        self._dispatch("POST")


def main():
    init_db()
    core.ensure_admin()
    if len(SECRET) < 32:
        log.warning("CRM_SECRET تنظیم نشده یا کوتاه است؛ ورود ممکن نیست.")
    bale.loop(os.environ.get("CRM_PUBLIC_URL", ""))
    port = int(os.environ.get("PORT", "10001"))
    log.info("CRM روی پورت %s", port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
