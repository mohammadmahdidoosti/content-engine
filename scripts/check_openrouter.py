"""
بررسی دسترسی به OpenRouter، اعتبار کلید و اسم دقیق مدل‌ها.

  python -m scripts.check_openrouter                 # کلید + مدل تنظیم‌شده
  python -m scripts.check_openrouter --search deepseek
"""
import argparse
import sys

import httpx

from app.config import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", help="جست‌وجو در اسم مدل‌ها، مثلاً deepseek یا claude")
    args = ap.parse_args()
    s = get_settings()
    base = s.openrouter_base_url.rstrip("/")

    # ۱. دسترسی شبکه + لیست مدل‌ها (بدون کلید هم کار می‌کند)
    try:
        r = httpx.get(f"{base}/models", timeout=20)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"❌ دسترسی به OpenRouter ممکن نیست: {e}")
        return 1
    models = r.json().get("data", [])
    print(f"✅ OpenRouter در دسترس است ({len(models)} مدل)")

    # ۲. کلید
    if not s.openrouter_api_key or s.openrouter_api_key.startswith("sk-or-v1-xxxx"):
        print("⚠️  OPENROUTER_API_KEY در .env تنظیم نشده.")
    else:
        k = httpx.get(f"{base}/key", headers={"Authorization": f"Bearer {s.openrouter_api_key}"}, timeout=20)
        if k.status_code == 200:
            d = k.json().get("data", {})
            print(f"✅ کلید معتبر است | مصرف: {d.get('usage')} | سقف: {d.get('limit')}")
        else:
            print(f"❌ کلید نامعتبر است (HTTP {k.status_code}): {k.text[:200]}")

    # ۳. مدل تنظیم‌شده
    ids = {m["id"] for m in models}
    if s.llm_model in ids or s.llm_model.startswith("~"):
        note = "" if s.llm_model in ids else " (alias؛ موقع اجرا بررسی می‌شود)"
        print(f"ℹ️  LLM_MODEL = {s.llm_model}{note}")
    else:
        print(f"❌ مدل «{s.llm_model}» در OpenRouter پیدا نشد. با --search اسم درست را پیدا کنید.")

    # ۴. جست‌وجو
    if args.search:
        q = args.search.lower()
        found = [m for m in models if q in m["id"].lower() or q in m.get("name", "").lower()]
        print(f"\nمدل‌های شامل «{args.search}»:")
        for m in sorted(found, key=lambda m: m["id"]):
            p = m.get("pricing", {})
            try:
                pin = float(p.get("prompt", 0)) * 1e6
                pout = float(p.get("completion", 0)) * 1e6
                price = f"${pin:.2f} / ${pout:.2f} per 1M tok"
            except (TypeError, ValueError):
                price = "?"
            json_ok = "json" if "response_format" in (m.get("supported_parameters") or []) else "—"
            print(f"  {m['id']:<55} {price:<28} {json_ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
