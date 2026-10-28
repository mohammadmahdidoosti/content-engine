from app.channels.telegram import _num, parse_channel_ref, score_channel
from app.plan.planner import _timing, _value_weight
from app.tracking.links import utm_url
from app.woo.store import parse_product


def test_parse_channel_refs():
    assert parse_channel_ref("https://t.me/keshavarzan_dezful") == ("telegram", "keshavarzan_dezful")
    assert parse_channel_ref("t.me/s/abc_def") == ("telegram", "abc_def")
    assert parse_channel_ref("@agri_jiroft") == ("telegram", "agri_jiroft")
    assert parse_channel_ref("https://eitaa.com/keshavarzi") == ("eitaa", "keshavarzi")
    assert parse_channel_ref("ble.ir/news_x", "bale") == ("bale", "news_x")
    assert parse_channel_ref("https://t.me/+AbCdEf") is None
    assert parse_channel_ref("junk!!") is None


def test_num():
    assert _num("12.3K") == 12300 and _num("1.2M") == 1_200_000 and _num("950") == 950


def test_channel_score_flags_fake_members():
    good, flags, _ = score_channel(10_000, 2_500, 3, 0.2, 0.5)
    fake, flags2, _ = score_channel(100_000, 900, 3, 0.2, 0.5)
    assert good > fake and any("فیک" in f for f in flags2) and not flags
    _, _, cpm = score_channel(10_000, 2_000, price_toman=500_000)
    assert cpm == 250_000


def test_timing_prefers_next_month_peak():
    seg = {"peak": [9], "good": [8]}
    t_next, _ = _timing(seg, 8)     # آبان، اوج آذر
    t_now, _ = _timing(seg, 9)
    t_off, _ = _timing(seg, 3)
    assert t_next > t_now > t_off == 0


def test_value_weight_favors_big_machines():
    assert _value_weight(150_000_000, None) > _value_weight(1_000_000, None)


def test_utm_url_encodes_persian_and_keeps_code():
    u = utm_url("https://caravansanat.com/product-category/پمپ-شناور/", "agri_dezful", "C7XK", "K2AB")
    assert "utm_content=K2AB" in u and "utm_campaign=C7XK" in u and "%D9%BE" in u


def test_parse_product():
    p = parse_product({"id": 5, "name": "پمپ &amp; تابلو", "prices": {"price": "120000", "regular_price": "150000",
                       "currency_minor_unit": 0}, "is_in_stock": False, "images": [{"thumbnail": "t.webp"}],
                       "categories": [{"id": 9}], "attributes": [{"name": "برند", "terms": [{"name": "لئو"}]}],
                       "short_description": "<p>کوتاه</p>"})
    assert p["name"] == "پمپ & تابلو" and p["price"] == 120000 and p["brand"] == "لئو"
    assert p["in_stock"] is False and p["category_ids"] == [9] and p["short_desc"] == "کوتاه"


def test_weather_rules():
    from app.plan.weather import _check
    d = {"time": ["d1", "d2", "d3", "d4"], "temperature_2m_min": [2, -5, -1, 0],
         "temperature_2m_max": [12, 10, 41, 15], "precipitation_sum": [0, 15, 18, 10]}
    kinds = {a["kind"] for a in _check(d)}
    assert kinds == {"frost", "heat", "rain"}
    assert _check({"time": ["a"], "temperature_2m_min": [5], "temperature_2m_max": [25], "precipitation_sum": [1]}) == []


def test_city_index_alignment():
    from types import SimpleNamespace
    from app.plan.cities import city_index
    p = SimpleNamespace(id="X", segments=[{"provinces": ["خوزستان", "کرمان (جنوب)"],
                                           "cities": "دزفول، شوش | جیرفت، کهنوج"}])
    idx = city_index([p])
    assert idx["دزفول"]["province"] == "خوزستان" and idx["جیرفت"]["province"] == "کرمان"
    assert idx["کهنوج"]["entries"][0] == ("X", 0, 3)


