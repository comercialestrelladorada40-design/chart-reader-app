"""
قارئ الشارت — تطبيق ويب محلي يحلل صورة (أو صورتين) شارت تداول باستخدام Claude API
(رؤية بصرية) ويطلع تقرير فني منظّم بنفس أسلوب أدوات إشارات التداول.

يدعم وضعين:
- صورة وحدة: تحليل فريم واحد فقط.
- صورتين (فريم 15 دقيقة + فريم 5 دقايق): تحليل متعدد الفريمات — الفريم الكبير
  (15 دقيقة) بيحدد الاتجاه العام (البوصلة)، والفريم الصغير (5 دقايق) بيحدد نقطة
  الدخول الدقيقة (الزناد)، وبيطلع تقرير واحد مبني على تطابق الفريمين.

التشغيل:
    pip install -r requirements.txt
    cp .env.example .env   # وحط مفتاح ANTHROPIC_API_KEY الخاص فيك جوا .env
    python app.py
    # افتح http://localhost:5000
"""
import base64
import json
import os
import re

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

app = Flask(__name__, static_folder="static", static_url_path="")

MODEL = os.environ.get("CHART_READER_MODEL", "claude-sonnet-4-5-20250929")
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_BYTES = 5 * 1024 * 1024  # حد أقصى تقريبي لحجم كل صورة يقبله الـ API

# بنية الـ JSON المطلوبة مشتركة بين وضع الصورة الوحدة ووضع الفريمين
JSON_SCHEMA_BLOCK = """رجّع **JSON فقط** بدون أي نص قبله أو بعده وبدون code fences، بالضبط بهاد البنية:

{
  "symbol": "اسم الزوج/الأداة متل ما هو ظاهر بالشارت",
  "symbolShort": "حرف أو حرفين للأيقونة",
  "timeframe": "الفريم الزمني الظاهر متل 5 دقائق (أو 15 دقيقة + 5 دقايق لو صورتين)",
  "signal": "buy" | "sell" | "neutral",
  "signalLabel": "شراء" | "بيع" | "محايد",
  "trendStrength": 0-100,
  "momentum": 0-100,
  "volatility": "منخفض" | "متوسط" | "مرتفع",
  "priceAction": "فقرة تشرح حركة السعر خلال الفترة الظاهرة بالشارت (أو بالشارتين)",
  "alignmentNote": "جملة أو جملتين تشرح هل الفريمين متوافقين على نفس الاتجاه ولّا لأ ولّيش — اتركها نص فاضي \\"\\" إذا كانت صورة وحدة بس",
  "support": [أرقام دعم مرتبة],
  "resistance": [أرقام مقاومة مرتبة],
  "summary": "فقرة ملخص شاملة",
  "strengths": ["نقطة قوة 1", "..."],
  "risks": ["نقطة خطر 1", "..."],
  "riskManagement": ["نصيحة إدارة مخاطر 1", "..."],
  "notes": ["ملاحظة إضافية 1", "..."],
  "recommendations": [
    {
      "term": "قصيرة المدى",
      "condition": "شرط الدخول بالتفصيل",
      "entry": رقم,
      "stop": رقم,
      "target": رقم,
      "rationale": "سبب هاد الإعداد"
    },
    {
      "term": "متوسطة المدى",
      "condition": "...",
      "entry": رقم, "stop": رقم, "target": رقم,
      "rationale": "..."
    }
  ]
}
"""

COMMON_RULES = """قواعد مهمة:
- كل القيم الرقمية (نقاط الدخول، الوقف، الهدف، الدعم/المقاومة) لازم تكون مبنية على
  أرقام أو مستويات ظاهرة فعلياً بالصورة (خطوط، تسميات، محور السعر) — لا تخترع أرقام
  من فراغ. إذا مستوى مش واضح، قرّبه من أقرب حركة سعر مرئية واذكر إنه تقديري.
- "قوة الاتجاه" و"الزخم" (0-100) هي تقييمات بصرية تقديرية بناءً على شكل الشموع
  وحدّة الحركة، مش أرقام محسوبة من مؤشر حقيقي.
- التوصيات (قصيرة/متوسطة المدى) دايماً أوامر معلّقة (pending) بشرط دخول واضح،
  لأنك بتحلل لقطة واحدة ثابتة وما عندك سعر حي.
- اكتب كل النصوص بالعربي (بالهجة أو الفصحى الواضحة زي تقارير التداول)، والأرقام
  بالإنجليزي (لاتينية).
- إذا الصورة (أو الصور) مش شارت تداول أصلاً (أو مش واضحة كفاية للتحليل)، رجّع فقط:
  {"error": "وصف قصير للمشكلة بالعربي"}
"""

SYSTEM_PROMPT_SINGLE = f"""أنت محلل فني متخصص بقراءة شارتات التداول (كريبتو/فوركس/أسهم) من الصور فقط.
بتوصلك صورة سكرين-شوت لشارت من منصة تداول. مهمتك تقرأ الصورة بصرياً — الشموع، أي
مؤشرات مرسومة عالشارت (متل SuperTrend، خطوط اتجاه، مستويات دعم/مقاومة مكتوبة)، الأرقام
الظاهرة على محور السعر — وتبني تحليل فني منظم متل يلي بتعمله أدوات إشارات التداول
الاحترافية.

{COMMON_RULES}
{JSON_SCHEMA_BLOCK}"""

