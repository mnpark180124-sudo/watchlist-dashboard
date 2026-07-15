"""
관심종목 실시간 시세 크롤러
- 종목명으로 네이버 검색 API에서 종목코드를 자동으로 찾는다
- 찾은 코드로 네이버 금융 실시간 시세 API를 호출해 현재가/등락률을 가져온다
- 결과를 data/stocks.json 에 저장한다 (GitHub Pages가 이 파일을 읽어서 화면에 그림)
"""

import json
import re
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# 1) 감시할 종목 목록 (이름만 적으면 코드는 자동으로 찾음)
#    이름이 애매해서 자동 검색이 틀릴 것 같으면 CODE_OVERRIDES 에 직접 코드를 적어준다.
# ----------------------------------------------------------------------
# 종목명: 섹터 (카드 그리드를 섹터별로 묶어서 보여주기 위한 분류)
WATCHLIST = {
    "삼성전자": "반도체/전자",
    "삼성전자우": "반도체/전자",
    "SK하이닉스": "반도체/전자",
    "삼성SDI": "반도체/전자",
    "TIGER 코리아AI전기전자": "반도체/전자",
    "KODEX AI반도체": "반도체/전자",

    "LIG디펜스앤에어로스페이스": "방산/조선/기계",
    "HD건설기계": "방산/조선/기계",
    "HD현대마린솔루션": "방산/조선/기계",

    "OCI홀딩스": "태양광/화학",
    "OCI": "태양광/화학",
    "효성티앤씨": "태양광/화학",

    "LS": "전력/인프라",
    "삼성E&A": "전력/인프라",

    "에이피알": "바이오/뷰티",
    "한국콜마": "바이오/뷰티",
    "에스티팜": "바이오/뷰티",
    "감성코퍼레이션": "바이오/뷰티",

    "두산퓨얼셀": "에너지",
    "두산에너빌리티": "에너지",

    "태광": "기타",
    "아이쓰리시스템": "기타",
    "티엘비": "기타",
}

# 자동 검색이 엉뚱한 종목을 찾아올 경우를 대비한 수동 지정 (필요할 때만 채우기)
# 예: "삼성전자우": "005935"
CODE_OVERRIDES = {
    "삼성전자": "005930",
    "삼성전자우": "005935",
    "SK하이닉스": "000660",
    "삼성SDI": "006400",
}

SEARCH_URL = "https://ac.stock.naver.com/ac"
PRICE_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; watchlist-dashboard/1.0)"
}


def find_code(name: str) -> str | None:
    """네이버 종목 검색 자동완성 API로 종목명 -> 종목코드를 찾는다."""
    if name in CODE_OVERRIDES:
        return CODE_OVERRIDES[name]

    params = {"q": name, "target": "stock,fund"}
    try:
        res = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=5)
        res.raise_for_status()
        data = res.json()
        items = data.get("items", [])
        for group in items:
            for item in group.get("items", []):
                # 이름이 정확히 일치하는 항목을 우선 채택
                if item.get("name") == name:
                    return item.get("code")
        # 정확히 일치하는 게 없으면 첫 번째 후보 사용
        for group in items:
            for item in group.get("items", []):
                return item.get("code")
    except Exception as e:
        print(f"[검색 실패] {name}: {e}")
    return None


def fetch_price(code: str) -> dict | None:
    """종목코드로 실시간 현재가/등락률을 가져온다."""
    try:
        res = requests.get(PRICE_URL.format(code=code), headers=HEADERS, timeout=5)
        res.raise_for_status()
        data = res.json()
        info = data["datas"][0]
        return {
            "price": info.get("closePrice"),
            "change": info.get("compareToPreviousClosePrice"),
            "changeRate": info.get("fluctuationsRatio"),
            "riseFall": info.get("compareToPreviousPrice", {}).get("text"),  # 상승/하락/보합
        }
    except Exception as e:
        print(f"[시세 실패] {code}: {e}")
        return None


def fetch_extra(code: str) -> dict:
    """52주 최고/최저, 증권사 목표주가, 투자의견을 가져온다.
    DOM id 대신 텍스트 라벨을 기준으로 찾아서, 페이지 구조가 조금 바뀌어도 덜 깨지게 했다.
    못 찾으면 None으로 채워서 점수 계산 쪽에서 해당 항목만 건너뛰도록 한다."""
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        def num_after(label: str):
            m = re.search(rf"{label}\s*([\d,]+)", page_text)
            return int(m.group(1).replace(",", "")) if m else None

        opinion_match = re.search(r"투자의견\s*(강력매수|매수|중립|매도|강력매도)", page_text)

        return {
            "week52High": num_after("52주최고"),
            "week52Low": num_after("52주최저"),
            "targetPrice": num_after("목표주가"),
            "opinion": opinion_match.group(1) if opinion_match else None,
        }
    except Exception as e:
        print(f"[추가정보 실패] {code}: {e}")
        return {"week52High": None, "week52Low": None, "targetPrice": None, "opinion": None}


