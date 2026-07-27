"""
평가점수 계산 로직 (index.html의 JS 계산식을 파이썬으로 그대로 복제)

주의: index.html의 computeBaseScore / computeMacroAdjustment / SCORE_WEIGHTS / MACRO_WEIGHTS를
바꾸면 이 파일도 반드시 같이 고쳐야 한다. 안 그러면 화면에 보이는 점수랑 기록(backtest)용
점수가 서로 달라진다.
"""

SCORE_WEIGHTS = {"target": 0.30, "opinion": 0.20, "week52": 0.20, "news": 0.20, "financial": 0.10}
OPINION_SCORE = {"강력매수": 100, "매수": 80, "중립": 50, "매도": 20, "강력매도": 0}
MACRO_WEIGHTS = {"usdkrw": 5, "nasdaq": 5, "geo": 4, "vix": 3, "us10y": 2}


def compute_base_score(stock: dict, news_info: dict | None) -> float | None:
    parts = []  # (score, weight)

    target_price = stock.get("targetPrice")
    price = stock.get("price")
    if target_price and price:
        upside_pct = (target_price / price - 1) * 100
        sc = max(0, min(100, (upside_pct / 50) * 100))
        parts.append((sc, SCORE_WEIGHTS["target"]))

    opinion = stock.get("opinion")
    if opinion in OPINION_SCORE:
        parts.append((OPINION_SCORE[opinion], SCORE_WEIGHTS["opinion"]))

    week52_high = stock.get("week52High")
    week52_low = stock.get("week52Low")
    if week52_high and week52_low and week52_high > week52_low and price is not None:
        pos = (price - week52_low) / (week52_high - week52_low) * 100
        parts.append((max(0, min(100, 100 - pos)), SCORE_WEIGHTS["week52"]))

    news_score = 50.0
    if news_info and news_info.get("hasImportantNews"):
        impact = news_info.get("impactPct") or 0
        if news_info.get("direction") == "down":
            news_score = max(0, 50 - impact / 2)
        else:
            news_score = min(100, 50 + impact / 2)
    parts.append((news_score, SCORE_WEIGHTS["news"]))

    fin_parts = []
    debt_ratio = stock.get("debtRatio")
    roe = stock.get("roe")
    if debt_ratio is not None:
        fin_parts.append(max(0, min(100, 100 - debt_ratio / 2)))
    if roe is not None:
        fin_parts.append(max(0, min(100, roe * 5)))
    if fin_parts:
        parts.append((sum(fin_parts) / len(fin_parts), SCORE_WEIGHTS["financial"]))

    total_weight = sum(w for _, w in parts)
    if total_weight <= 0:
        return None
    weighted = sum(sc * w for sc, w in parts)
    return weighted / total_weight


def compute_macro_adjustment(macro: dict | None, geo: dict | None) -> float:
    adj = 0.0

    if macro and macro.get("usdkrw") and macro["usdkrw"].get("changeRate") is not None:
        rate = macro["usdkrw"]["changeRate"]
        w = MACRO_WEIGHTS["usdkrw"]
        adj += w if rate < 0 else (-w if rate > 0 else 0)

    if macro and macro.get("nasdaq") and macro["nasdaq"].get("changeRate") is not None:
        rate = macro["nasdaq"]["changeRate"]
        w = MACRO_WEIGHTS["nasdaq"]
        adj += w if rate > 0 else (-w if rate < 0 else 0)

    if geo and geo.get("hasRisk"):
        w = MACRO_WEIGHTS["geo"]
        direction = geo.get("direction")
        adj += -w if direction == "down" else (w if direction == "up" else 0)

    if macro and macro.get("vix") and macro["vix"].get("changeRate") is not None:
        rate = macro["vix"]["changeRate"]
        w = MACRO_WEIGHTS["vix"]
        adj += -w if rate > 0 else (w if rate < 0 else 0)

    if macro and macro.get("us10y") and macro["us10y"].get("changeRate") is not None:
        rate = macro["us10y"]["changeRate"]
        w = MACRO_WEIGHTS["us10y"]
        adj += -w if rate > 0 else (w if rate < 0 else 0)

    return adj


def compute_score(stock: dict, news_info: dict | None, macro: dict | None, geo: dict | None) -> float | None:
    base = compute_base_score(stock, news_info)
    if base is None:
        return None
    adj = compute_macro_adjustment(macro, geo)
    return max(0, min(100, base + adj))
