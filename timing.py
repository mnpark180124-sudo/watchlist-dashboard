"""V4-1/V4-2 price timing logic shared by snapshot.py and documentation."""

SIDEWAYS_5D = 2.0
SIDEWAYS_20D = 5.0


def _returns(prices):
    r5 = (prices[-1] / prices[-6] - 1) * 100 if len(prices) >= 6 and prices[-6] else None
    r20 = (prices[-1] / prices[-21] - 1) * 100 if len(prices) >= 21 and prices[-21] else None
    return r5, r20


def calc_v41(stock, history_prices):
    price = float(stock.get("price")) if stock.get("price") is not None else None
    high = float(stock.get("week52High")) if stock.get("week52High") is not None else None
    low = float(stock.get("week52Low")) if stock.get("week52Low") is not None else None
    high_drop = (price / high - 1) * 100 if price and high and high > 0 else None
    pos = max(0, min(100, (price-low)/(high-low)*100)) if price is not None and high and low is not None and high > low else None
    r5, r20 = _returns(history_prices)
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


def calc_v42(stock, v41, news_info=None):
    debt = stock.get("debtRatio"); roe = stock.get("roe")
    if debt is None and roe is None:
        q4 = None
    else:
        parts=[]
        if debt is not None: parts.append(max(0,min(100,100-float(debt)/2)))
        if roe is not None: parts.append(max(0,min(100,float(roe)*5)))
        q4 = sum(parts)/len(parts) >= 60
    upside = None
    if stock.get("targetPrice") and stock.get("price"):
        upside=(float(stock["targetPrice"])/float(stock["price"])-1)*100
    target_good = upside >= 10 if upside is not None else stock.get("opinion") in ("매수","강력매수")
    news_bad = bool(news_info and news_info.get("hasImportantNews") and news_info.get("direction")=="down" and float(news_info.get("impactPct") or 0)>=5)
    q5 = None if (upside is None and not stock.get("opinion") and news_info is None) else bool(target_good and not news_bad)
    q1 = None if v41["highDrop"] is None else v41["highDrop"] <= -10
    q2 = None if v41["r20"] is None else v41["r20"] > -10
    q3 = None if v41["r5"] is None else v41["r5"] >= -2
    status, cls = "관찰", "warn"
    if v41["pattern"] in ("고점 추격 주의","하락 추세") or q2 is False or q3 is False:
        status, cls = "주의", "bad"
    elif q1 is True and q2 is not False and q3 is True and q4 is not False and q5 is not False:
        status, cls = "관심", "good"
    return {"status":status,"statusClass":cls,"q1":q1,"q2":q2,"q3":q3,"q4":q4,"q5":q5,"upside":upside}