def fetch_volume_surge(code: str) -> dict:
    """오늘 거래량을 최근 20일 평균 거래량과 비교해 배율을 계산한다.
    페이지의 '일별시세' 표를 컬럼명(거래량) 기준으로 찾기 때문에 표 위치가 바뀌어도 잘 버틴다."""
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page=1"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        res.raise_for_status()
        tables = pd.read_html(res.text)
        df = next((t for t in tables if "거래량" in t.columns), None)
        if df is None:
            raise ValueError("거래량 표를 못 찾음")

        volumes = (
            df["거래량"].astype(str).str.replace(",", "", regex=False)
            .pipe(pd.to_numeric, errors="coerce").dropna()
        )
        if volumes.empty:
            raise ValueError("거래량 데이터 없음")

        today_volume = int(volumes.iloc[0])
        avg20 = volumes.iloc[1:21].mean() if len(volumes) > 1 else None
        ratio = round(today_volume / avg20, 2) if avg20 else None

        return {
            "volume": today_volume,
            "avgVolume20": int(avg20) if avg20 else None,
            "volumeRatio": ratio,
        }
    except Exception as e:
        print(f"[거래량 실패] {code}: {e}")
        return {"volume": None, "avgVolume20": None, "volumeRatio": None}


def fetch_foreign_institution(code: str) -> dict:
    """외국인/기관 순매매 동향(가장 최근 거래일)을 가져온다."""
    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        res.raise_for_status()
        tables = pd.read_html(res.text)

        target = None
        foreign_col = inst_col = None
        for t in tables:
            cols = [str(c) for c in t.columns]
            f_col = next((c for c in cols if "외국인" in c and "순매매" in c), None)
            i_col = next((c for c in cols if "기관" in c and "순매매" in c), None)
            if f_col and i_col:
                target, foreign_col, inst_col = t, f_col, i_col
                break

        if target is None:
            raise ValueError("외국인/기관 표를 못 찾음")

        row = target.dropna(subset=[foreign_col]).iloc[0]

        def clean(v):
            try:
                return int(str(v).replace(",", ""))
            except (ValueError, TypeError):
                return None

        return {"foreignNet": clean(row[foreign_col]), "instNet": clean(row[inst_col])}
    except Exception as e:
        print(f"[수급 실패] {code}: {e}")
        return {"foreignNet": None, "instNet": None}


def fetch_naver_index(code: str, label: str) -> dict:
    """네이버 국내 지수 페이지의 og:description 메타태그에서 현재가/등락 정보를 뽑는다.
    (지수 페이지는 보통 og:description에 '코스피 3,412.55 -12.34 -0.36%' 식 요약이 들어있다)"""
    url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        og = soup.find("meta", attrs={"property": "og:description"})
        desc = og["content"] if og and og.get("content") else ""

        nums = re.findall(r"-?[\d,]+\.\d+", desc)
        nums = [float(n.replace(",", "")) for n in nums]

        return {
            "price": nums[0] if len(nums) > 0 else None,
            "change": nums[1] if len(nums) > 1 else None,
            "changeRate": nums[2] if len(nums) > 2 else None,
        }
    except Exception as e:
        print(f"[지수 실패] {label}({code}): {e}")
        return {"price": None, "change": None, "changeRate": None}


def fetch_yf_quote(ticker: str, label: str) -> dict:
    """야후 파이낸스로 해외 지수/환율 전일 대비 등락을 계산한다."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        if len(hist) < 2:
            raise ValueError("데이터 부족")
        prev_close = float(hist["Close"].iloc[-2])
        last_close = float(hist["Close"].iloc[-1])
        change = last_close - prev_close
        rate = (change / prev_close) * 100
        return {"price": round(last_close, 2), "change": round(change, 2), "changeRate": round(rate, 2)}
    except Exception as e:
        print(f"[해외지표 실패] {label}({ticker}): {e}")
        return {"price": None, "change": None, "changeRate": None}


def fetch_macro() -> dict:
    """코스피/코스닥/VKOSPI + 원달러 환율/나스닥/필라델피아반도체지수(SOX)를 모아온다."""
    return {
        "kospi": fetch_naver_index("KOSPI", "코스피"),
        "kosdaq": fetch_naver_index("KOSDAQ", "코스닥"),
        # VKOSPI는 네이버 지수 코드가 바뀌었을 수 있어 실패하면 None으로 채워짐 (README 참고)
        "vkospi": fetch_naver_index("VKOSPI", "VKOSPI"),
        "usdkrw": fetch_yf_quote("KRW=X", "원/달러"),
        "nasdaq": fetch_yf_quote("^IXIC", "나스닥"),
        "sox": fetch_yf_quote("^SOX", "필라델피아반도체지수"),
    }


def main():
    results = []
    for name, sector in WATCHLIST.items():
        code = find_code(name)
        if not code:
            print(f"⚠️  코드 못 찾음: {name}")
            continue

        price_info = fetch_price(code)
        if not price_info:
            continue

        extra_info = fetch_extra(code)
        volume_info = fetch_volume_surge(code)
        flow_info = fetch_foreign_institution(code)

        results.append({
            "name": name,
            "code": code,
            "sector": sector,
            **price_info,
            **extra_info,
            **volume_info,
            **flow_info,
        })
        time.sleep(0.3)  # 페이지 여러 개 긁으니 요청 간격 살짝 늘림

    kst = timezone(timedelta(hours=9))
    output = {
        "updatedAt": datetime.now(kst).isoformat(),
        "stocks": results,
    }

    with open("data/stocks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ {len(results)}개 종목 저장 완료")

    macro_output = {
        "updatedAt": datetime.now(kst).isoformat(),
        **fetch_macro(),
    }
    with open("data/macro.json", "w", encoding="utf-8") as f:
        json.dump(macro_output, f, ensure_ascii=False, indent=2)

    print("✅ 매크로 지표 저장 완료")


if __name__ == "__main__":
    main()
