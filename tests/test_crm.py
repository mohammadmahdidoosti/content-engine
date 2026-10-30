import json
import os
import threading
import urllib.request
from datetime import timedelta

import pytest


@pytest.fixture()
def crm(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{tmp_path}/crm.db")
    from crm import db
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_Session", None)
    db.init_db()
    from crm import core
    return core


def test_phone_and_dates(crm):
    assert crm.norm_phone("۰۹۱۲ ۳۴۵ ۶۷۸۹") == "09123456789"
    assert crm.norm_phone("+98 912 345 6789") == "09123456789"
    assert crm.norm_phone("9123456789") == "09123456789"
    assert crm.norm_phone("abc") is None
    d = crm.parse_day("۱۴۰۵/۰۸/۱۰")
    assert d.isoformat() == "2026-11-01" and crm.jalali(d) == "1405/08/10"


def test_deal_flow_and_reports(crm):
    r = crm.quick_add({"phone": "09121112233", "name": "علی", "city": "دزفول", "title": "الکتروموتور ۵٫۵", "category": "الکتروموتور",
                       "ad_code": "cc2y", "follow_days": "0"}, "sara")
    again = crm.quick_add({"phone": "0912 111 2233", "title": "پمپ آب", "source": "ترب"}, "sara")
    assert again["contact_id"] == r["contact_id"]                         # همان مشتری با همان شماره
    with pytest.raises(ValueError):
        crm.quick_add({"phone": "", "title": "x"})
    v = crm.today_view()
    assert [d["id"] for d in v["due"]] == [r["deal_id"]] and len(v["no_followup"]) == 1
    with pytest.raises(ValueError):                                         # انصراف بدون دلیل
        crm.update_deal(again["deal_id"], {"stage": "lost"})
    crm.update_deal(again["deal_id"], {"stage": "lost", "lost_reason": "گران بود"})
    crm.update_deal(r["deal_id"], {"stage": "won", "amount": "۴۵,۰۰۰,۰۰۰", "log": "فاکتور زد", "log_kind": "call"}, "sara")
    det = crm.deal_detail(r["deal_id"])
    assert det["amount"] == 45_000_000 and det["next_at"] is None and len(det["activities"]) == 3
    assert det["contact"]["phone"] == "09121112233" and len(det["other_deals"]) == 1
    rep = crm.report(30)
    src = {x["key"]: x for x in rep["by_source"]}
    assert src["CC2Y"]["won"] == 1 and src["ترب"]["lost"] == 1 and rep["lost_reasons"] == [("گران بود", 1)]
    st = crm.stats_for_marketing()
    assert st["codes"]["CC2Y"] == {"deals": 1, "won": 1, "revenue": 45_000_000, "profit": 0}
    assert "09121112233" not in json.dumps(st, ensure_ascii=False) and "علی" not in json.dumps(st, ensure_ascii=False)
    # مشتری فصلی: خریداران «الکتروموتور» در ماه جاری
    import jdatetime
    m = jdatetime.date.today().month
    assert [c["name"] for c in crm.search_contacts(category="الکتروموتور", months=[m])] == ["علی"]
    assert crm.search_contacts(category="الکتروموتور", months=[(m % 12) + 1]) == []
    crm.update_deal(again["deal_id"], {"stage": "followup", "follow_days": "3"})
    assert crm.deal_detail(again["deal_id"])["next_at"] == (crm.today() + timedelta(days=3)).isoformat()


def test_users_and_session(crm, monkeypatch):
    monkeypatch.setenv("CRM_ADMIN_USER", "mahdi")
    monkeypatch.setenv("CRM_ADMIN_PASSWORD", "long-enough-pass")
    crm.ensure_admin()
    assert crm.login("Mahdi", "long-enough-pass") and not crm.login("mahdi", "wrong-pass-123")
    crm.add_user("sara", "another-pass-1", "سارا")
    with pytest.raises(ValueError):
        crm.add_user("sara", "another-pass-1")
    crm.change_password("sara", "another-pass-1", "new-pass-12345")
    assert crm.login("sara", "new-pass-12345")


def test_http_auth_and_stats(crm, monkeypatch):
    monkeypatch.setenv("CRM_ADMIN_USER", "mahdi")
    monkeypatch.setenv("CRM_ADMIN_PASSWORD", "long-enough-pass")
    monkeypatch.setenv("CRM_STATS_TOKEN", "s" * 20)
    import importlib
    import crm.server as srv
    monkeypatch.setattr(srv, "SECRET", b"x" * 40)
    crm.ensure_admin()
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def req(path, body=None, headers=None):
        r = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json", **(headers or {})}, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers
    assert req("/api/today")[0] == 401
    assert req("/api/stats")[0] == 401
    assert req("/api/stats", headers={"X-CRM-Token": "s" * 20})[0] == 200
    assert req("/api/login", {"username": "mahdi", "password": "bad-password"})[0] == 401
    code, d, h = req("/api/login", {"username": "mahdi", "password": "long-enough-pass"})
    assert code == 200 and "HttpOnly" in h["Set-Cookie"]
    cookie = {"Cookie": h["Set-Cookie"].split(";")[0]}
    assert req("/api/deals", {"phone": "09120000000", "title": "تست"}, cookie)[0] == 200
    code, d, _ = req("/api/today", headers=cookie)
    assert code == 200 and len(d["no_followup"]) == 1
    assert req("/api/deals", {"phone": "1", "title": "x"}, {**cookie, "Origin": "https://evil.example"})[0] == 403
    httpd.shutdown()
    importlib.reload(srv)


LEADS = "/mnt/user-data/attachments/8c04216c-f1f3-52e9-b52f-6f8776a8743a/5cfa10c6-Leads.xlsx"


@pytest.mark.skipif(not os.path.exists(LEADS), reason="فایل Leads در این محیط نیست")
def test_import_real_leads(crm):
    data = open(LEADS, "rb").read()
    r = crm.import_leads(data)
    assert r["added"] > 500 and r["invoices_sheet"] == "مجموع فاکتورها"
    assert crm.import_leads(data)["added"] == 0                      # دوباره وارد نمی‌شود
    rep = crm.report(3650)
    assert rep["won"] > 50
    assert crm.today_view()["due"] == [] and crm.stats_for_marketing()["codes"] == {}   # سابقه در آمار جاری نمی‌آید


T1405 = "/mnt/user-data/attachments/8c04216c-f1f3-52e9-b52f-6f8776a8743a/6aa7df04-_____1405.xlsx"


@pytest.mark.skipif(not os.path.exists(T1405), reason="فایل تماس ۱۴۰۵ در این محیط نیست")
def test_import_1405_file(crm):
    data = open(T1405, "rb").read()
    r = crm.import_leads(data)
    assert r["opened"] > 50 and r["contacts_only"] > 50
    assert crm.import_leads(data)["opened"] == 0 and crm.import_leads(data)["contacts_only"] == 0
    v = crm.today_view()
    assert len(v["no_followup"]) >= 50                     # درخواست‌های باز برای پیگیری
