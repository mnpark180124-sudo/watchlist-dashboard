"""평가점수 A안 개선형.
index.html의 JS 계산식과 동일하게 유지한다.
- 포화 완화(piecewise)
- 금융주: 일반기업 부채비율 감점 제외, ROE 중심
- ETF/결측: 누락 항목 재정규화 대신 중립 50점
- 뉴스: materialImpact=true 실질 이벤트만 반영
- 매크로: 최대 ±10점, 야간 미국 섹터는 점수 미반영
"""
SCORE_WEIGHTS={"target":0.30,"opinion":0.20,"week52":0.15,"news":0.15,"financial":0.10,"shortSelling":0.10}
OPINION_SCORE={"강력매수":95,"매수":80,"비중확대":70,"중립":55,"보유":55,"비중축소":35,"매도":20,"강력매도":10}
MACRO_WEIGHTS={"usdkrw":2,"nasdaq":3,"geo":2,"vix":2,"us10y":1}
def _piecewise(v,bands,default=50):
    if v is None:return default
    for limit,score in bands:
        if v<=limit:return score
    return bands[-1][1]
def _target_score(u):
    if u is None:return 50
    if u<=0:return 20
    return _piecewise(u,[(10,30),(20,45),(30,58),(40,68),(50,76),(60,82),(70,87),(85,92),(100,96),(float("inf"),100)])
def _week52_score(dd):
    if dd is None:return 50
    d=abs(min(0.0,dd))
    return _piecewise(d,[(10,45),(20,55),(30,70),(40,82),(50,90),(60,85),(70,75),(float("inf"),60)])
def _roe_score(v):return _piecewise(v,[(0,20),(5,35),(10,50),(15,62),(20,72),(25,80),(30,85),(40,92),(float("inf"),100)])
def _debt_score(v):return _piecewise(v,[(50,95),(80,90),(100,82),(150,70),(200,55),(300,40),(float("inf"),25)])
def _short_score(v):return _piecewise(v,[(0.1,90),(0.3,85),(0.5,80),(0.8,75),(1.0,70),(1.5,60),(2.0,50),(3.0,40),(float("inf"),30)])
def compute_base_score(stock:dict,news_info:dict|None)->float|None:
    price=stock.get("price"); target=stock.get("targetPrice")
    upside=((target/price-1)*100) if target and price else None
    high=stock.get("week52High"); dd=((price/high-1)*100) if high and price else None
    news=50.0
    if news_info and news_info.get("hasImportantNews") and news_info.get("materialImpact"):
        impact=max(0.0,min(30.0,float(news_info.get("impactPct") or 0)))
        if news_info.get("direction")=="down":news=50-impact/2
        elif news_info.get("direction")=="up":news=50+impact/2
    roe=_roe_score(stock.get("roe"))
    if stock.get("sector")=="금융":fin=roe
    elif stock.get("roe") is None and stock.get("debtRatio") is None:fin=50
    else:fin=roe*0.60+_debt_score(stock.get("debtRatio"))*0.40
    scores={"target":_target_score(upside),"opinion":OPINION_SCORE.get(stock.get("opinion"),50),"week52":_week52_score(dd),"news":news,"financial":fin,"shortSelling":_short_score(stock.get("shortSellingRatio"))}
    return sum(scores[k]*SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
def compute_macro_adjustment(macro:dict|None,geo:dict|None)->float:
    adj=0.0
    if macro and macro.get("usdkrw") and macro["usdkrw"].get("changeRate") is not None:
        r=macro["usdkrw"]["changeRate"];w=MACRO_WEIGHTS["usdkrw"];adj+=w if r<0 else (-w if r>0 else 0)
    if macro and macro.get("nasdaq") and macro["nasdaq"].get("changeRate") is not None:
        r=macro["nasdaq"]["changeRate"];w=MACRO_WEIGHTS["nasdaq"];adj+=w if r>0 else (-w if r<0 else 0)
    if geo and geo.get("hasRisk"):
        w=MACRO_WEIGHTS["geo"];d=geo.get("direction");adj+=-w if d=="down" else (w if d=="up" else 0)
    if macro and macro.get("vix") and macro["vix"].get("changeRate") is not None:
        r=macro["vix"]["changeRate"];w=MACRO_WEIGHTS["vix"];adj+=-w if r>0 else (w if r<0 else 0)
    if macro and macro.get("us10y") and macro["us10y"].get("changeRate") is not None:
        r=macro["us10y"]["changeRate"];w=MACRO_WEIGHTS["us10y"];adj+=-w if r>0 else (w if r<0 else 0)
    return max(-10.0,min(10.0,adj))
def compute_score(stock:dict,news_info:dict|None,macro:dict|None,geo:dict|None)->float|None:
    base=compute_base_score(stock,news_info)
    return None if base is None else max(0,min(100,base+compute_macro_adjustment(macro,geo)))
