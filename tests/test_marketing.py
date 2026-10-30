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


def test_pricing_rejects_other_models_and_replicas():
    from app.plan.pricing import compare, similarity
    assert similarity("شمشاد زن اولیومک HC ۲۴۷ P", "شمشاد زن اولیومک مدل HC ۷۵۰ E") == 0.0
    res = compare("اره زنجیری اشتیل مدل MS230", 51_000_000, [
        {"name1": "اره موتوری اشتیل دیزاین MS230", "price": 35_000_000, "shop_text": "در ۳ فروشگاه"},
        {"name1": "اره زنجیری اشتیل مدل MS230 بنزینی", "price": 95_000_000, "shop_text": "در ۲ فروشگاه"}])
    assert res["market_min"] == 95_000_000 and res["status"] == "cheaper"


def test_channel_metrics():
    from datetime import datetime, timedelta, timezone
    from app.channels.metrics import ad_contacts, classify_post, compute, parse_count, score
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    assert classify_post("عضو شو 👇 t.me/+abc") == "join_ad"
    assert classify_post("فروش ویژه پمپ؛ سفارش 09121234567") == "product_ad"
    assert classify_post("بارش باران در رشت") == "content"
    posts = []
    for i in range(20):
        ad = i % 5 == 0
        posts.append({"views": (400 if ad else 1000 + 37 * i), "ts": now - timedelta(days=5) + timedelta(hours=5 * i),
                      "text": "#تبلیغ فروش ویژه" if ad else "خبر روز", "reactions": 3})
    m = compute(posts, 10000, "x", now=now)
    assert m["median_views"] and 0.25 <= m["ad_view_ratio"] <= 0.45 and m["expected_ad_views"] < m["median_views"]
    assert m["kinds"]["product_ad"] == 4 and m["ad_life_hours"] == 5.0
    s, flags, cpm = score(m, 10000, 500_000)
    assert 0 < s <= 100 and cpm and any("تبلیغاتی" in f for f in flags)
    assert parse_count("1.39K") == 1390 and parse_count("1.6میلیون") == 1_600_000 and parse_count("۱۸٫۸K") == 18800
    assert ad_contacts("سفارش تبلیغ: @Newsadmin1 09018373801") == ["@Newsadmin1", "09018373801"]