def test_offline_parse_numbers_and_summary():
    from app.tracking.offline import _summarize, classify, num, province
    assert num("1.400.000") == 1_400_000
    assert num("200,000") == 200_000
    assert num("1) 3.500.000\n2) 4.500.000") == 8_000_000
    assert num("378.235\n-14%") == 378_235
    assert num("حدود 70 هزار تومان") == 70_000
    assert province("کرج") == "البرز" and province("ارومیه") == "آذربایجان غربی" and province(None, "رشت") == "گیلان"
    assert classify("پمپ دنده ای گروه سه") == "HYD_GEAR_VANE"
    assert classify("قطره چکان نتافیم") == "IRRIG"
    inv = [{"ym": "1405/02", "prov": "تهران", "plan": "IRRIG", "rev": 10_000_000, "profit": 1_500_000, "ship": "تیپاکس"}] * 4
    inv += [{"ym": "1405/03", "prov": "فارس", "plan": "PNEU", "rev": 20_000_000, "profit": 1_000_000, "ship": None}] * 4
    calls = [{"sheet": "s", "ym": "1404/09", "plan": "PNEU", "buy": i % 5 == 0} for i in range(25)]
    s = _summarize(inv, calls, [])
    assert s["invoices"] == 8 and s["plans"]["IRRIG"]["margin_pct"] == 15.0
    assert s["plans"]["PNEU"]["call_conv_pct"] == 20.0
    assert s["plans"]["PNEU"]["max_cost_per_call"] == 200_000
    assert s["plan_prov"]["IRRIG|تهران"] == 4


def test_offline_demand_baseline():
    from app.tracking.offline import _demand, province
    assert province(None, "کوهدشت") == "لرستان" and province(None, "رباط کریم") == "تهران"
    rows = [{"day": "1405/07/27", "ym": "1405/07", "prov": "تهران", "plan": "IRRIG"}] * 20
    rows += [{"day": "1405/07/28", "ym": "1405/07", "prov": "فارس", "plan": "OTHER"}] * 10
    rows += [{"day": "1499/01/01", "ym": None, "prov": "نامشخص", "plan": "OTHER"}]   # تاریخ اشتباه آینده
    d = _demand(rows)
    assert d["days"] == 2 and d["per_day"] == 15.0 and d["plan_prov"] == {"IRRIG|تهران": 20}


def test_external_gsc():
    from app.tracking import external
    g = external._gsc([["قیمت شیر پروانه ای", 0, 120, "0%", 12.0], ["تست شیر برقی کنیستر", 35, 2535, "1.4%", 7.7]], {})
    assert g["commercial"]["queries"] == 1 and g["quick_wins"][0]["q"] == "تست شیر برقی کنیستر"
    assert external._slug("https://caravansanat.com/product/%D9%88%D8%A7%D9%86/") == "وان"


