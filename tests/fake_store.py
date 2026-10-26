"""یک Store API جعلی برای تست محلی همگام‌سازی (بدون اتصال به سایت واقعی)."""
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CATS = [
    {"id": 1, "name": "ابزار و ماشین‌آلات کشاورزی و باغبانی", "slug": "agri", "parent": 0, "count": 0},
    {"id": 2, "name": "بذر", "slug": "seed", "parent": 1, "count": 0},
    {"id": 3, "name": "بذر خربزه", "slug": "seed-melon", "parent": 2, "count": 3},
    {"id": 4, "name": "پمپ شناور", "slug": "sub-pump", "parent": 0, "count": 4},
    {"id": 5, "name": "کارواش خانگی", "slug": "carwash", "parent": 0, "count": 2},
    {"id": 6, "name": "اسباب‌بازی", "slug": "toys", "parent": 0, "count": 1},
]
for c in CATS:
    c["permalink"] = f"https://example.test/product-category/{c['slug']}/"

PRODUCTS = []
spec = [(3, "بذر خربزه جباری قوطی ۵۰۰ گرم", 1_200_000), (3, "بذر خربزه قصری ۵۰۰ گرم", 1_100_000),
        (3, "بذر خربزه آناناس راین", 3_400_000), (4, "پمپ شناور ۴ اینچ ۳ اسب", 28_000_000),
        (4, "پمپ شناور ۶ اینچ ۱۵ اسب", 120_000_000), (4, "پمپ شناور ۴ اینچ ۵.۵ اسب", 41_000_000),
        (4, "تابلو پمپ شناور", 9_000_000), (5, "کارواش دینامی ویوارکس", 9_726_000), (5, "کارواش ۱۲۰ بار", 6_500_000),
        (6, "عروسک", 300_000)]
for i, (cid, name, price) in enumerate(spec, start=100):
    PRODUCTS.append({
        "id": i, "name": name, "permalink": f"https://example.test/product/p{i}/", "sku": f"SKU{i}",
        "prices": {"price": str(price), "regular_price": str(price), "currency_minor_unit": 0},
        "on_sale": False, "is_in_stock": i % 7 != 0,
        "images": [{"src": f"https://example.test/i{i}.webp", "thumbnail": f"https://example.test/i{i}-600.webp"}],
        "categories": [{"id": cid, "name": "x"}], "attributes": [{"name": "برند", "terms": [{"name": "برند" + str(i)}]}],
        "brands": [], "short_description": "<p>توضیح <b>کوتاه</b></p>",
    })


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        per, page = int(q.get("per_page", 10)), int(q.get("page", 1))
        if u.path.endswith("/products/categories"):
            data = CATS
        elif u.path.endswith("/products"):
            data = sorted(PRODUCTS, key=lambda p: p["id"], reverse=(q.get("order") == "desc"))
        else:
            self.send_response(404)
            self.end_headers()
            return
        chunk = data[(page - 1) * per: page * per]
        body = json.dumps(chunk, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-WP-Total", str(len(data)))
        self.send_header("X-WP-TotalPages", str(math.ceil(len(data) / per)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    import sys
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
