"""
전일 스냅샷 검증/보정기

매일 09:10 KST(장 시작 직후)에 실행한다.
- score_history.json에서 가장 최근 거래일을 확인한다.
- 네이버 금융의 '일별시세'에서 그 날짜의 실제 종가를 다시 조회한다.
- 정상인 값은 건드리지 않는다.
- 누락/오차가 있는 값만 보정한다.
- 같은 날짜를 새로 추가하지 않으며, score 자체는 보존한다.
- 가격이 보정되면 V4-2 timing_history와 해당 날짜의 backtest 수익률도 필요한 범위만 갱신한다.

핵심 원칙: 09시 작업은 '두 번째 공식 스냅샷'이 아니라 '전일 데이터 검증/복구'다.
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

from timing import calc_v41, calc_v42

KST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; watchlist-dashboard/1.0)"
}


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_num(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_historical_close(code, target_date, pages=2):
    """target_date(YYYY-MM-DD)의 종가를 정확히 찾아온다.
    09시에는 '현재가'를 사용하면 장중 값이 섞일 수 있으므로 반드시 일별시세의 날짜를 확인한다.
    """
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page={page}"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            table = soup.find("table", class_="type2") or soup.find("table", class_="type_1")
            if table is None:
                continue

            for row in table.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue
                date_text = cols[0].get_text(strip=True)
                if date_text != target_date.replace("-", "."):
                    continue
                close = clean_num(cols[1].get_text(strip=True))
                if close is not None:
                    return close
        except Exception as e:
            print(f"[전일 검증 실패] {code} page={page}: {e}")
        time.sleep(0.25)
    return None


def fetch_kospi_close(target_date):
    for page in range(1, 3):
        url = f"https://finance.naver.com/sise/sise_index_day.naver?code=KOSPI&page={page}"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            table = soup.find("table", class_="type_1")
            if table is None:
                continue
            for row in table.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) != 6:
                    continue
                date_text = cols[0].get_text(strip=True)
                if date_text != target_date.replace("-", "."):
                    continue
                close = clean_num(cols[1].get_text(strip=True))
                if close is not None:
                    return close
        except Exception as e:
            print(f"[KOSPI 전일 검증 실패] page={page}: {e}")
        time.sleep(0.25)
    return None


def update_timing_for_date(history, timing_history, target_date, stocks_by_code):
    price_history = {}
    market_history = []
    for day in history.get("days", []):
        for row in day.get("stocks", []):
            try:
                price_history.setdefault(row["code"], []).append((day["date"], float(row["price"])))
            except (KeyError, TypeError, ValueError):
                pass
        try:
            kp = float(day.get("kospiPrice"))
            if kp > 0:
                market_history.append((day["date"], kp))
        except (TypeError, ValueError):
            pass

    for arr in price_history.values():
        arr.sort(key=lambda x: x[0])
    market_history.sort(key=lambda x: x[0])
    market_prices = [p for _, p in market_history]

    changed = 0
    for entry in timing_history.get("entries", []):
        if entry.get("date") != target_date:
            continue
        code = entry.get("code")
        stock = stocks_by_code.get(code, {})
        arr = [p for _, p in price_history.get(code, [])]
        if not arr:
            continue
        v41 = calc_v41(stock, arr)
        v42 = calc_v42(stock, v41, arr, market_prices)
        entry.update({
            "price": arr[-1],
            "pattern": v41["pattern"],
            "status": v42["status"],
            "highDrop": v41["highDrop"],
            "r5": v41["r5"],
            "r20": v41["r20"],
            "q1": v42["q1"],
            "q2": v42["q2"],
            "q3": v42["q3"],
            "q4": v42["q4"],
            "q5": v42["q5"],
            "trueCount": v42["trueCount"],
            "knownCount": v42["knownCount"],
            "marketR5": v42["marketR5"],
            "vol5": v42["vol5"],
            "prev5": v42["prev5"],
            "prev20": v42["prev20"],
        })
        changed += 1
    return changed


def repair_backtest(backtest, history, target_date):
    """보정된 전일 가격이 이미 생성된 backtest에 영향을 주는 경우만 재계산."""
    by_code = {}
    for day in history.get("days", []):
        for row in day.get("stocks", []):
            try:
                by_code.setdefault(row["code"], []).append((day["date"], float(row["price"])))
            except (KeyError, TypeError, ValueError):
                pass
    for arr in by_code.values():
        arr.sort(key=lambda x: x[0])

    changed = 0
    for entry in backtest.get("entries", []):
        # target_date가 기준일(prevDate)인 경우: 다음 거래일 수익률이 영향을 받음.
        if entry.get("prevDate") != target_date:
            continue
        code = entry.get("code")
        arr = by_code.get(code, [])
        dates = [d for d, _ in arr]
        if target_date not in dates:
            continue
        i = dates.index(target_date)
        if i + 1 >= len(arr):
            continue
        base = arr[i][1]
        if not base:
            continue
        ret = round((arr[i + 1][1] / base - 1) * 100, 2)
        entry["nextDayReturn"] = ret
        if entry.get("marketReturn") is not None:
            entry["excessReturn"] = round(ret - float(entry["marketReturn"]), 2)
        changed += 1
    return changed


def main():
    history = load_json("data/score_history.json", {"days": []})
    if not history.get("days"):
        print("ℹ️ score_history가 없어 검증할 전일 데이터가 없습니다.")
        return

    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    previous_days = [d for d in history["days"] if d.get("date", "") < today]
    if not previous_days:
        print("ℹ️ 오늘 이전 거래일 기록이 없습니다.")
        return
    target_day = previous_days[-1]
    target_date = target_day["date"]

    stocks_data = load_json("data/stocks.json", {"stocks": []})
    stocks_by_code = {s.get("code"): s for s in stocks_data.get("stocks", []) if s.get("code")}

    corrections = []
    checked = 0
    for row in target_day.get("stocks", []):
        code = row.get("code")
        if not code:
            continue
        checked += 1
        actual = fetch_historical_close(code, target_date)
        if actual is None:
            print(f"⚠️ {row.get('name', code)}: {target_date} 종가 조회 실패 → 기존값 유지")
            continue
        old = clean_num(row.get("price"))
        if old is None or abs(actual - old) > 0.0001:
            row["price"] = actual
            corrections.append({"code": code, "name": row.get("name", code), "old": old, "new": actual})
        time.sleep(0.15)

    kospi_actual = fetch_kospi_close(target_date)
    if kospi_actual is not None:
        old_k = clean_num(target_day.get("kospiPrice"))
        if old_k is None or abs(kospi_actual - old_k) > 0.0001:
            target_day["kospiPrice"] = kospi_actual
            corrections.append({"code": "KOSPI", "name": "코스피", "old": old_k, "new": kospi_actual})

    # 날짜/점수는 유지하고 가격만 검증한다. 중복 날짜도 만들지 않는다.
    history["days"] = sorted(history["days"], key=lambda d: d.get("date", ""))[-150:]
    save_json("data/score_history.json", history)

    timing_history = load_json("data/timing_history.json", {"entries": []})
    timing_changed = 0
    backtest_changed = 0
    if corrections:
        timing_changed = update_timing_for_date(history, timing_history, target_date, stocks_by_code)
        save_json("data/timing_history.json", timing_history)

        backtest = load_json("data/backtest.json", {"entries": []})
        backtest_changed = repair_backtest(backtest, history, target_date)
        save_json("data/backtest.json", backtest)

    print(f"✅ 전일 검증 완료: {target_date} / {checked}개 종목 확인")
    if corrections:
        print(f"🔧 보정 {len(corrections)}건 / V4-2 갱신 {timing_changed}건 / 백테스트 갱신 {backtest_changed}건")
        for c in corrections[:20]:
            print(f"   {c['name']}: {c['old']} → {c['new']}")
    else:
        print("✓ 보정할 가격 데이터가 없습니다. 기존 점수/기록을 그대로 유지합니다.")


if __name__ == "__main__":
    main()