def test_torob_season_index_removes_growth(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from app.config import get_settings
    from app.db import session as sess
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    for attr in ("_engine", "_Session", "_session_factory"):
        if hasattr(sess, attr):
            monkeypatch.setattr(sess, attr, None)
    if hasattr(sess.get_engine, "cache_clear"):
        sess.get_engine.cache_clear()
    sess.init_db()
    from app.tracking.external import _torob_yearly
    urls = ["https://caravansanat.com/product/a", "https://caravansanat.com/product/b"]
    rows = []
    for i, m in enumerate(["1404-08", "1404-09", "1404-10", "1404-11"]):
        grow = (i + 1) * 100                       # فروشگاه رشد می‌کند
        rows.append([m, 0, grow, 0])               # a: ثابت نسبت به رشد
        rows.append([m, 1, grow * (3 if m == "1404-10" else 1), 0])   # b: دی پرتقاضا
    y = _torob_yearly(rows, urls, {})
    un = y["plans"][0]
    assert y["months"] == ["1404-08", "1404-09", "1404-10", "1404-11"] and un["plan_id"] == "_unmatched"
    assert y["month_clicks"][2] == 300 + 900


def test_backup_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/b.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    from app import backup
    from app.db.session import get_setting, set_setting
    backup._tables()
    set_setting("x.test", {"a": 1})
    data, meta = backup.export_bytes()
    set_setting("x.test", {"a": 2})
    r = backup.restore_bytes(data)
    assert get_setting("x.test") == {"a": 1} and r["restored"]["settings"] >= 1


def test_hunter_candidates_and_fit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/h.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from app.channels import hunter
    from app.db.marketing import Channel
    r = hunter.add_candidates([("telegram", "Rasht_Kesht"), ("telegram", "rasht_kesht"), ("telegram", "abc_news")], "snowball", "x")
    assert r["added"] == 2 and r["bumped"] == 1
    ch = Channel(platform="telegram", username="a", provinces=["گیلان"], plan_ids=["IRRIG"], audience="farmer", relevant=True)
    assert hunter._fit(ch, "IRRIG", "WATER", "گیلان") == 1.0
    ch2 = Channel(platform="telegram", username="b", provinces=["گیلان"], plan_ids=[], audience="local_news", relevant=True)
    assert hunter._fit(ch2, "PNEU", "PNEU", "گیلان") == 0.6 and hunter._fit(ch2, "PNEU", "PNEU", "تهران") == 0
    g = hunter.ingest_candidates([["https://t.me/new_chan", "site:t.me کشاورزی رشت"], ["https://eitaa.com/rasht_agri", "q"]])
    assert g["added"] == 2


def test_call_log(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from app.tracking import calls
    r = calls.add({"city": "دزفول", "product": "قطره چکان نتافیم", "source": "telegram_ad", "ad_code": "ab12",
                   "outcome": "bought", "amount_toman": "12,000,000", "note": "تماس از 09121234567 گرفت"})
    assert r["province"] == "خوزستان" and r["plan_id"] == "IRRIG" and r["ad_code"] == "AB12" and r["amount_toman"] == 12_000_000
    assert "0912" not in (r["note"] or "")
    assert calls.by_ad_code()["AB12"]["bought"] == 1
    assert calls.plan_province_counts()[("IRRIG", "خوزستان")] == 1.0
    st = calls.stats()
    assert st["total"] == 1 and st["sources"][0]["conv_pct"] == 100.0


def test_lift_window(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/l.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from collections import Counter
    from datetime import date, timedelta
    from app.tracking.lift import _avg, _j2g
    c = Counter({date(2026, 10, 1) + timedelta(days=i): (2 if i < 7 else 5) for i in range(14)})
    assert _avg(c, date(2026, 10, 1), 7, date(2026, 12, 1))[0] == 2 and _avg(c, date(2026, 10, 8), 7, date(2026, 12, 1))[0] == 5
    assert _j2g("1405/08/04") == date(2026, 10, 26)
    from app.tracking import lift
    assert lift.placement_lift() == [] and set(lift.baselines()) == {"calls", "orders", "torob"}


def test_pricing_stops_on_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/p.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    import httpx
    from app.db.marketing import Product
    from app.plan import pricing
    with sess.get_session() as db:
        for i in (1, 2, 3):
            db.add(Product(id=i, name=f"پمپ {i}", price=1000, in_stock=True))
        db.commit()
    calls = []

    def boom(q, timeout=20):
        calls.append(q)
        raise httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://x"), response=httpx.Response(429))
    monkeypatch.setattr(pricing, "search", boom)
    monkeypatch.setattr(pricing.time, "sleep", lambda s: None)
    st = pricing.check_products([1, 2, 3])
    assert len(calls) == 1 and "429" in st["stopped"] and st["checked"] == 0

    def blocked(q, timeout=20):
        calls.append(q)
        raise httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://x"), response=httpx.Response(490))
    monkeypatch.setattr(pricing, "search", blocked)
    calls.clear()
    st = pricing.check_products([1, 2, 3])
    assert len(calls) == 1 and "490" in st["stopped"]
    with sess.get_session() as db:
        assert db.query(pricing.PriceCheck).count() == 0      # خطا نتیجه‌ی قبلی را خراب نمی‌کند


def test_pricing_browser_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/pb.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from app.db.marketing import Product
    from app.plan import pricing
    with sess.get_session() as db:
        db.add(Product(id=7, name="پمپ شناور 3 اسب لئو 4XRM4/24", price=50_000_000, in_stock=True))
        db.commit()
    res = [{"name1": "پمپ شناور 3 اسب لئو 4XRM4/24", "price": 60_000_000, "shop_text": "در ۵ فروشگاه",
            "web_client_absolute_url": "/p/x"}]
    st = pricing.ingest_browser([[7, res], ["bad", []]])
    assert st["checked"] == 1 and st["cheaper"] == 1 and st["skipped"] == 1


def test_ad_problems():
    from app.plan.campaigns import ad_problems
    assert ad_problems("برای برخی کالاها خرید اقساطی هم داریم؛ ضمانت اصالت") == []
    assert len(ad_problems("خرید نقدی تخفیف دارد، اقساطی هم موجوده")) == 2
