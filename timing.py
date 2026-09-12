"""V4-1/V4-2 price timing logic shared by snapshot.py and stock.html."""

SIDEWAYS_5D = 2.0
SIDEWAYS_20D = 5.0


def _returns(prices):
    r5 = (prices[-1] / prices[-6] - 1) * 100 if len(prices) >= 6 and prices[-6] else None
    r20 = (prices[-1] / prices[-21] - 1) * 100 if len(prices) >= 21 and prices[-21] else None
    prev5 = (prices[-6] / prices[-11] - 1) * 100 if len(prices) >= 11 and prices[-11] else None
    prev20 = (prices[-21] / prices[-41] - 1) * 100 if len(prices) >= 41 and prices[-41] else None
    return r5, r20, prev5, prev20


def _volatility(prices, n=5):
    if len(prices) < n + 1:
        return None
    rets = [(prices[i] / prices[i-1] - 1) * 100 for i in range(len(prices)-n+1, len(prices)) if prices[i-1]]
    if len(rets) < n:
        return None
    mean = sum(rets) / len(rets)
    return (sum((x-mean)**2 for x in rets) / len(rets)) ** 0.5


def _prior_volatility(prices, n=5):
    if len(prices) < 2*n + 1:
        return None
    end = len(prices)-n
    start = end-n
    rets = [(prices[i] / prices[i-1] - 1) * 100 for i in range(start+1, end+1) if prices[i-1]]
    if len(rets) < n:
        return None
    mean = sum(rets) / len(rets)
    return (sum((x-mean)**2 for x in rets) / len(rets)) ** 0.5


def calc_v41(stock, history_prices):
    price = float(stock.get("price")) if stock.get("price") is not None else None
    high = float(stock.get("week52High")) if stock.get("week52High") is not None else None
    low = float(stock.get("week52Low")) if stock.get("week52Low") is not None else None
    high_drop = (price / high - 1) * 100 if price and high and high > 0 else None
    pos = max(0, min(100, (price-low)/(high-low)*100)) if price is not None and high and low is not None and high > low else None
    r5, r20, _, _ = _returns(history_prices)
    state5 = "데이터 부족" if r5 is None else ("횡보" if abs(r5) <= SIDEWAYS_5D else ("상승" if r5 > 0 else "하락"))
    state20 = "데이터 부족" if r20 is None else ("횡보" if abs(r20) <= SIDEWAYS_20D else ("상승" if r20 > 0 else "하락"))
    pattern, cls = "관찰", "warn"
    if high_drop is not None and high_drop > -10 and r5 is not None and r5 > 3:
        pattern, cls = "고점 추격 주의", "bad"
    elif high_drop is not None and high_drop <= -15 and r20 is not None and r20 < 0 and r5 is not None and abs(r5) <= SIDEWAYS_5D:
        pattern, cls = "조정 후 횡보", "good"
    elif r5 is not None and r20 is not None and r5 > 0 and r20 > 0:
        pattern, cls = "상승 추세", "good"
    elif r5 is not None and r20 is not None and r5 < -3 and r20 < -5:
        pattern, cls = "하락 추세", "bad"
    elif r5 is not None and abs(r5) <= SIDEWAYS_5D:
        pattern, cls = "단기 횡보", "warn"
    return {"highDrop":high_drop,"pos":pos,"r5":r5,"r20":r20,"state5":state5,"state20":state20,"pattern":pattern,"patternClass":cls}


def calc_v42(stock, v41, history_prices, market_prices=None):
    """V4-2는 기존 평가점수와 겹치지 않도록 가격/수급 흐름만 판단한다."""
    prices = history_prices or []
    r5, r20, prev5, prev20 = _returns(prices)
    vol5 = _volatility(prices, 5)
    prev_vol5 = _prior_volatility(prices, 5)

    # ① 단기 모멘텀이 살아나는가?
    q1 = None if r5 is None else r5 > 0

    # ② 중기 추세가 개선되고 있는가? 20일 수익률이 이전 20일보다 개선되는지
    q2 = None if r20 is None or prev20 is None else r20 > prev20

    # ③ 하락이 멈추고 반전 신호가 나타나는가? 최근 5일이 이전 5일보다 개선되고, 현재가 급락은 아닌지
    q3 = None if r5 is None or prev5 is None else (r5 > prev5 and r5 >= -2)

    # ④ 최근 변동성이 직전 구간보다 줄어들고 있는가?
    q4 = None if vol5 is None or prev_vol5 is None else vol5 < prev_vol5

    # ⑤ KOSPI 대비 최근 5일 상대강도가 양호한가?
    market_r5 = None
    if market_prices and len(market_prices) >= 6 and market_prices[-6]:
        market_r5 = (market_prices[-1] / market_prices[-6] - 1) * 100
    q5 = None if r5 is None or market_r5 is None else r5 > market_r5

    checks = [q1,q2,q3,q4,q5]
    true_count = sum(x is True for x in checks)
    known_count = sum(x is not None for x in checks)

    # 위험 패턴은 우선 주의. 그 외에는 충족 비율로 관심/관찰. 데이터 부족 시 관찰.
    status, cls = "관찰", "warn"
    if v41["pattern"] in ("고점 추격 주의", "하락 추세"):
        status, cls = "주의", "bad"
    elif known_count >= 3 and true_count / known_count >= 0.8:
        status, cls = "관심", "good"
    elif known_count >= 3 and true_count / known_count <= 0.4:
        status, cls = "주의", "bad"

    return {
        **v41, "q1":q1,"q2":q2,"q3":q3,"q4":q4,"q5":q5,
        "trueCount":true_count,"knownCount":known_count,"marketR5":market_r5,
        "vol5":vol5,"prevVol5":prev_vol5,"prev5":prev5,"prev20":prev20,
        "status":status,"statusClass":cls
    }
