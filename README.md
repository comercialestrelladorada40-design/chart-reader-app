# قارئ الشارت

تطبيق ويب محلي: بترفعله صورة شارت تداول، وهو بيبعتها لـ Claude API (قراءة بصرية)
ويطلع لك بطاقة تحليل فني (اتجاه، زخم، دعم/مقاومة، توصيات قصيرة/متوسطة المدى، مخاطر).

## التشغيل (أسهل طريقة)

دوس دبل-كليك على **run.bat**.

- أول مرة: رح يعمل environment ويثبّت المكتبات، وبعدين يفتح ملف `.env` بالـ Notepad
  عشان تلصق مفتاح Anthropic API مكان `paste-your-key-here`. احفظ وسكّر Notepad.
- شغّل **run.bat** مرة ثانية — هلق رح يفتح المتصفح تلقائياً على http://localhost:5000
  ويشتغل السيرفر.
- كل مرة بعدها، بس دوس run.bat وبيشتغل مباشرة (المكتبات مثبتة أصلاً).

لازم يكون عندك Python 3.9+ مثبت على الجهاز (من python.org) — إذا run.bat اشتكى إنه
"python" مش موجود، ثبّت Python وتأكد تعلّم خيار "Add python.exe to PATH" أثناء التثبيت.

## التشغيل يدوياً (بديل)

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# افتح .env وحط مفتاحك
python app.py
```

## ملاحظات

- المفتاح بيضل على جهازك بس (ملف `.env` محلي، ما بينبعت لأي مكان غير Anthropic API مباشرة).
- الموديل الافتراضي `claude-sonnet-4-5-20250929` — شيك على
  https://docs.claude.com/en/docs/about-claude/models إذا بدك موديل أحدث وحدّث
  `CHART_READER_MODEL` بملف `.env`.
- هاد تحليل بصري تقديري من صورة واحدة، مش بيانات سعر حية أو مؤشرات محسوبة فعلياً —
  مش توصية استثمارية.
