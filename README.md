# content-engine — مدیر بازاریابی کاروان صنعت

ابزار بازاریابی اجتماعی برای caravansanat.com:

- **نقشه‌ی پویا:** نقشه‌ی تبلیغات (۴۸ دسته × منطقه × ماه) + همگام‌سازی روزانه با محصولات سایت
  (Store API عمومی ووکامرس). دسته‌های جدید سایت خودکار به نقشه وصل می‌شوند و برای گروه‌های جدید ردیف پیشنهادی ساخته می‌شود.
- **برنامه‌ریز کمپین:** بر اساس تاریخ شمسی، اوج فصل، اولویت گروه، قیمت و موجودی، و بودجه‌ی ماهانه.
- **کانال‌ها:** ثبت گروهی و ارزیابی خودکار کانال‌های تلگرام (عضو، بازدید، فیک بودن، شلوغی تبلیغ، هزینه هر ۱۰۰۰ بازدید).
- **تبلیغ و ردیابی:** متن تبلیغ مخصوص هر کانال با کالاهای واقعی، لینک کوتاه + UTM، اتصال سفارش‌های ووکامرس، گزارش بازگشت هزینه.
- **استودیو محتوا:** متنی، اینفوگرافیک و سناریوی ویدئو از یک موضوع.

## اجرا و زمان‌بندی
روی VPS با Coolify: `https://content.caravansanat.com`، دستور اجرا `python -m scripts.server` (پورت 10000)، دیتابیس Postgres 16 جدا.
متغیرهای اصلی: `DATABASE_URL`، `RUN_TOKEN`، `PUBLIC_BASE_URL`، `OPENROUTER_API_KEY`، کلیدهای ووکامرس، و در صورت نیاز توکن ربات‌ها.
زمان‌بند داخلی (وقتی `NO_SCHEDULER` خاموش است): هر ۱۰ دقیقه همگام‌سازی روزانه‌ی سایت اگر وقتش باشد، و هر ۱۵ دقیقه شکارچی کانال.
همگام‌سازی تکه‌تکه است (حداکثر ۱۲ دقیقه در هر اجرا) و جای خود را در جدول settings نگه می‌دارد.
`.github/workflows/scheduler.yml` فقط برای اجرای دستی `/api/cron` نگه داشته شده است.
بعد از هر push روی main، در Coolify دکمه‌ی Redeploy زده می‌شود.

## پنل وب

آدرس سرویس را باز کنید و با مقدار `RUN_TOKEN` (متغیرهای محیطی در Coolify) وارد شوید.
در پنل موضوع می‌دهید، نوع محتوا را انتخاب می‌کنید و پیش‌نمایش، تاریخچه، زمان و هزینه‌ی هر محتوا را می‌بینید.

اجرای محلی پنل بدون API:

```bash
DATABASE_URL=sqlite:///./content_engine.db RUN_TOKEN=test LLM_MOCK=1 python -m scripts.server
# http://localhost:10000  (رمز: test)
```

## ساختار

```
app/
  config.py            تنظیمات از .env
  db/models.py         جدول jobs و وضعیت‌ها
  db/session.py        اتصال دیتابیس
  brain/schemas.py     اسکیمای text / infographic / video
  brain/prompts.py     پرامپت برند و هر نوع محتوا
  brain/llm.py         کلاینت OpenRouter + اعتبارسنجی + retry + Mock
  brain/generate.py    ساخت job و اجرای تولید
scripts/
  init_db.py           ساخت جدول‌ها
  check_openrouter.py  تست کلید و پیدا کردن اسم مدل
  generate.py          تولید محتوا از خط فرمان
  server.py            پنل وب + API
app/web/index.html     پنل
tests/                 تست‌ها (بدون اینترنت)
```

## راه‌اندازی با Docker

```bash
cp .env.example .env          # کلید OpenRouter را داخلش بگذارید
docker compose up -d db
docker compose run --rm app python -m scripts.init_db
docker compose run --rm app python -m scripts.check_openrouter --search deepseek
docker compose run --rm app python -m scripts.generate --type all
```

خروجی‌ها در پوشه‌ی `outputs/` ذخیره می‌شوند.

## اجرای بدون Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# در .env:  DATABASE_URL=sqlite:///./content_engine.db
python -m pytest                         # تست‌ها
python -m scripts.generate --mock        # تست کامل بدون API
python -m scripts.check_openrouter --search deepseek
python -m scripts.generate --type all
```

## دستورهای مفید

| کار | دستور |
|---|---|
| فقط یک نوع | `--type video` |
| موضوع دیگر | `--topic "۵ اشتباه رایج در اولین سفر با کاراوان"` |
| مدل دیگر | `--model <id>` |
| مقایسه‌ی چند مدل | `--models "<id1>,<id2>"` |
| بدون API | `--mock` |

## نکته‌ها

- اسم مدل‌ها در OpenRouter عوض می‌شود. همیشه با `check_openrouter --search` اسم دقیق را پیدا کنید.
  ستون `json` نشان می‌دهد مدل `response_format` را پشتیبانی می‌کند یا نه.
- کد فقط providerهایی را انتخاب می‌کند که خروجی JSON را پشتیبانی می‌کنند (`require_parameters`).
- اگر خروجی مدل نامعتبر باشد، خطاها به مدل برگردانده می‌شود و تا `LLM_MAX_RETRIES` بار تکرار می‌شود.
- هزینه و تعداد توکن هر job در دیتابیس و فایل خروجی ثبت می‌شود.
- اگر از IP ایران اجرا کنید ممکن است مدل‌ها (Claude، OpenAI و...) در دسترس نباشند؛ روی سرور خارج از ایران اجرا کنید.
