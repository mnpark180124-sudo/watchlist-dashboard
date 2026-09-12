"""
평가점수 기록기 (백테스트용 데이터 수집)

장 마감 후 하루 1번 실행해서:
1. 오늘자 각 종목의 평가점수를 계산해 data/score_history.json 에 날짜별로 쌓는다.
2. 어제 기록된 점수와 오늘 실제 가격 변화를 짝지어서 "그 점수를 받았을 때 다음날 실제로
   얼마나 움직였는지"를 data/backtest.json 에 계속 누적한다.

이렇게 100일 정도 쌓으면, 고점수 종목군과 저점수 종목군의 평균 다음날 수익률을 비교해서
평가점수가 실제로 의미가 있는 신호인지 확인해볼 수 있다.
"""

import json
import os
from datetime import datetime, timezone, timedelta

from scoring import compute_score
from timing import calc_v41, calc_v42

KST = timezone(timedelta(hours=9))


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    os.makedirs("data", exist_ok=True)

    stocks_data = load_json("data/stocks.json", {"stocks": []})
    news_data = load_json("data/news.json", {})
    macro_data = load_json("data/macro.json", {})
    geo_data = load_json("data/geopolitical.json", {})

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # V4-2 상대강도용 KOSPI 종가. 실패해도 기존 기록 수집은 계속한다.
    kospi_price = None
    try:
        import yfinance as yf
        kh = yf.Ticker("^KS11").history(period="5d")
        if len(kh):
            kospi_price = round(float(kh["Close"].iloc[-1]), 2)
    except Exception as e:
        print(f"[V4-2 KOSPI 실패] {e}")
    if kospi_price is None:
        try:
            kospi_price = float((macro_data.get("kospi") or {}).get("price"))
        except (TypeError, ValueError):
            kospi_price = None

    # ---- 1) 오늘자 점수 스냅샷 계산 ----
    today_snapshot = []
    for s in stocks_data.get("stocks", []):
        score = compute_score(s, news_data.get(s["name"]), macro_data, geo_data)
        if score is None or s.get("price") is None:
            continue
        today_snapshot.append({
            "code": s["code"],
            "name": s["name"],
            "score": round(score, 1),
            "price": s["price"],
        })

    # ---- 2) 기존 기록 불러오기 ----
    history = load_json("data/score_history.json", {"days": []})
    timing_history = load_json("data/timing_history.json", {"entries": []})

    # 오늘 이미 기록된 날짜면(같은 날 여러 번 실행) 덮어쓰기, 아니면 새로 추가
    history["days"] = [d for d in history["days"] if d["date"] != today]
    history["days"].append({"date": today, "stocks": today_snapshot, "kospiPrice": kospi_price})
    # 너무 오래된 기록까지 무한정 쌓이지 않도록 최근 150일치만 유지
    history["days"] = sorted(history["days"], key=lambda d: d["date"])[-150:]

    # ---- 2-b) V4-2 타이밍 판정 기록 ----
    price_history = {}
    for day in history["days"]:
        for row in day.get("stocks", []):
            try: price_history.setdefault(row["code"], []).append((day["date"], float(row["price"])))
            except (KeyError, TypeError, ValueError): pass
    news_by_name = news_data
    market_history = []
    for day in history["days"]:
        try:
            kp = float(day.get("kospiPrice"))
            if kp > 0: market_history.append((day["date"], kp))
        except (TypeError, ValueError, KeyError):
            pass
    market_history.sort(key=lambda x: x[0])
    market_prices = [p for _, p in market_history]
    today_timing = []
    for s in stocks_data.get("stocks", []):
        prices = [p for _,p in price_history.get(s["code"], [])]
        v41 = calc_v41(s, prices)
        v42 = calc_v42(s, v41, prices, market_prices)
        if s.get("price") is None: continue
        today_timing.append({"date":today,"code":s["code"],"name":s["name"],"price":s["price"],"score":next((x["score"] for x in today_snapshot if x["code"]==s["code"]),None),"pattern":v41["pattern"],"status":v42["status"],"highDrop":v41["highDrop"],"r5":v41["r5"],"r20":v41["r20"],"q1":v42["q1"],"q2":v42["q2"],"q3":v42["q3"],"q4":v42["q4"],"q5":v42["q5"],"trueCount":v42["trueCount"],"knownCount":v42["knownCount"],"marketR5":v42["marketR5"],"vol5":v42["vol5"],"prev5":v42["prev5"],"prev20":v42["prev20"],"upside":v42.get("upside"),"return1d":None,"return5d":None,"return20d":None})
    timing_history["entries"] = [e for e in timing_history.get("entries", []) if e.get("date") != today]
    timing_history["entries"].extend(today_timing)
    timing_history["entries"] = sorted(timing_history["entries"], key=lambda e: (e.get("date",""),e.get("code","")))[-10000:]

    # 이후 날짜의 가격이 이미 존재하면 과거 V4-2 판정의 1/5/20일 결과를 자동 채움
    by_code = {}
    for day in history["days"]:
        for row in day.get("stocks", []):
            try: by_code.setdefault(row["code"], []).append((day["date"], float(row["price"])))
            except (KeyError, TypeError, ValueError): pass
    for e in timing_history["entries"]:
        arr = by_code.get(e.get("code"), [])
        dates = [d for d,_ in arr]
        if e.get("date") not in dates: continue
        i = dates.index(e["date"]); base=float(e["price"])
        for key, offset in (("return1d",1),("return5d",5),("return20d",20)):
            if i+offset < len(arr) and base:
                e[key]=round((arr[i+offset][1]/base-1)*100,2)

    with open("data/timing_history.json", "w", encoding="utf-8") as f:
        json.dump(timing_history, f, ensure_ascii=False, indent=2)

    with open("data/score_history.json", "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    # ---- 3) 어제 점수 vs 오늘 실제 수익률 짝짓기 ----
    # 오늘자 코스피 등락률 = 오늘 macro.json의 changeRate (어제 종가 대비 오늘 종가와 동일한 기간이라
    # 종목의 nextDayReturn과 정확히 같은 기간의 "시장 수익률"로 쓸 수 있다)
    market_return = None
    kospi = macro_data.get("kospi")
    if kospi and kospi.get("changeRate") is not None:
        market_return = kospi["changeRate"]

    prev_days = [d for d in history["days"] if d["date"] < today]
    backtest_entries = []
    if prev_days:
        prev_day = prev_days[-1]  # 가장 최근 이전 거래일
        prev_by_code = {s["code"]: s for s in prev_day["stocks"]}

        for s in today_snapshot:
            prev = prev_by_code.get(s["code"])
            if not prev or not prev.get("price"):
                continue
            next_day_return = round((s["price"] / prev["price"] - 1) * 100, 2)
            excess_return = (
                round(next_day_return - market_return, 2) if market_return is not None else None
            )
            backtest_entries.append({
                "date": today,
                "code": s["code"],
                "name": s["name"],
                "prevDate": prev_day["date"],
                "prevScore": prev["score"],
                "nextDayReturn": next_day_return,
                "marketReturn": market_return,
                "excessReturn": excess_return,
            })

    backtest = load_json("data/backtest.json", {"entries": []})
    # 오늘 날짜로 이미 기록된 게 있으면 덮어쓰기
    backtest["entries"] = [e for e in backtest["entries"] if e["date"] != today]
    backtest["entries"].extend(backtest_entries)
    backtest["entries"] = sorted(backtest["entries"], key=lambda e: e["date"])[-3000:]  # 여유 있게 상한

    with open("data/backtest.json", "w", encoding="utf-8") as f:
        json.dump(backtest, f, ensure_ascii=False, indent=2)

    print(f"✅ {today} 스냅샷 {len(today_snapshot)}개 저장, 백테스트 신규 {len(backtest_entries)}개 추가")
    print(f"   누적 거래일수: {len(history['days'])}일 / 목표 100일")


if __name__ == "__main__":
    main()
