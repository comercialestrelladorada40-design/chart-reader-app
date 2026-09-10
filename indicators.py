"""
حساب مؤشرات فنية حقيقية (مش تقدير بصري) من شموع OHLC حية — مشان وضع "تحليل مباشر":
SuperTrend, RSI, ATR, MACD, بولينجر باندز, التقلب التاريخي (HV), دعم/مقاومة.

يعتمد فقط على pandas/numpy — حسابات رياضية معروفة ومباشرة. الفلسفة نفسها المتبعة
بباقي المشروع: كل رقم محسوب بالكود، ما في أي رقم من نموذج لغوي — النموذج بس بيشرح
ويلخّص الأرقام الجاهزة.

يدمج فريمين (15 دقيقة = البوصلة، 5 دقايق = الزناد) بمنطق "توافق مؤشرات" (confluence):
كل فريم بيوصّت 4 مؤشرات (SuperTrend, RSI, MACD histogram, موقع السعر من بولينجر) على
اتجاه شراء/بيع/محايد، وبعدين بتتقارن نتيجة الفريمين مع بعض.
"""
import numpy as np
import pandas as pd


# ===== أدوات أساسية =====

def to_dataframe(candles: list) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    atr = compute_atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upperband = hl2 + multiplier * atr
    lowerband = hl2 - multiplier * atr

    final_upper = upperband.copy()
    final_lower = lowerband.copy()
    trend = pd.Series(index=df.index, dtype="int64")
    trend.iloc[0] = 1

    for i in range(1, len(df)):
        if df["close"].iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = min(upperband.iloc[i], final_upper.iloc[i - 1]) if df["close"].iloc[i] > final_upper.iloc[i - 1] else upperband.iloc[i]
        else:
            final_upper.iloc[i] = min(upperband.iloc[i], final_upper.iloc[i - 1])

        if df["close"].iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = max(lowerband.iloc[i], final_lower.iloc[i - 1]) if df["close"].iloc[i] < final_lower.iloc[i - 1] else lowerband.iloc[i]
        else:
            final_lower.iloc[i] = max(lowerband.iloc[i], final_lower.iloc[i - 1])

        if df["close"].iloc[i] > final_upper.iloc[i - 1]:
            trend.iloc[i] = 1
        elif df["close"].iloc[i] < final_lower.iloc[i - 1]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i - 1]

    supertrend_line = np.where(trend == 1, final_lower, final_upper)
    return pd.Series(supertrend_line, index=df.index), trend, atr


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0):
    close = df["close"]
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return mid, upper, lower


def compute_hv(df: pd.DataFrame, period: int = 20, interval_minutes: int = 15) -> pd.Series:
    """التقلب التاريخي السنوي (%) مبني على انحراف العوائد اللوغاريتمية، معدّل
    حسب طول الفريم الزمني (مشان يكون قابل للمقارنة بين فريم 5 و15 دقيقة)."""
    log_ret = np.log(df["close"] / df["close"].shift(1))
    minutes_per_year = 365 * 24 * 60
    periods_per_year = minutes_per_year / interval_minutes
    hv = log_ret.rolling(period).std() * np.sqrt(periods_per_year) * 100
    return hv


def find_swing_points(df: pd.DataFrame, window: int = 3):
    """قمم وقيعان محلية بسيطة (swing highs/lows) لاستخراج دعم/مقاومة."""
    highs, lows = [], []
    n = len(df)
    for i in range(window, n - window):
        seg_high = df["high"].iloc[i - window : i + window + 1]
        seg_low = df["low"].iloc[i - window : i + window + 1]
        if df["high"].iloc[i] == seg_high.max():
            highs.append(df["high"].iloc[i])
        if df["low"].iloc[i] == seg_low.min():
            lows.append(df["low"].iloc[i])
    return sorted(set(round(h, 2) for h in highs)), sorted(set(round(l, 2) for l in lows))


def support_resistance(df: pd.DataFrame, current_price: float, window: int = 3, max_levels: int = 3):
    highs, lows = find_swing_points(df, window=window)
    resistance = sorted([h for h in highs if h > current_price])[:max_levels]
    support = sorted([l for l in lows if l < current_price], reverse=True)[:max_levels]
    support.sort()
    return support, resistance


