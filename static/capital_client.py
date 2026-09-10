"""
عميل بسيط لـ Capital.com Public API — تسجيل دخول (session) وجلب بيانات
شموع تاريخية حقيقية لأداة معينة (هون: الذهب).

التوثيق: https://open-api.capital.com/
"""
import time
import requests


class CapitalAPIError(Exception):
    """خطأ واضح فيه تفاصيل الرد الفعلي من Capital.com عشان يسهل تشخيصه."""


class CapitalClient:
    DEMO_BASE = "https://demo-api-capital.backend-capital.com/api/v1"
    LIVE_BASE = "https://api-capital.backend-capital.com/api/v1"

    def __init__(self, api_key: str, identifier: str, password: str, demo: bool = True):
        if not (api_key and identifier and password):
            raise CapitalAPIError("بيانات اعتماد Capital.com ناقصة (API key / identifier / password).")
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.base = self.DEMO_BASE if demo else self.LIVE_BASE
        self.cst = None
        self.security_token = None
        self._session_started_at = 0.0
        self._session_ttl_seconds = 8 * 60

    def _login(self):
        try:
            resp = requests.post(
                f"{self.base}/session",
                headers={"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"},
                json={"identifier": self.identifier, "password": self.password},
                timeout=20,
            )
        except requests.RequestException as exc:
            raise CapitalAPIError(f"تعذر الاتصال بـ Capital.com: {exc}") from exc

        if resp.status_code != 200:
            raise CapitalAPIError(
                f"فشل تسجيل الدخول لـ Capital.com (HTTP {resp.status_code}): {resp.text[:400]}"
            )

        cst = resp.headers.get("CST")
        sec = resp.headers.get("X-SECURITY-TOKEN")
        if not cst or not sec:
            raise CapitalAPIError("رد تسجيل الدخول ما فيه توكنات الجلسة (CST / X-SECURITY-TOKEN).")

        self.cst = cst
        self.security_token = sec
        self._session_started_at = time.time()

    def _auth_headers(self) -> dict:
        expired = (time.time() - self._session_started_at) > self._session_ttl_seconds
        if not self.cst or expired:
            self._login()
        return {"CST": self.cst, "X-SECURITY-TOKEN": self.security_token}

    def _get(self, path: str, params: dict | None = None) -> dict:
        headers = self._auth_headers()
        resp = requests.get(f"{self.base}{path}", headers=headers, params=params, timeout=20)
        if resp.status_code == 401:
            self._login()
            headers = self._auth_headers()
            resp = requests.get(f"{self.base}{path}", headers=headers, params=params, timeout=20)
        if resp.status_code != 200:
            raise CapitalAPIError(f"طلب {path} فشل (HTTP {resp.status_code}): {resp.text[:400]}")
        return resp.json()

    def search_markets(self, term: str) -> list:
        data = self._get("/markets", params={"searchTerm": term})
        return data.get("markets", [])

    def resolve_gold_epic(self) -> str:
        """بيدور على الـ epic الصحيح للذهب سبوت (XAU/USD) عند Capital.com."""
        candidates = self.search_markets("gold")
        preferred_names = {"gold", "spot gold", "xau/usd", "gold spot"}
        best = None
        for m in candidates:
            name = (m.get("instrumentName") or "").strip().lower()
            epic = m.get("epic") or ""
            if epic.upper() == "GOLD":
                return epic
            if name in preferred_names and best is None:
                best = epic
        if best:
            return best
        for m in candidates:
            name = (m.get("instrumentName") or "").lower()
            if "gold" in name and m.get("epic"):
                return m["epic"]
        raise CapitalAPIError(
            "ما قدرنا نلاقي رمز الذهب (epic) بحساب Capital.com — النتائج يلي رجعت: "
            + ", ".join(f"{m.get('epic')}={m.get('instrumentName')}" for m in candidates[:10])
        )

    def get_prices(self, epic: str, resolution: str = "MINUTE_15", max_points: int = 120) -> list:
        """
        بيرجع لائحة شموع OHLC حقيقية. كل عنصر: dict فيه
        snapshotTime, open, high, low, close (متوسط bid/ask).
        """
        data = self._get(f"/prices/{epic}", params={"resolution": resolution, "max": max_points})
        raw = data.get("prices", [])
        if not raw:
            raise CapitalAPIError(f"Capital.com ما رجع أي بيانات أسعار لـ {epic} / {resolution}.")

        def mid(node):
            if node is None:
                return None
            bid = node.get("bid")
            ask = node.get("ask")
            if bid is not None and ask is not None:
                return (bid + ask) / 2
            return bid if bid is not None else ask

        candles = []
        for p in raw:
            o, h, l, c = mid(p.get("openPrice")), mid(p.get("highPrice")), mid(p.get("lowPrice")), mid(p.get("closePrice"))
            if None in (o, h, l, c):
                continue
            candles.append(
                {
                    "time": p.get("snapshotTimeUTC") or p.get("snapshotTime"),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                }
            )
        if not candles:
            raise CapitalAPIError("رجعت بيانات من Capital.com بس ما قدرنا نستخرج أسعار صالحة منها.")
        return candles
