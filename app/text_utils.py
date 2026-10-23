"""
نرمال‌سازی سبک متن فارسی خروجی LLM.

فقط قواعد کم‌ریسک: حروف عربی ← فارسی، نیم‌فاصله‌ی «می/نمی» و پسوندهای «ها/تر/ترین»،
و فاصله‌های اضافه. به اعداد، ایموجی و علامت‌های Markdown دست نمی‌زند.
"""
import re

ZWNJ = "‌"
_ARABIC = str.maketrans({"ي": "ی", "ك": "ک", "ى": "ی", "ة": "ه"})
_FA = "آ-ی؀-ۿ"

_RULES = [
    # «می کنم» / «نمی شود» ← «می‌کنم» / «نمی‌شود»
    (re.compile(rf"(?<![{_FA}])(ن?می) (?=[{_FA}])"), rf"\1{ZWNJ}"),
    # «کاراوان ها» / «بزرگ تر» ← «کاراوان‌ها» / «بزرگ‌تر»
    (re.compile(rf"(?<=[{_FA}]) (ها|های|هایی|هایم|هایت|هایش|تر|ترین)(?![{_FA}])"), rf"{ZWNJ}\1"),
    # نیم‌فاصله‌ی تکراری یا کنار فاصله
    (re.compile(rf"{ZWNJ}+"), ZWNJ),
    (re.compile(rf" ?{ZWNJ} ?"), ZWNJ),
    # فاصله‌ی تکراری (نه خط جدید)
    (re.compile(r"[ \t]{2,}"), " "),
]

# اصلاح چند واژه‌ی پرتکرار که مدل‌ها بی‌نیم‌فاصله می‌نویسند
_WORDS = {
    "آتشنشانی": "آتش‌نشانی",
    "کمکهای": "کمک‌های",
    "جعبهابزار": "جعبه‌ابزار",
}


def normalize_fa(text: str) -> str:
    if not text:
        return text
    t = text.translate(_ARABIC)
    for pat, rep in _RULES:
        t = pat.sub(rep, t)
    for wrong, right in _WORDS.items():
        t = t.replace(wrong, right)
    return t.strip()


def normalize_obj(obj):
    """همه‌ی رشته‌های داخل dict/list را نرمال می‌کند (به جز visual.query که انگلیسی است)."""
    if isinstance(obj, str):
        return normalize_fa(obj)
    if isinstance(obj, list):
        return [normalize_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k in ("query", "icon", "template", "aspect", "type") else normalize_obj(v))
                for k, v in obj.items()}
    return obj
