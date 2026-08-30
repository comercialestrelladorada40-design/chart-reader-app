"""
قارئ الشارت — تطبيق ويب محلي يحلل صورة شارت تداول باستخدام Claude API (رؤية بصرية)
ويطلع تقرير فني منظّم بنفس أسلوب أدوات إشارات التداول.

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
MAX_BYTES = 5 * 1024 * 1024  # حد أقصى تقريبي لحجم الصورة يقبله الـ API

SYSTEM_PROMPT = """أنت محلل فني متخصص بقراءة شارتات التداول (كريبتو/فوركس/أسهم) من الصور فقط.
بتوصلك صورة سكرين-شوت لشارت من منصة تداول. مهمتك تقرأ الصورة بصرياً — الشموع، أي
مؤشرات مرسومة عالشارت (متل SuperTrend، خطوط اتجاه، مستويات دعم/مقاومة مكتوبة)، الأرقام
الظاهرة على محور السعر — وتبني تحليل فني منظم متل يلي بتعمله أدوات إشارات التداول
الاحترافية.

قواعد مهمة:
- كل القيم الرقمية (نقاط الدخول، الوقف، الهدف، الدعم/المقاومة) لازم تكون مبنية على
  أرقام أو مستويات ظاهرة فعلياً بالصورة (خطوط، تسميات، محور السعر) — لا تخترع أرقام
  من فراغ. إذا مستوى مش واضح، قرّبه من أقرب حركة سعر مرئية واذكر إنه تقديري.
- "قوة الاتجاه" و"الزخم" (0-100) هي تقييمات بصرية تقديرية بناءً على شكل الشموع
  وحدّة الحركة، مش أرقام محسوبة من مؤشر حقيقي.
- التوصيات (قصيرة/متوسطة المدى) دايماً أوامر معلّقة (pending) بشرط دخول واضح،
  لأنك بتحلل لقطة واحدة ثابتة وما عندك سعر حي.
- اكتب كل النصوص بالعربي (بالهجة أو الفصحى الواضحة زي تقارير التداول)، والأرقام
  بالإنجليزي (لاتينية).
- إذا الصورة مش شارت تداول أصلاً (أو مش واضحة كفاية للتحليل)، رجّع فقط:
  {"error": "وصف قصير للمشكلة بالعربي"}

رجّع **JSON فقط** بدون أي نص قبله أو بعده وبدون code fences، بالضبط بهاد البنية:

{
  "symbol": "اسم الزوج/الأداة متل ما هو ظاهر بالشارت",
  "symbolShort": "حرف أو حرفين للأيقونة",
  "timeframe": "الفريم الزمني الظاهر متل 5 دقائق",
  "signal": "buy" | "sell" | "neutral",
  "signalLabel": "شراء" | "بيع" | "محايد",
  "trendStrength": 0-100,
  "momentum": 0-100,
  "volatility": "منخفض" | "متوسط" | "مرتفع",
  "priceAction": "فقرة تشرح حركة السعر خلال الفترة الظاهرة بالشارت",
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

    if "image" not in request.files:
        return jsonify({"error": "لم يتم إرفاق صورة."}), 400

    file = request.files["image"]
    img_bytes = file.read()

    if not img_bytes:
        return jsonify({"error": "الملف فاضي."}), 400
    if len(img_bytes) > MAX_BYTES:
        return jsonify({"error": "حجم الصورة كبير — حاول تصغّرها لأقل من 5 ميغابايت."}), 400

    media_type = file.mimetype or "image/png"
    if media_type not in ALLOWED_MIME:
        return jsonify({"error": "صيغة الصورة غير مدعومة. استخدم PNG أو JPEG أو WEBP."}), 400

    # الاستيراد هون بيأخر ظهور خطأ "المفتاح مفقود" إذا الحزمة مش مثبتة أصلاً
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    b64 = base64.b64encode(img_bytes).decode("utf-8")

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2200,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64},
                        },
                        {
                            "type": "text",
                            "text": "حلل هاد الشارت واطلع لي JSON فقط حسب البنية المطلوبة بالضبط.",
                        },
                    ],
                }
            ],
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
