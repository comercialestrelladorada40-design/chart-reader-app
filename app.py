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

# ===== وضع التحليل المباشر (بيانات حية من Capital.com، بدون صور) =====

SYSTEM_PROMPT_LIVE = f"""أنت محلل فني متخصص بتحليل الذهب (XAU/USD) باستخدام مؤشرات فنية محسوبة فعلياً
من بيانات شموع حية (مو من صورة) — مجلوبة مباشرة من منصة التداول.

بيوصلك وصف نصي فيه كل الأرقام والمؤشرات المحسوبة مسبقاً بالكود (SuperTrend, RSI, ATR,
MACD, Bollinger Bands, التقلب التاريخي HV) لفريمين: الفريم الكبير (15 دقيقة — البوصلة
يلي بتحدد الاتجاه العام) والفريم الصغير (5 دقايق — الزناد يلي بيحدد لحظة الدخول)، بالإضافة
لنتيجة التوافق بين الفريمين وتوصيات محسوبة جاهزة (دخول/وقف/هدف).

مهمتك الوحيدة: تكتب تحليل نصي احترافي بالعربي يشرح ويلخّص ويبرر هاي الأرقام — **بدون ما
تغيّر ولا تخترع ولا تعدّل أي رقم من يلي انعطى لك**. كل الحقول الرقمية والبنيوية (الإشارة،
قوة الاتجاه، الزخم، الدعم/المقاومة، نقاط الدخول/الوقف/الهدف) رح تنستبدل تلقائياً بالأرقام
المحسوبة الحقيقية بعد ردك بغض النظر عمّا تكتبه — فركّز فقط على جودة الشرح والتحليل النصي.

قواعد:
- "alignmentNote" لازم يشرح بوضوح هل الفريمين (15 و5 دقايق) متوافقين على نفس الاتجاه
  ولّا لأ، وليش، بالاستناد لأصوات المؤشرات المعطاة لك بكل فريم (SuperTrend/RSI/MACD/بولينجر).
- "priceAction" فقرة تشرح وضع كل فريم: الاتجاه، الزخم، موقع السعر من بولينجر، مستوى
  التقلب (ATR وHV).
- "summary" ملخص شامل للحالة والقرار المقترح (دخول الآن / انتظار توافق الفريمين / محايد).
- "strengths" و"risks" و"riskManagement" و"notes" استنتجهم من الأرقام المعطاة (مثلاً:
  RSI قريب من تشبع شرائي/بيعي، HV مرتفع يعني حركة عنيفة محتملة، السعر عند حافة بولينجر،
  إلخ). سيب أي مصفوفة فاضية إذا ما في نقاط فعلية.
- "recommendations[].condition" و"recommendations[].rationale" نص فقط — الأرقام
  (entry/stop/target) رح تُستبدل تلقائياً، بس اكتب condition وrationale منطقيين ومتوافقين
  مع الأرقام المعطاة لك واتجاه التوصية.
- "symbol" خليه "XAU/USD (الذهب)"، "symbolShort" خليه "Au"، "timeframe" خليه
  "15 دقيقة + 5 دقايق (مباشر)".
- اكتب كل النصوص بالعربي (باللهجة أو الفصحى الواضحة زي تقارير التداول)، والأرقام
  بالإنجليزي (لاتينية).

{JSON_SCHEMA_BLOCK}"""


