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

    # 오늘 이미 기록된 날짜면(같은 날 여러 번 실행) 덮어쓰기, 아니면 새로 추가
    history["days"] = [d for d in history["days"] if d["date"] != today]
    history["days"].append({"date": today, "stocks": today_snapshot})
    # 너무 오래된 기록까지 무한정 쌓이지 않도록 최근 150일치만 유지
    history["days"] = sorted(history["days"], key=lambda d: d["date"])[-150:]

    with open("data/score_history.json", "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    # ---- 3) 어제 점수 vs 오늘 실제 수익률 짝짓기 ----
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
            backtest_entries.append({
                "date": today,
                "code": s["code"],
                "name": s["name"],
                "prevDate": prev_day["date"],
                "prevScore": prev["score"],
                "nextDayReturn": next_day_return,
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