def classify_volatility(atr_value: float, price: float) -> str:
    pct = (atr_value / price) * 100 if price else 0
    if pct < 0.15:
        return "منخفض"
    if pct < 0.4:
        return "متوسط"
    return "مرتفع"


# ===== تحليل فريم وحيد (يحسب كل المؤشرات + تصويت التوافق) =====

SIGNAL_LABELS = {"buy": "شراء", "sell": "بيع", "neutral": "محايد"}


def analyze_frame(candles: list, interval_minutes: int, min_candles: int = 30) -> dict:
    df = to_dataframe(candles)
    if len(df) < min_candles:
        raise ValueError(
            f"عدد الشموع المرجعة قليل جداً لفريم {interval_minutes} دقيقة "
            f"({len(df)} شمعة، والمطلوب {min_candles} على الأقل)."
        )

    supertrend_line, trend, atr = compute_supertrend(df)
    rsi = compute_rsi(df)
    macd_line, signal_line, hist = compute_macd(df)
    bb_mid, bb_upper, bb_lower = compute_bollinger(df)
    hv = compute_hv(df, interval_minutes=interval_minutes)

    def last(series, default=0.0):
        v = float(series.iloc[-1])
        return default if np.isnan(v) else v

    current_price = float(df["close"].iloc[-1])
    current_trend = int(trend.iloc[-1])
    current_atr = last(atr, 0.0)
    current_rsi = last(rsi, 50.0)
    current_hist = last(hist, 0.0)
    current_bb_mid = last(bb_mid, current_price)
    current_bb_upper = last(bb_upper, current_price)
    current_bb_lower = last(bb_lower, current_price)
    current_hv = last(hv, 0.0)

    support, resistance = support_resistance(df, current_price)
    volatility = classify_volatility(current_atr, current_price)

    # ===== تصويت التوافق بين 4 مؤشرات =====
    buy_votes = 0
    sell_votes = 0

    # SuperTrend
    if current_trend == 1:
        buy_votes += 1
    else:
        sell_votes += 1

    # RSI (مع منطقة محايدة حول 50 مشان ما يصوّت بإشارة ضعيفة)
    if current_rsi >= 55:
        buy_votes += 1
    elif current_rsi <= 45:
        sell_votes += 1

    # MACD histogram
    if current_hist > 0:
        buy_votes += 1
    elif current_hist < 0:
        sell_votes += 1

    # موقع السعر من الوسط المتحرك لبولينجر
    if current_price > current_bb_mid:
        buy_votes += 1
    elif current_price < current_bb_mid:
        sell_votes += 1

    total_indicators = 4
    # إشارة الفريم لا تُعتبر واضحة إلا بأغلبية 3 من 4 مؤشرات (توافق حقيقي)
    if buy_votes >= 3 and buy_votes > sell_votes:
        signal = "buy"
    elif sell_votes >= 3 and sell_votes > buy_votes:
        signal = "sell"
    else:
        signal = "neutral"

    return {
        "intervalMinutes": interval_minutes,
        "currentPrice": round(current_price, 2),
        "trend": "صاعد" if current_trend == 1 else "هابط",
        "trendDirectionNum": current_trend,
        "rsi": round(current_rsi, 1),
        "atr": round(current_atr, 3),
        "macdHistogram": round(current_hist, 4),
        "bollingerMid": round(current_bb_mid, 2),
        "bollingerUpper": round(current_bb_upper, 2),
        "bollingerLower": round(current_bb_lower, 2),
        "hv": round(current_hv, 2),
        "volatility": volatility,
        "support": [round(s, 2) for s in support],
        "resistance": [round(r, 2) for r in resistance],
        "buyVotes": buy_votes,
        "sellVotes": sell_votes,
        "totalIndicators": total_indicators,
        "signal": signal,
        "signalLabel": SIGNAL_LABELS[signal],
        "candleCount": len(df),
    }


def _merge_levels(list_a, list_b, current_price, above: bool, max_levels: int = 3):
    combined = sorted(set(round(x, 2) for x in list(list_a) + list(list_b)))
    if above:
        levels = sorted([x for x in combined if x > current_price])[:max_levels]
    else:
        levels = sorted([x for x in combined if x < current_price], reverse=True)[:max_levels]
        levels.sort()
    return levels