def build_live_prompt_text(computed: dict) -> str:
    f15 = computed["frame15"]
    f5 = computed["frame5"]

    def frame_block(label, f):
        return (
            f"### فريم {label} دقيقة\n"
            f"- السعر الحالي: {f['currentPrice']}\n"
            f"- اتجاه SuperTrend: {f['trend']}\n"
            f"- RSI(14): {f['rsi']}\n"
            f"- ATR(14): {f['atr']} (تصنيف التقلب: {f['volatility']})\n"
            f"- MACD histogram: {f['macdHistogram']}\n"
            f"- بولينجر (20، 2 انحراف معياري): الوسط {f['bollingerMid']} — العلوي "
            f"{f['bollingerUpper']} — السفلي {f['bollingerLower']}\n"
            f"- التقلب التاريخي السنوي HV: {f['hv']}%\n"
            f"- الدعم القريب: {f['support']} / المقاومة القريبة: {f['resistance']}\n"
            f"- تصويت المؤشرات: {f['buyVotes']} مع الشراء، {f['sellVotes']} مع البيع "
            f"(من أصل {f['totalIndicators']}) → إشارة الفريم: {f['signalLabel']}\n"
            f"- عدد الشموع المستخدمة: {f['candleCount']}\n"
        )

    align_txt = "متوافقين على نفس الاتجاه" if computed["aligned"] else "غير متوافقين (أو أحدهما/كلاهما محايد)"
    recs_txt = "\n".join(
        f"  - {r['term']}: دخول {r['entry']} — وقف {r['stop']} — هدف {r['target']}"
        for r in computed["recommendations"]
    )

    return (
        frame_block("15", f15)
        + "\n"
        + frame_block("5", f5)
        + "\n"
        + "### النتيجة المجمّعة بعد مقارنة الفريمين\n"
        + f"- حالة التوافق بين الفريمين: {align_txt}\n"
        + f"- الإشارة النهائية: {computed['signalLabel']}\n"
        + f"- قوة الاتجاه المحسوبة: {computed['trendStrength']}/100\n"
        + f"- الزخم المحسوب: {computed['momentum']}/100\n"
        + f"- التقلب العام: {computed['volatility']}\n"
        + f"- الدعم المدمج: {computed['support']} / المقاومة المدمجة: {computed['resistance']}\n"
        + "- التوصيات المحسوبة (الأرقام نهائية، لا تغيّرها):\n"
        + recs_txt
        + "\n\nاكتب الآن التحليل الكامل بصيغة JSON حسب البنية المطلوبة بالضبط، بالاعتماد "
        + "فقط على الأرقام أعلاه."
    )


_capital_client = None
_gold_epic = None


def get_capital_client():
    global _capital_client
    if _capital_client is None:
        from capital_client import CapitalClient

        api_key = os.environ.get("CAPITAL_API_KEY")
        identifier = os.environ.get("CAPITAL_IDENTIFIER")
        password = os.environ.get("CAPITAL_PASSWORD")
        demo = os.environ.get("CAPITAL_DEMO", "true").strip().lower() not in ("false", "0", "no")
        _capital_client = CapitalClient(api_key, identifier, password, demo=demo)
    return _capital_client


def get_gold_epic(client) -> str:
    global _gold_epic
    if _gold_epic is None:
        _gold_epic = client.resolve_gold_epic()
    return _gold_epic


# ===== وضع تحليل صفقة مفتوحة =====

