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
