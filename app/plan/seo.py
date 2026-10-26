"""
پیشنهاد محتوای سئو از داده‌ی سرچ کنسول.

سه نوع فرصت:
- «فرصت سریع»: عبارتی که رتبه‌ی ۴ تا ۲۰ دارد و زیاد دیده می‌شود ولی کم کلیک می‌خورد ← بهبود همان صفحه یا مقاله‌ی کامل‌تر
- «کلمه‌ی کالا با رتبه‌ی دور»: نام کالا (مثل «شیر سوزنی») که رتبه‌ی ۲۰ به بعد دارد ← راهنمای خرید یا متن صفحه‌ی دسته
- «عبارت خرید»: «قیمت/خرید …» ← صفحه‌ی کالا با جدول قیمت و مشخصات

برای هر پیشنهاد، کالاهای مرتبط سایت پیدا می‌شود تا مقاله به آن‌ها لینک بدهد (مقاله‌ی آموزشی پربازدید بدون لینک کالا،
بازدید می‌آورد ولی فروش نه؛ مشکل فعلی سایت همین است).
"""
from app.tracking.external import get

KIND = {"quick": "فرصت سریع (رتبه‌ی نزدیک)", "head": "کلمه‌ی کالا با رتبه‌ی دور", "buy": "عبارت خرید"}


def ideas(limit: int = 30) -> list[dict]:
    from app.assistant import search_products
    g = get("gsc_queries") or {}
    if not g:
        return []
    pool = ([("head", q) for q in g.get("far_head_terms") or []] + [("buy", q) for q in (g.get("commercial") or {}).get("top") or []]
            + [("quick", q) for q in g.get("quick_wins") or []])
    out, seen = [], set()
    for kind, q in pool:
        key = q["q"].strip()
        if key in seen:
            continue
        seen.add(key)
        prods = search_products(key.replace("قیمت", "").replace("خرید", ""), limit=3)
        if kind == "quick":
            title = f"راهنمای کامل: {key}"
            brief = (f"مقاله‌ی سئو برای عبارت «{key}» (الان رتبه‌ی {q['p']} با {q['i']} نمایش در ۳ ماه). "
                     "به سؤال کاربر کامل جواب بده، در پایان «کالاهای مرتبط» با لینک بگذار.")
        elif kind == "head":
            title = f"راهنمای خرید {key}: انواع، کاربرد و قیمت"
            brief = (f"راهنمای خرید برای «{key}» ({q['i']} نمایش، رتبه‌ی {q['p']}): انواع، جدول مقایسه‌ی سایز و جنس، "
                     "نکات انتخاب، و لینک به کالاهای سایت.")
        else:
            title = f"{key} — لیست قیمت روز و مشخصات"
            brief = f"متن صفحه‌ی کالا/دسته برای «{key}» با جدول قیمت روز، مشخصات فنی و دکمه‌ی ثبت سفارش."
        if prods:
            brief += " کالاهای سایت برای لینک: " + "، ".join(p.name[:60] for p in prods)
        out.append({"kind": kind, "kind_label": KIND[kind], "query": key, "impr": q["i"], "clicks": q["c"], "pos": q["p"],
                    "title": title, "brief": brief[:300],
                    "products": [{"name": p.name, "url": p.permalink, "price": p.price} for p in prods]})
        if len(out) >= limit:
            break
    return out