SYSTEM_PROMPT_TRADE = """أنت محلل فني متخصص بقراءة صفقات تداول مفتوحة من صور شاشة منصة تداول (مو تحليل
دخول جديد، هاد لمتابعة صفقة موجودة أصلاً).

بتوصلك صورة سكرين-شوت لشارت عليه صفقة مفتوحة وحدة أو أكتر، مرسومة كخطوط/صناديق سعر.

قواعد قراءة الصورة:
- كل صفقة مفتوحة بتظهر عادة بثلاث عناصر ممكن ينرسم منها وحدة أو اتنين أو التلاتة سوا:
  1) خط/صندوق **سعر الدخول** — وهاد هو المعرّف الحقيقي للصفقة. علامته المميزة: القيمة
     الدولارية جنبه هي **الربح أو الخسارة العائمة الحالية الحقيقية**، يعني بتساوي تقريباً
     (السعر الحالي − سعر الدخول) × الحجم × اتجاه الصفقة. هاي القيمة بتتغير مع كل حركة
     سعر، وعادة الخط هاد ما يكون جنبه أي تسمية "TP" أو ستوب.
  2) خط/صندوق **وقف الخسارة** — القيمة الدولارية جنبه **افتراضية**: شو رح يكون الربح أو
     الخسارة **لو انضرب هالمستوى تحديداً**، مش الربح الحالي. ملاحظة مهمة: إذا كان
     المستخدم حرّك الستوب لمنطقة رابحة (تريلنغ ستوب / بعد كسر التعادل)، القيمة ممكن
     تكون **ربح موجب** مش خسارة — هاد طبيعي وما لازم يلخبطك، يبقى هاد خط ستوب مش خط
     دخول.
  3) خط/صندوق **جني الربح** — نفس مبدأ الستوب: قيمة افتراضية لو انضرب هالمستوى، وممكن
     يكون معلّم بحروف "TP" (بدون رقم إذا ما كان محدد، أو مع رقم إذا محدد).
- **قاعدة حاسمة**: خط الستوب وخط الهدف مش صفقة قائمة بذاتها أبداً — هاد مجرد جزء من
  صفقة، ما تعتبرهم صفقتين منفصلتين ولا تركّب صفقة وهمية من رقم خط الستوب أو الهدف.
  الصفقة الواحدة بتترقم مرة وحدة بس، حتى لو شفت عليها اتنين أو تلات خطوط.
- إذا ما قدرت تحدد بوضوح **خط سعر الدخول نفسه** (يلي فيه الربح/الخسارة العائمة
  الحقيقية) لأي صفقة بالصورة — حتى لو شفت خطوط ستوب أو هدف واضحة — **ما تخترع صفقة
  إطلاقاً**. رجّع بس: {"error": "ما قدرت الاقي خط سعر الدخول (صندوق الربح/الخسارة
  العائمة الحقيقية) بوضوح بالصورة — لازم يكون ظاهر مشان تحليل دقيق، جرب سكرين شوت
  تاني."}
- لون خط الصفقة بيدل على اتجاهها: أحمر = صفقة بيع، أزرق = صفقة شراء. إذا المنصة مستخدمة
  ألوان أو تسميات تانية (متل كلمة SELL أو BUY مكتوبة صراحة)، اعتمد عليها. بس تذكر: اللون
  وحده مش كافي لتحديد إذا الخط هو خط دخول أو خط ستوب أو خط هدف — رجوع لقاعدة الربح
  العائم الحقيقي مقابل الافتراضي فوق.
- **تجاهل تماماً** أي صندوق "شراء سريع/بيع سريع" بزاوية الشاشة (يلي فيه سعرين جنب بعض
  وزر شراء وزر بيع) — هاد مش صفقة مفتوحة، هاد بس زر تنفيذ سريع. وأي رقم "سبريد" ظاهر
  جنبه مش حجم صفقة، تجاهله كلياً.
- الحجم الحقيقي للصفقة هو الرقم الصغير الظاهر جنب خط/صندوق الدخول نفسه (مثال: 0.5)، مش
  أي رقم تاني بالشاشة.
- إذا فيه أكتر من صفقة مفتوحة بنفس الصورة (أكتر من خط دخول حقيقي، كل وحدة إلها ربح/خسارة
  عائمة حقيقية خاصة فيها)، حلل كل وحدة لحالها ورجعهم كلهم بمصفوفة "trades".
- إذا الستوب أو الهدف مش مرسوم إطلاقاً على الصفقة، خلي قيمته null بالـ JSON — لا تخترع
  رقم.
- "statusHeadline" جملة قصيرة توضح وضع الصفقة الحالي (رابحة/خاسرة، قريبة من الستوب أو
  الهدف، ...).
- "recommendation" لازم تكون وحدة بالضبط من: "hold" (استمر متل ما هي)،
  "move_to_breakeven" (حرّك الستوب لنقطة الدخول)، "trail" (فعّل تريلنغ ستوب)،
  "partial_close" (اقفل جزء من الصفقة)، "close" (اقفل الصفقة كاملة). اختار الأنسب بناءً
  على قرب السعر الحالي من الستوب أو الهدف ومقدار الربح المتحقق لهلأ.
- "rationale" اشرح سبب التوصية بوضوح.
- "risks" أي تنبيهات إضافية (مثلاً الستوب قريب كتير، أو الصفقة عم تتحرك عكس الاتجاه
  العام لو بان شي من الشارت). سيب المصفوفة فاضية إذا ما في تنبيهات.
- ما تحسب مسافات أو نسب مخاطرة/عائد بنفسك — هاي بيحسبها النظام تلقائياً من الأرقام يلي
  بترجعها.
- اكتب كل النصوص بالعربي، والأرقام بالإنجليزي (لاتينية).
- إذا الصورة مش شاشة تداول فيها صفقة مفتوحة واضحة أصلاً، رجّع فقط:
  {"error": "وصف قصير للمشكلة بالعربي"}

رجّع **JSON فقط** بدون أي نص قبله أو بعده وبدون code fences، بالضبط بهاد البنية:

{
  "trades": [
    {
      "symbol": "اسم الأداة متل ما هو ظاهر",
      "direction": "buy" | "sell",
      "directionLabel": "شراء" | "بيع",
      "size": رقم أو null,
      "entryPrice": رقم,
      "currentPrice": رقم,
      "stopLoss": رقم أو null,
      "takeProfit": رقم أو null,
      "floatingPnl": رقم أو null,
      "statusHeadline": "...",
      "recommendation": "hold" | "move_to_breakeven" | "trail" | "partial_close" | "close",
      "recommendationLabel": "نص عربي قصير للتوصية",
      "rationale": "...",
      "risks": ["...", "..."]
    }
  ]
}
"""


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def augment_trade(trade: dict) -> dict:
    """بيحسب المسافات ونسبة المخاطرة/العائد رياضياً بالكود — ما بنعتمد على حساب
    النموذج اللغوي مشان الدقة (نفس فلسفة باقي المشروع: الأرقام محسوبة، مش مُخمّنة)."""
    direction = trade.get("direction")
    entry = trade.get("entryPrice")
    current = trade.get("currentPrice")
    stop = trade.get("stopLoss")
    target = trade.get("takeProfit")

    distance_to_stop = None
    distance_to_target = None
    risk_reward_now = None
    pct_to_target = None

    if direction in ("buy", "sell") and _is_num(entry) and _is_num(current):
        if direction == "sell":
            if _is_num(stop):
                distance_to_stop = round(stop - current, 2)
            if _is_num(target):
                distance_to_target = round(current - target, 2)
                if entry != target:
                    pct_to_target = round((entry - current) / (entry - target) * 100, 1)
        else:  # buy
            if _is_num(stop):
                distance_to_stop = round(current - stop, 2)
            if _is_num(target):
                distance_to_target = round(target - current, 2)
                if target != entry:
                    pct_to_target = round((current - entry) / (target - entry) * 100, 1)

        if distance_to_stop is not None and distance_to_stop > 0 and distance_to_target is not None:
            risk_reward_now = round(distance_to_target / distance_to_stop, 2)

    trade["distanceToStop"] = distance_to_stop
    trade["distanceToTarget"] = distance_to_target
    trade["riskRewardNow"] = risk_reward_now
    trade["pctToTarget"] = pct_to_target
    return trade


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

    # وضع الفريمين بيحتاج رد أطول (شرح فريمين + مواءمة بينهم)، فبنزود سقف الطول
    # مشان ما ينقطع الـ JSON قبل ما يكمل.
    max_out_tokens = 3600 if is_mtfa else 2200

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_out_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        raw_text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        data = extract_json(raw_text)
    except json.JSONDecodeError:
        return jsonify({"error": "الرد انقطع قبل ما يكمل — جرب مرة ثانية (أو صورة أوضح إذا تكرر الموضوع)."}), 502
    except Exception as exc:  # noqa: BLE001 — نرجع رسالة مفهومة للواجهة
        return jsonify({"error": f"صار خطأ بالاتصال مع Claude API: {exc}"}), 502

    if "error" in data and len(data) == 1:
        return jsonify(data), 422

    return jsonify(data)