SYSTEM_PROMPT_MTFA = f"""أنت محلل فني متخصص بقراءة شارتات التداول من الصور، وبتستخدم إطار تحليل متعدد
الفريمات الزمنية (فريم كبير + فريم صغير) — نفس منهجية أدوات إشارات التداول الاحترافية.

بتوصلك صورتين سكرين-شوت لنفس الأداة (نفس الرمز):
- الصورة الأولى: الفريم الأكبر (15 دقيقة) — هاد "البوصلة" يلي بتحدد الاتجاه العام.
- الصورة الثانية: الفريم الأصغر (5 دقايق) — هاد "الزناد" يلي بيحدد لحظة ونقطة الدخول
  الدقيقة.

طريقة التحليل المشترك:
1. حدد اتجاه الفريم الكبير (15 دقيقة) أولاً من شكل الشموع، أي مؤشرات مرسومة (متل
   SuperTrend)، وخطوط الاتجاه. هاد الاتجاه هو المرجع الأساسي.
2. بعدين افحص الفريم الصغير (5 دقايق) وشوف هل حركته الحالية متوافقة مع اتجاه الفريم
   الكبير ولّا لأ.
3. **لا توصي بصفقة فعلية إلا إذا كان الفريمين متوافقين على نفس الاتجاه.** إذا كانوا
   متعاكسين أو الوضع غير واضح، خلّي "signal" = "neutral" واشرح سبب التعارض بوضوح
   بفقرة "priceAction" و"summary"، واقترح الانتظار لحد ما يصير توافق بين الفريمين.
4. مستويات التوصيات (الدخول/الوقف/الهدف) لازم تُبنى على مستويات ظاهرة بالفريم
   الصغير (لأنه الأدق لتحديد نقطة الدخول)، لكن بس بشرط توافقها مع اتجاه الفريم
   الكبير.
5. حقل "alignmentNote" خصصه لشرح واضح: هل الفريمين متوافقين ولّا متعارضين، وليش،
   بجملة أو جملتين.
6. حقل "timeframe" خلّيه "15 دقيقة + 5 دقايق".

{COMMON_RULES}
{JSON_SCHEMA_BLOCK}"""

LABEL_TEXT = {
    "15": "هاي صورة الفريم الأكبر (15 دقيقة) — البوصلة يلي بتحدد الاتجاه العام:",
    "5": "هاي صورة الفريم الأصغر (5 دقايق) — الزناد يلي بيحدد نقطة الدخول:",
}


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    return json.loads(text)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "مفتاح ANTHROPIC_API_KEY مش مضبوط. حط قيمته بملف .env وأعد تشغيل التطبيق."}), 500

    file_15m = request.files.get("image_15m")
    file_5m = request.files.get("image_5m")
    # توافق مع الواجهة القديمة يلي كانت بترفع حقل اسمه "image" بس
    if not file_15m and not file_5m:
        legacy = request.files.get("image")
        if legacy:
            file_5m = legacy

    uploaded = [("15", file_15m), ("5", file_5m)]
    images = []
    for label, f in uploaded:
        if not f or not f.filename:
            continue
        img_bytes = f.read()
        if not img_bytes:
            return jsonify({"error": "أحد الملفات المرفوعة فاضي."}), 400
        if len(img_bytes) > MAX_BYTES:
            return jsonify({"error": "حجم إحدى الصور كبير — حاول تصغّرها لأقل من 5 ميغابايت."}), 400
        media_type = f.mimetype or "image/png"
        if media_type not in ALLOWED_MIME:
            return jsonify({"error": "صيغة إحدى الصور غير مدعومة. استخدم PNG أو JPEG أو WEBP."}), 400
        images.append((label, img_bytes, media_type))

    if not images:
        return jsonify({"error": "لم يتم إرفاق أي صورة."}), 400

    is_mtfa = len(images) == 2
    # رتب الصور دايماً: الفريم الكبير (15) قبل الفريم الصغير (5)
    images.sort(key=lambda item: 0 if item[0] == "15" else 1)

    content = []
    if is_mtfa:
        for label, img_bytes, media_type in images:
            content.append({"type": "text", "text": LABEL_TEXT[label]})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(img_bytes).decode("utf-8"),
                    },
                }
            )
        content.append(
            {
                "type": "text",
                "text": "حلل الشارتين مع بعض حسب إطار تحليل متعدد الفريمات، واطلع لي JSON فقط حسب البنية المطلوبة بالضبط.",
            }
        )
        system_prompt = SYSTEM_PROMPT_MTFA
    else:
        _, img_bytes, media_type = images[0]
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(img_bytes).decode("utf-8"),
                },
            }
        )
        content.append(
            {"type": "text", "text": "حلل هاد الشارت واطلع لي JSON فقط حسب البنية المطلوبة بالضبط."}
        )
        system_prompt = SYSTEM_PROMPT_SINGLE

    # الاستيراد هون بيأخر ظهور خطأ "المفتاح مفقود" إذا الحزمة مش مثبتة أصلاً
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2200,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        raw_text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        data = extract_json(raw_text)
    except json.JSONDecodeError:
        return jsonify({"error": "الرد ما كان JSON صالح — جرب صورة أوضح أو حاول مرة ثانية."}), 502
    except Exception as exc:  # noqa: BLE001 — نرجع رسالة مفهومة للواجهة
        return jsonify({"error": f"صار خطأ بالاتصال مع Claude API: {exc}"}), 502

    if "error" in data and len(data) == 1:
        return jsonify(data), 422

    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