def analyze_multi_timeframe(candles_15m: list, candles_5m: list) -> dict:
    """يدمج فريم 15 دقيقة (البوصلة) مع فريم 5 دقايق (الزناد) بنفس منهجية باقي
    المشروع: ما في توصية فعلية إلا لو الفريمين متوافقين على نفس الاتجاه."""
    frame15 = analyze_frame(candles_15m, interval_minutes=15)
    frame5 = analyze_frame(candles_5m, interval_minutes=5)

    current_price = frame5["currentPrice"]

    aligned = frame15["signal"] != "neutral" and frame15["signal"] == frame5["signal"]
    signal = frame15["signal"] if aligned else "neutral"
    signal_label = SIGNAL_LABELS[signal]

    if signal != "neutral":
        strength_votes = frame15["buyVotes" if signal == "buy" else "sellVotes"] + frame5["buyVotes" if signal == "buy" else "sellVotes"]
    else:
        strength_votes = max(
            frame15["buyVotes"] + frame5["buyVotes"],
            frame15["sellVotes"] + frame5["sellVotes"],
        )
    trend_strength = int(round(min(100, max(0, strength_votes / 8 * 100))))
    momentum = int(round((frame15["rsi"] + frame5["rsi"]) / 2))
    volatility = frame5["volatility"]

    resistance = _merge_levels(frame5["resistance"], frame15["resistance"], current_price, above=True)
    support = _merge_levels(frame5["support"], frame15["support"], current_price, above=False)

    # اتجاه بناء التوصيات: لو متوافقين نعتمد الإشارة المشتركة، لو لأ نعتمد البوصلة
    # (15 دقيقة) إذا كانت واضحة، وإلا الزناد (5 دقايق)، وإلا أغلبية الأصوات المجمّعة
    if aligned:
        direction = 1 if signal == "buy" else -1
    elif frame15["signal"] != "neutral":
        direction = 1 if frame15["signal"] == "buy" else -1
    elif frame5["signal"] != "neutral":
        direction = 1 if frame5["signal"] == "buy" else -1
    else:
        direction = 1 if (frame15["buyVotes"] + frame5["buyVotes"]) >= (frame15["sellVotes"] + frame5["sellVotes"]) else -1

    entry = current_price
    atr5 = frame5["atr"] if frame5["atr"] else current_price * 0.001

    # أدنى نسبة عائد/مخاطرة مقبولة مشان نسمح بتقريب الهدف لأقرب دعم/مقاومة. لو
    # تقريب الهدف لأقرب مستوى بيخلي العائد أصغر من المخاطرة (صفقة غير منطقية)،
    # نتجاهل التقريب ونخلي الهدف على أساس ATR الخام (نفس فلسفة باقي المشروع: ما
    # نعطي توصية بأرقام غير متوازنة حتى لو قريبة من مستوى فني ظاهر).
    MIN_ACCEPTABLE_RR = 1.0

    def build_recommendation(term: str, atr_stop_mult: float, atr_target_mult: float):
        stop = entry - direction * atr_stop_mult * atr5
        target = entry + direction * atr_target_mult * atr5
        risk = abs(entry - stop)
        if direction == 1 and resistance:
            nearer = min([r for r in resistance if r > entry], default=None)
            if nearer and nearer < target:
                reward = abs(nearer - entry)
                if risk > 0 and reward / risk >= MIN_ACCEPTABLE_RR:
                    target = nearer
        if direction == -1 and support:
            nearer = max([s for s in support if s < entry], default=None)
            if nearer and nearer > target:
                reward = abs(entry - nearer)
                if risk > 0 and reward / risk >= MIN_ACCEPTABLE_RR:
                    target = nearer
        return {"term": term, "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2)}

    recommendations = [
        build_recommendation("قصيرة المدى", atr_stop_mult=1.2, atr_target_mult=2.0),
        build_recommendation("متوسطة المدى", atr_stop_mult=2.2, atr_target_mult=3.8),
    ]

    return {
        "currentPrice": current_price,
        "signal": signal,
        "signalLabel": signal_label,
        "trendStrength": trend_strength,
        "momentum": momentum,
        "volatility": volatility,
        "support": support,
        "resistance": resistance,
        "aligned": aligned,
        "recommendations": recommendations,
        "frame15": frame15,
        "frame5": frame5,
    }