@app.route("/api/analyze-trade", methods=["POST"])
def analyze_trade():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "مفتاح ANTHROPIC_API_KEY مش مضبوط. حط قيمته بملف .env وأعد تشغيل التطبيق."}), 500

    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"error": "لم يتم إرفاق صورة."}), 400

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
            max_tokens=3200,
            system=SYSTEM_PROMPT_TRADE,
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
                            "text": "حلل الصفقة (أو الصفقات) المفتوحة الظاهرة بالصورة واطلع لي JSON فقط حسب البنية المطلوبة بالضبط.",
                        },
                    ],
                }
            ],
        )
        raw_text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        data = extract_json(raw_text)
    except json.JSONDecodeError:
        return jsonify({"error": "الرد انقطع قبل ما يكمل — جرب مرة ثانية."}), 502
    except Exception as exc:  # noqa: BLE001 — نرجع رسالة مفهومة للواجهة
        return jsonify({"error": f"صار خطأ بالاتصال مع Claude API: {exc}"}), 502

    if "error" in data and "trades" not in data:
        return jsonify(data), 422

    trades = [augment_trade(t) for t in data.get("trades", [])]
    return jsonify({"trades": trades})


@app.route("/api/analyze-live", methods=["POST"])
def analyze_live():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "مفتاح ANTHROPIC_API_KEY مش مضبوط. حط قيمته بملف .env وأعد تشغيل التطبيق."}), 500

    if not (os.environ.get("CAPITAL_API_KEY") and os.environ.get("CAPITAL_IDENTIFIER") and os.environ.get("CAPITAL_PASSWORD")):
        return (
            jsonify(
                {
                    "error": "بيانات اعتماد Capital.com مش مضبوطة على السيرفر (CAPITAL_API_KEY / "
                    "CAPITAL_IDENTIFIER / CAPITAL_PASSWORD). أضفهم من إعدادات Render (Environment) وأعد التشغيل."
                }
            ),
            500,
        )

    from capital_client import CapitalAPIError
    import indicators

    try:
        client = get_capital_client()
        epic = get_gold_epic(client)
        candles_15m = client.get_prices(epic, resolution="MINUTE_15", max_points=120)
        candles_5m = client.get_prices(epic, resolution="MINUTE_5", max_points=120)
        computed = indicators.analyze_multi_timeframe(candles_15m, candles_5m)
    except CapitalAPIError as exc:
        return jsonify({"error": f"صار خطأ بجلب بيانات Capital.com: {exc}"}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # noqa: BLE001 — نرجع رسالة مفهومة للواجهة
        return jsonify({"error": f"صار خطأ غير متوقع بجلب أو حساب البيانات: {exc}"}), 502

    prompt_text = build_live_prompt_text(computed)

    # الاستيراد هون بيأخر ظهور خطأ "المفتاح مفقود" إذا الحزمة مش مثبتة أصلاً
    from anthropic import Anthropic

    client_ai = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    try:
        response = client_ai.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=SYSTEM_PROMPT_LIVE,
            messages=[{"role": "user", "content": prompt_text}],
        )
        raw_text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        data = extract_json(raw_text)
    except json.JSONDecodeError:
        return jsonify({"error": "الرد انقطع قبل ما يكمل — جرب مرة ثانية."}), 502
    except Exception as exc:  # noqa: BLE001 — نرجع رسالة مفهومة للواجهة
        return jsonify({"error": f"صار خطأ بالاتصال مع Claude API: {exc}"}), 502

    if "error" in data and len(data) == 1:
        return jsonify(data), 422

    # فرض الأرقام المحسوبة فعلياً بالكود — نفس فلسفة المشروع بكل الأوضاع: النموذج
    # اللغوي بيكتب نص بس، الأرقام والبنية دايماً من الحساب الحقيقي.
    data["symbol"] = "XAU/USD (الذهب)"
    data["symbolShort"] = "Au"
    data["timeframe"] = "15 دقيقة + 5 دقايق (مباشر)"
    data["signal"] = computed["signal"]
    data["signalLabel"] = computed["signalLabel"]
    data["trendStrength"] = computed["trendStrength"]
    data["momentum"] = computed["momentum"]
    data["volatility"] = computed["volatility"]
    data["support"] = computed["support"]
    data["resistance"] = computed["resistance"]

    computed_recs = computed["recommendations"]
    model_recs = data.get("recommendations") or []
    merged_recs = []
    for i, crec in enumerate(computed_recs):
        mrec = model_recs[i] if i < len(model_recs) else {}
        merged_recs.append(
            {
                "term": crec["term"],
                "condition": mrec.get("condition", ""),
                "entry": crec["entry"],
                "stop": crec["stop"],
                "target": crec["target"],
                "rationale": mrec.get("rationale", ""),
            }
        )
    data["recommendations"] = merged_recs

    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