def test_ingest_channel_posts(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/ip.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from app.channels.hunter import ingest_channel_posts
    from app.db.marketing import Channel
    posts = [{"views": str(900 + i), "ts": str(1793000000 + 3600 * 6 * i), "text": "خبر"} for i in range(12)]
    r = ingest_channel_posts([{"platform": "eitaa", "username": "dez_news", "title": "اخبار دزفول", "members": "5000",
                               "posts": posts, "query": "کشاورزی دزفول"}, {"platform": "x"}])
    assert r == {"added": 1, "updated": 0, "skipped": 1, "new_candidates": 0}
    with sess.get_session() as db:
        ch = db.query(Channel).one()
        assert ch.platform == "eitaa" and ch.expected_ad_views and ch.score > 0 and ch.status == "checked"


def test_collector_routes_whitelist():
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("srv", "scripts/server.py")
    srv = importlib.util.module_from_spec(spec)
    sys.modules["srv"] = srv
    spec.loader.exec_module(srv)
    assert ("POST", "/api/ext/import") in srv.COLLECTOR_ROUTES and ("GET", "/api/campaigns") not in srv.COLLECTOR_ROUTES


def test_hunter_noise_and_eitaa_snowball(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/ns.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from app.channels import hunter
    from app.db.marketing import Channel, ChannelCandidate
    assert hunter.is_noise("انجمن علمي عمران") and hunter.is_noise("x", "کانال دانشجویان")
    assert not hunter.is_noise("اخبار ورامین", "خبر، فیلم و عکس شهر و دانشگاه") and not hunter.is_noise("کشاورزی رشت")
    posts = [{"views": "900", "ts": str(1793000000 + 3600 * 6 * i), "text": f"کانال خوب @agri_rasht{i % 2} و eitaa.com/bazar_rasht"}
             for i in range(10)]
    r = hunter.ingest_channel_posts([{"platform": "eitaa", "username": "rasht_news", "title": "اخبار رشت", "members": "9000",
                                      "posts": posts}], "eitaa")
    assert r["new_candidates"] == 3
    assert {"agri_rasht0", "agri_rasht1", "bazar_rasht"} <= {c["username"] for c in hunter.eitaa_todo()}
    # دانشجویی ← بی‌ربط و بدون گلوله‌برفی
    r = hunter.ingest_channel_posts([{"platform": "eitaa", "username": "civil_uni", "title": "انجمن علمی عمران", "members": "3000",
                                      "posts": [{**p, "text": "@student_x1 جزوه"} for p in posts]}], "eitaa")
    assert r["new_candidates"] == 0
    assert hunter.mark_candidates([["eitaa", "agri_rasht0", False, "عضو کم"]]) == {"marked": 1}
    hunter.ingest_channel_posts([{"platform": "eitaa", "username": "bazar_rasht", "title": "بازار رشت", "members": "2000",
                                  "posts": posts, "query": "rasht_news"}], "snowball")
    with sess.get_session() as db:
        st = {c.username: c.status for c in db.query(ChannelCandidate).all()}
        assert st["agri_rasht0"] == "rejected" and st["bazar_rasht"] == "added" and st["agri_rasht1"] == "new"
        assert db.query(Channel).filter(Channel.username == "civil_uni").one().relevant is False
        # والد کم‌بازده: ۵ بچه‌ی بی‌ربط ← نامزدهای منتظرش رد می‌شوند
        for i in range(5):
            db.add(Channel(platform="telegram", username=f"kid{i}", source="snowball", found_via="rasht_news",
                           relevant=False, tagged_at=hunter._now(), status="checked"))
        db.commit()
    assert hunter.cleanup()["dropped_candidates"] == 1


def test_torob_search_query():
    from app.plan.pricing import search_query as q
    assert q("اره زنجیری اشتیل مدل MS 230", "اشتیل") == "اره زنجیری اشتیل MS 230"
    assert q("دریل بتن کن بوش GBH 2-26 DRE", "بوش").startswith("دریل بتن کن بوش GBH 2-26")
    assert q("شمشاد زن شارژی ماکیتا مدل UH201DZ با تیغه ۲۰ سانتی") == "شمشاد زن شارژی UH201DZ"
    assert q("پمپ آب کشاورزی سه اینچ") == "پمپ آب کشاورزی سه اینچ"
    assert q("گاوآهن چهار خیش طرح ترک با چرخ فولادی (ممتاز) مدل AKMA–TU4–12 برند اکما", "اکما") == "گاوآهن چهار خیش اکما AKMA-TU4-12"
    assert q("فرز آهنگری ماکیتا - مدل GA9020", "ماکیتا") == "فرز آهنگری ماکیتا GA9020"


def test_topic_affinity_and_small_city():
    from datetime import datetime, timedelta, timezone
    from app.channels.metrics import TOPICS, compute
    rx = TOPICS["AGRI"]
    assert rx.search("قیمت کود اوره") and rx.search("سم‌پاشی باغات") and rx.search("برداشت گندم آغاز شد")
    assert not rx.search("کودک") and not rx.search("هوای آفتابی") and not rx.search("دامنه‌ی کوه") and not rx.search("دامغان")
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    posts = [{"views": 2000, "ts": now - timedelta(days=3 + i), "text": "خبر کشاورزی و آبیاری باغات"} for i in range(4)] + \
            [{"views": 1000, "ts": now - timedelta(days=3 + i), "text": "خبر حوادث شهر"} for i in range(8)]
    m = compute(posts, 10000, "x", now)
    assert m["topics"]["AGRI"] == {"n": 4, "lift": 2.0} and m["topics"]["BUILD"]["lift"] is None
    from types import SimpleNamespace
    from app.channels import hunter
    ch = SimpleNamespace(relevant=True, status="checked", provinces=["گیلان"], plan_ids=[], audience="local_news",
                         scope="city", metrics=m)
    assert hunter._fit(ch, "IRRIG", "WATER", "گیلان") == 0.9          # ۰٫۶ × ۱٫۵ (سقف)
    assert hunter._fit(ch, "PIPEX", "PIPE", "گیلان") == 0.6           # ساختمان نامشخص ← بدون تغییر
    hunter._CITY_RX = __import__("re").compile("میناب|رشت")
    assert hunter.min_members("اخبار میناب") == 400 and hunter.min_members("اخبار ورزشی") == 800
    hunter._CITY_RX = None


def test_outreach_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/out.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from app.channels import outreach as o
    from app.db.marketing import Campaign, Channel, Placement
    with sess.get_session() as db:
        db.add(Campaign(id=7, code="CX7", title="خاک‌ورزی — گلستان", plan_id="TILL", status="active"))
        a = Channel(platform="telegram", username="gonbadnews", title="خبرهای گنبد", admin_contact="@gonbadads", status="checked")
        b = Channel(platform="telegram", username="aqqalaa", title="آق قلا", ad_contacts=["@ghojoghi_71", "0911"], status="checked")
        c = Channel(platform="telegram", username="gorgan", title="گرگان", admin_contact="@ghojoghi_71", status="checked")
        d = Channel(platform="telegram", username="noadmin", title="بی‌ادمین", status="checked")
        db.add_all([a, b, c, d]); db.flush()
        for i, ch in enumerate((a, b, c, d)):
            db.add(Placement(code=f"PX{i}", campaign_id=7, channel_id=ch.id))
        db.commit()
    r = o.draft(7)
    assert r["made"] == 2 and r["no_contact"] == ["بی‌ادمین"]
    assert o.draft(7)["made"] == 0                       # تکراری نمی‌سازد
    items = o.listing(7)["items"]
    two = next(x for x in items if x["handle"] == "ghojoghi_71")
    assert len(two["channels"]) == 2 and "«آق قلا»، «گرگان»" in two["text"]
    assert o.tasks()["send"] == []                       # بدون تأیید هیچ پیامی نمی‌رود
    o.approve([x["id"] for x in items])
    t = o.tasks()
    assert len(t["send"]) == 2 and t["send"][0]["lines"][0] == "سلام، وقت بخیر"
    o.ingest_results([{"id": items[0]["id"], "ok": True, "peer": "726306308"},
                      {"id": items[1]["id"], "ok": False, "error": "Too many requests"}])
    t = o.tasks()
    assert t["paused"] and t["send"] == [] and len(t["check"]) == 1 and t["check"][0]["peer"] == "726306308"
    assert "🔖" not in o.message_text(["دزفوری | اخبار فوری دزفول 🔖"])
    o.ingest_replies([{"id": items[0]["id"], "text": "سلام، پست ۲۴ ساعته ۸۰۰ تومن"}], mock=True)
    with sess.get_session() as db:
        ch = db.query(Channel).filter(Channel.username == "gonbadnews").one()
        pl = db.query(Placement).filter(Placement.channel_id == ch.id).one()
        assert ch.price_toman == 800000 and pl.cost_toman == 800000 and pl.status == "contacted"
    assert o.resume()["ok"] and not o.tasks()["paused"]


def test_support_links(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/sl.db")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://content.example.com")
    from app.config import get_settings
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from app.db.marketing import Campaign, Placement
    from app.plan import campaigns as c
    with sess.get_session() as db:
        db.add(Campaign(id=3, code="CQ3", title="سم‌پاشی باغ — گیلان", plan_id="SPRAY", status="active"))
        db.add(Placement(id=5, code="AB12", campaign_id=3, ad_status="ready",
                         ad={"headline": "ت", "body": "ب", "cta": "س", "short_version": "ک", "hashtags": ["باغ"], "link": "L"}))
        db.commit()
    assert c.relink_all() == 1
    with sess.get_session() as db:
        ad = db.get(Placement, 5).ad
    assert "/m/AB12" in ad["full_text"] and "@Caravansanatsupport" in ad["full_text"] and "#باغ" in ad["full_text"]
    u = c.support_redirect_url("AB12")
    assert u.startswith("https://t.me/Caravansanatsupport?text=") and "AB12" in __import__("urllib.parse").parse.unquote(u)
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None


def test_projects_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/pj.db")
    from app.db import session as sess
    monkeypatch.setattr(sess, "_engine", None)
    monkeypatch.setattr(sess, "_Session", None)
    sess.init_db()
    from datetime import datetime, timedelta, timezone
    from app import projects as P
    assert P.parse_refs("لینک کانال\nhttps://t.me/Vadana_Zaban\nhttps://t.me/s/sharifdaily, @startup360 t.me/joinchat/x https://t.me/Vadana_Zaban") \
        == ["Vadana_Zaban", "sharifdaily", "startup360"]
    r = P.create("کارآفرینی", "مسابقه‌ی کارآفرینی برای دانشجویان و استارتاپ‌ها", "https://t.me/aaaa1\nhttps://t.me/bbbb2", mock=True)
    pid = r["id"]
    assert r["channels"] == 2
    P.update(pid, {"keywords": ["کارآفرین", "استارتاپ"], "intro": "از دبیرخانه‌ی مسابقه‌ی کارآفرینی هستیم."})
    now = datetime.now(timezone.utc)
    def fake(user, before=None):
        posts = [{"views": 3000 if i % 3 == 0 else 1000, "ts": now - timedelta(days=2 + i),
                  "text": "رویداد استارتاپ" if i % 3 == 0 else "خبر دانشگاه", "reactions": 0} for i in range(20)]
        return {"post_list": posts, "min_post_id": None, "title": "کانال " + user, "description": "تبلیغات: @ads_" + user,
                "members": 20000, "texts": ["x"]}
    import app.channels.telegram as T
    monkeypatch.setattr(T, "fetch_telegram", fake)
    st = P.run_once(60, mock=True)
    assert st["measured"] == 2 and st["tagged"] == 2
    d = P.get(pid)
    row = d["items"][0]
    assert d["stats"]["checked"] == 2 and row["topic_lift"] == 3.0 and row["est_price"] and row["price_is_estimate"]
    assert row["ad_contacts"] == ["@ads_" + row["username"]]
    P.set_price(row["id"], 2_000_000)
    b = P.budget_plan(pid, 10_000_000, min_fit=0.5)
    assert b["channels"] == 2 and b["spent"] <= 10_000_000
    assert P.export_csv(pid).startswith("﻿".encode())
    from app.channels import outreach as O
    r = O.draft_project(pid, [x["id"] for x in d["items"]])
    assert r["made"] == 2
    items = O.listing(project_id=pid)["items"]
    assert "دبیرخانه" in items[0]["text"] and O.listing()["items"] == []        # جدا از کمپین‌های کاروان
    O.approve([items[0]["id"]]); O.ingest_results([{"id": items[0]["id"], "ok": True}])
    O.ingest_replies([{"id": items[0]["id"], "text": "پست ۲۴ ساعته ۹۰۰"}], mock=True)
    assert any(x["price_toman"] == 900000 for x in P.get(pid)["items"])
