"""
관심종목 실시간 시세 크롤러
V4-4 데이터 안정화: m.stock.naver.com JSON 재무/통합정보 우선 + KRX/pykrx 공매도 보강
- 종목명으로 네이버 검색 API에서 종목코드를 자동으로 찾는다
- 찾은 코드로 네이버 금융 실시간 시세 API를 호출해 현재가/등락률을 가져온다
- 결과를 data/stocks.json 에 저장한다 (GitHub Pages가 이 파일을 읽어서 화면에 그림)
"""

import io
import json
import os
import re
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """네이버 표는 헤더가 2단(예: '외국인' > '순매매량')인 경우가 많아서
    pandas가 컬럼명을 ('외국인', '순매매량') 같은 튜플(MultiIndex)로 만든다.
    이걸 '외국인순매매량' 같은 일반 문자열 한 줄로 합쳐서, 이후 문자열 매칭이 실제로 먹히게 한다."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            "".join(str(level) for level in tup if str(level) and "Unnamed" not in str(level))
            for tup in df.columns
        ]
    return df

# ----------------------------------------------------------------------
# 1) 감시할 종목 목록 (이름만 적으면 코드는 자동으로 찾음)
#    이름이 애매해서 자동 검색이 틀릴 것 같으면 CODE_OVERRIDES 에 직접 코드를 적어준다.
# ----------------------------------------------------------------------
# 종목명: 섹터 (카드 그리드를 섹터별로 묶어서 보여주기 위한 분류)
WATCHLIST = {
    "삼성전자": "반도체/전자",
    "삼성전자우": "반도체/전자",
    "SK하이닉스": "반도체/전자",
    "삼성SDI": "2차전지",
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
    "SFA넥셀": "에너지",

    "한농화성": "태양광/화학",

    "태광": "기타",
    "아이쓰리시스템": "기타",
    "포스코엠텍": "기타",
    "티엘비": "기타",

    "LG에너지솔루션": "2차전지",
    "에코프로": "2차전지",
    "포스코퓨처엠": "2차전지",

    "KB금융": "금융",
    "신한지주": "금융",
    "하나금융지주": "금융",

    "현대차": "자동차",
    "기아": "자동차",
    "현대모비스": "자동차",
}

# 자동 검색이 실패하거나 엉뚱한 종목을 찾아올 경우를 대비한 수동 지정
# (2026-07-15 기준 직접 확인한 코드로 전체 채워둠. 종목을 새로 추가할 때는
#  이름만 WATCHLIST에 넣어도 되지만, 안 잡히면 여기에 코드를 추가해주면 된다)
CODE_OVERRIDES = {
    "삼성전자": "005930",
    "삼성전자우": "005935",
    "SK하이닉스": "000660",
    "삼성SDI": "006400",
    "TIGER 코리아AI전기전자": "0117V0",  # 정식명: TIGER 코리아AI전력기기TOP3플러스
    "KODEX AI반도체": "395160",  # 정식명: KODEX AI반도체TOP2플러스
    "LIG디펜스앤에어로스페이스": "079550",
    "HD건설기계": "267270",
    "HD현대마린솔루션": "443060",
    "OCI홀딩스": "010060",
    "OCI": "456040",
    "효성티앤씨": "298020",
    "LS": "006260",
    "삼성E&A": "028050",
    "에이피알": "278470",
    "한국콜마": "161890",
    "에스티팜": "237690",
    "감성코퍼레이션": "036620",
    "두산퓨얼셀": "336260",
    "두산에너빌리티": "034020",
    "태광": "023160",
    "아이쓰리시스템": "214430",
    "티엘비": "356860",
    "SFA넥셀": "222080",
    "한농화성": "011500",
    "포스코엠텍": "009520",
    "LG에너지솔루션": "373220",
    "에코프로": "086520",
    "포스코퓨처엠": "003670",
    "KB금융": "105560",
    "신한지주": "055550",
    "하나금융지주": "086790",
    "현대차": "005380",
    "기아": "000270",
    "현대모비스": "012330",
}

SEARCH_URL = "https://ac.stock.naver.com/ac"
PRICE_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "close",
}


def make_session() -> requests.Session:
    """네이버/KRX 요청용 재시도 세션. 일시적인 EOF/429/5xx에 자동 재시도한다."""
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


SESSION = make_session()


def find_code(name: str) -> str | None:
    """네이버 종목 검색 자동완성 API로 종목명 -> 종목코드를 찾는다."""
    if name in CODE_OVERRIDES:
        return CODE_OVERRIDES[name]

    params = {"q": name, "target": "stock,fund"}
    try:
        res = SESSION.get(SEARCH_URL, params=params, timeout=10)
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
        res = SESSION.get(PRICE_URL.format(code=code), timeout=10)
        res.raise_for_status()
        data = res.json()
        info = data["datas"][0]

        def to_num(v):
            """'280,750' 같은 콤마 포함 문자열도 숫자로 안전하게 변환"""
            if v is None:
                return None
            try:
                return float(str(v).replace(",", ""))
            except ValueError:
                return None

        return {
            "price": to_num(info.get("closePrice")),
            "change": to_num(info.get("compareToPreviousClosePrice")),
            "changeRate": info.get("fluctuationsRatio"),
            "riseFall": info.get("compareToPreviousPrice", {}).get("text"),  # 상승/하락/보합
        }
    except Exception as e:
        print(f"[시세 실패] {code}: {e}")
        return None


def _clean_number(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _find_key_recursive(obj, keys):
    """API 응답 구조가 조금 바뀌어도 후보 key를 재귀적으로 찾는다."""
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj[key] not in (None, ""):
                return obj[key]
        for value in obj.values():
            found = _find_key_recursive(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key_recursive(item, keys)
            if found not in (None, ""):
                return found
    return None


def fetch_naver_integration(code: str) -> dict:
    """현재 네이버 모바일 공개 JSON API에서 52주/컨센서스/투자자 흐름을 가져온다.

    기존 finance.naver.com HTML/테이블 방식 대신 m.stock.naver.com의 integration을 우선 사용한다.
    """
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    try:
        res = SESSION.get(url, timeout=15)
        res.raise_for_status()
        data = res.json()
        infos = data.get("totalInfos", []) if isinstance(data, dict) else []

        def info_value(keys):
            for row in infos:
                if not isinstance(row, dict):
                    continue
                if row.get("code") in keys or row.get("key") in keys:
                    return row.get("value")
            return _find_key_recursive(infos, keys)

        high = _clean_number(info_value({"highPriceOf52Weeks", "52주최고"}))
        low = _clean_number(info_value({"lowPriceOf52Weeks", "52주최저"}))

        consensus = data.get("consensusInfo") or {}
        target = _clean_number(_find_key_recursive(consensus, {"priceTargetMean", "targetPriceMean", "priceTarget"}))
        recomm = _clean_number(_find_key_recursive(consensus, {"recommMean", "recommendationMean"}))
        opinion = None
        if recomm is not None:
            # 네이버 컨센서스 평균의견은 통상 1~5 척도다. 기존 화면/점수의 문자열 체계로 변환한다.
            if recomm >= 4.0:
                opinion = "매수"
            elif recomm >= 3.0:
                opinion = "중립"
            else:
                opinion = "매도"

        trends = data.get("dealTrendInfos") or []
        latest = trends[0] if isinstance(trends, list) and trends else {}
        foreign = _clean_number(_find_key_recursive(latest, {"foreignerPureBuyQuant", "foreignPureBuyQuant", "foreignNet"}))
        inst = _clean_number(_find_key_recursive(latest, {"organPureBuyQuant", "organizationPureBuyQuant", "instNet"}))
        indiv = _clean_number(_find_key_recursive(latest, {"individualPureBuyQuant", "individualNet", "indivNet"}))
        trend_date = _find_key_recursive(latest, {"localDate", "date", "tradeDate", "localTradedAt"})

        return {
            "week52High": int(high) if high is not None else None,
            "week52Low": int(low) if low is not None else None,
            "targetPrice": int(target) if target is not None else None,
            "opinion": opinion,
            "foreignNet": int(foreign) if foreign is not None else None,
            "instNet": int(inst) if inst is not None else None,
            "indivNet": int(indiv) if indiv is not None else None,
            "investorTrendDate": trend_date or None,
            "integrationSource": "m.stock.naver.com/integration",
        }
    except Exception as e:
        print(f"[네이버 통합정보 실패] {code}: {e}")
        return {
            "week52High": None, "week52Low": None, "targetPrice": None, "opinion": None,
            "foreignNet": None, "instNet": None, "indivNet": None,
            "investorTrendDate": None, "integrationSource": None,
        }


def fetch_extra(code: str) -> dict:
    """52주 범위/목표가/의견. 최신 JSON API를 우선하고 legacy HTML을 보조로 사용한다."""
    current = fetch_naver_integration(code)
    if current.get("week52High") or current.get("week52Low") or current.get("targetPrice") or current.get("opinion"):
        return {k: current.get(k) for k in ("week52High", "week52Low", "targetPrice", "opinion")}

    # 구형 HTML fallback
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        res = SESSION.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        def num_after(label: str):
            m = re.search(rf"{label}[^\d]{{0,10}}([\d,]{{4,}})", page_text)
            return int(m.group(1).replace(",", "")) if m else None

        high = num_after("52주최고")
        low = num_after("52주최저")
        opinion_match = re.search(r"투자의견\s*(강력매수|매수|중립|매도|강력매도)", page_text)
        return {"week52High": high, "week52Low": low, "targetPrice": num_after("목표주가"),
                "opinion": opinion_match.group(1) if opinion_match else None}
    except Exception as e:
        print(f"[추가정보 실패] {code}: {e}")
        return {"week52High": None, "week52Low": None, "targetPrice": None, "opinion": None}


def fetch_financials(code: str) -> dict:
    """네이버 모바일 공개 재무 JSON에서 최신 실제 연간 재무값을 읽어 ROE/부채비율을 계산한다.

    ROE = 당기순이익 / 자본총계 * 100
    부채비율 = 부채총계 / 자본총계 * 100
    컨센서스(Y) 열은 제외하고 실제 재무기간(N) 중 가장 최근 값을 사용한다.
    """
    url = f"https://m.stock.naver.com/api/stock/{code}/finance/annual"
    try:
        res = SESSION.get(url, timeout=15)
        res.raise_for_status()
        data = res.json()
        fin = data.get("financeInfo", {}) if isinstance(data, dict) else {}
        periods = fin.get("trTitleList", [])
        rows = fin.get("rowList", [])
        actual_keys = [str(p.get("key")) for p in periods if str(p.get("isConsensus", "N")).upper() != "Y"]
        if not actual_keys:
            actual_keys = [str(p.get("key")) for p in periods]
        actual_keys = [k for k in actual_keys if k and k != "None"]
        if not actual_keys:
            raise ValueError("실제 재무기간 없음")
        latest_key = actual_keys[-1]

        def row_value(primary_titles, fallback_titles=()):
            ordered = list(primary_titles) + list(fallback_titles)
            for wanted in ordered:
                for row in rows:
                    title = str(row.get("title", ""))
                    if wanted not in title:
                        continue
                    cols = row.get("columns", {}) or {}
                    for key in reversed(actual_keys):
                        value = _clean_number((cols.get(key) or {}).get("value") if isinstance(cols.get(key), dict) else cols.get(key))
                        if value is not None:
                            return value, key, title
            return None, None, None

        equity, equity_key, equity_title = row_value(["자본총계"], ["자본"])
        debt, debt_key, debt_title = row_value(["부채총계"], ["부채"])
        net_income, income_key, income_title = row_value(["당기순이익"], ["순이익"])
        if equity is None:
            raise ValueError("자본총계 없음")

        debt_ratio = (debt / equity * 100) if debt is not None and equity else None
        roe = (net_income / equity * 100) if net_income is not None and equity else None
        return {
            "debtRatio": round(debt_ratio, 2) if debt_ratio is not None else None,
            "roe": round(roe, 2) if roe is not None else None,
            "financialPeriod": latest_key,
            "financialSource": "m.stock.naver.com/finance/annual",
        }
    except Exception as e:
        print(f"[재무제표 실패] {code}: {e}")
        return {"debtRatio": None, "roe": None, "financialPeriod": None, "financialSource": None}

def fetch_volume_surge(code: str) -> dict:
    """오늘 거래량을 최근 20일 평균 거래량과 비교해 배율을 계산하고,
    최근 20일 저가 중 최솟값을 '지지선' 참고값으로 함께 계산한다.
    페이지의 '일별시세' 표를 컬럼명(거래량/저가) 기준으로 찾기 때문에 표 위치가 바뀌어도 잘 버틴다."""
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page=1"
    try:
        res = SESSION.get(url, timeout=10)
        res.raise_for_status()
        tables = [flatten_columns(t) for t in pd.read_html(io.StringIO(res.text))]
        df = next((t for t in tables if "거래량" in t.columns), None)
        if df is None:
            raise ValueError("거래량 표를 못 찾음")

        def to_series(col):
            return (
                df[col].astype(str).str.replace(",", "", regex=False)
                .pipe(pd.to_numeric, errors="coerce").dropna()
            )

        volumes = to_series("거래량")
        if volumes.empty:
            raise ValueError("거래량 데이터 없음")

        today_volume = int(volumes.iloc[0])
        avg20 = volumes.iloc[1:21].mean() if len(volumes) > 1 else None
        ratio = round(today_volume / avg20, 2) if avg20 else None

        support_line = None
        if "저가" in df.columns:
            lows = to_series("저가").iloc[:20]
            if not lows.empty:
                support_line = int(lows.min())

        return {
            "volume": today_volume,
            "avgVolume20": int(avg20) if avg20 else None,
            "volumeRatio": ratio,
            "supportLine": support_line,
        }
    except Exception as e:
        print(f"[거래량 실패] {code}: {e}")
        return {"volume": None, "avgVolume20": None, "volumeRatio": None, "supportLine": None}


def fetch_foreign_institution(code: str) -> dict:
    """외국인/기관 최근 순매매를 최신 integration JSON에서 가져온다.
    실패하면 legacy HTML을 보조로 시도한다."""
    current = fetch_naver_integration(code)
    if current.get("foreignNet") is not None or current.get("instNet") is not None:
        return {k: current.get(k) for k in ("foreignNet", "instNet", "indivNet", "investorTrendDate")}

    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    try:
        res = SESSION.get(url, timeout=10)
        res.raise_for_status()
        tables = [flatten_columns(t) for t in pd.read_html(io.StringIO(res.text))]
        for t in tables:
            cols = [str(c) for c in t.columns]
            f_col = next((c for c in cols if "외국인" in c and "순매매" in c), None)
            i_col = next((c for c in cols if "기관" in c and "순매매" in c), None)
            if f_col and i_col:
                row = t.dropna(subset=[f_col]).iloc[0]
                def clean(v):
                    try: return int(str(v).replace(",", ""))
                    except (ValueError, TypeError): return None
                f, i = clean(row[f_col]), clean(row[i_col])
                return {"foreignNet": f, "instNet": i,
                        "indivNet": -(f+i) if f is not None and i is not None else None,
                        "investorTrendDate": str(row.iloc[0]) if len(row) else None}
        raise ValueError("외국인/기관 표를 못 찾음")
    except Exception as e:
        print(f"[수급 실패] {code}: {e}")
        return {"foreignNet": None, "instNet": None, "indivNet": None, "investorTrendDate": None}


def _short_ratio_from_df(df, code):
    if df is None or df.empty:
        return None
    try:
        if code in df.index:
            row = df.loc[code]
        else:
            return None
        if hasattr(row, "iloc") and not isinstance(row, (str, bytes)):
            # duplicate index/row handling
            if getattr(row, "ndim", 1) > 1:
                row = row.iloc[-1]
        for col in ("비중", "shortRatio", "공매도비중"):
            if col in getattr(row, "index", []):
                val = _clean_number(row[col])
                if val is not None:
                    return float(val)
    except Exception:
        return None
    return None


def fetch_short_selling(code: str) -> dict:
    """KRX 공매도 잔고비율.

    1) pykrx의 '전종목 잔고'를 최근 거래가능일에 시장별로 조회해 빠르게 찾는다.
    2) 실패하면 기존 종목별 기간조회로 재시도한다.
    공매도 잔고는 KRX 제공 지연(T+2) 특성상 오늘 값이 없을 수 있다.
    """
    try:
        from pykrx import stock as pykrx_stock
    except Exception as e:
        print(f"[공매도 라이브러리 실패] {code}: {e}")
        return {"shortSellingRatio": None, "shortSellingDate": None}

    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).date()
    last_error = None
    # T+2를 고려해 최근 12일 중 평일을 후보로 잡는다.
    candidates = []
    for n in range(2, 15):
        d = today - timedelta(days=n)
        if d.weekday() < 5:
            candidates.append(d.strftime("%Y%m%d"))

    # 시장별 전종목 조회: 종목별 35회 호출 대신 날짜당 2회만 호출
    for date_str in candidates:
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = pykrx_stock.get_shorting_balance_by_ticker(date_str, market)
                ratio = _short_ratio_from_df(df, code)
                if ratio is not None:
                    return {"shortSellingRatio": ratio, "shortSellingDate": date_str}
            except Exception as e:
                last_error = e

    # 최종 fallback: 종목별 기간 조회
    from_date = (today - timedelta(days=20)).strftime("%Y%m%d")
    to_date = today.strftime("%Y%m%d")
    for attempt in range(1, 4):
        try:
            df = pykrx_stock.get_shorting_balance_by_date(from_date, to_date, code)
            if df is not None and not df.empty and "비중" in df.columns:
                series = pd.to_numeric(df["비중"], errors="coerce").dropna()
                if not series.empty:
                    date_value = str(series.index[-1])
                    return {"shortSellingRatio": float(series.iloc[-1]), "shortSellingDate": date_value}
        except Exception as e:
            last_error = e
            if attempt < 3:
                time.sleep(1.5 * attempt)
    print(f"[공매도 실패] {code}: {last_error}")
    return {"shortSellingRatio": None, "shortSellingDate": None}

def estimate_next_earnings() -> str:
    """상장사 분기보고서 법정 제출기한 근사치를 기준으로 다음 실적발표 예상일을 추정한다.
    종목마다 실제 발표일은 다를 수 있어 시장 전체 공통 참고용 날짜다."""
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).date()
    year = today.year
    candidates = [
        datetime(year, 5, 15, tzinfo=kst).date(),
        datetime(year, 8, 14, tzinfo=kst).date(),
        datetime(year, 11, 14, tzinfo=kst).date(),
        datetime(year + 1, 2, 15, tzinfo=kst).date(),
    ]
    upcoming = next((d for d in candidates if d >= today), candidates[-1])
    return upcoming.isoformat()


def fetch_naver_index(code: str, label: str) -> dict:
    """네이버 지수 일별시세 표에서 최근 2개 거래일의 가격을 가져와 직접 등락을 계산한다.
    (표 형식: 날짜/체결가/전일비/등락률/거래량/거래대금, 6칸짜리 행만 데이터로 인정)
    전일비/등락률 컬럼은 상승·하락 아이콘으로 부호를 표시해서 텍스트 파싱이 불안정하기 때문에,
    가격 두 값의 차이로 직접 계산해 부호 불일치를 원천적으로 막는다."""
    url = f"https://finance.naver.com/sise/sise_index_day.naver?code={code}&page=1"
    try:
        res = SESSION.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        table = soup.find("table", class_="type_1")
        if table is None:
            raise ValueError("표를 못 찾음")

        def clean(v):
            v = v.replace(",", "").replace("%", "").strip()
            return float(v) if v else None

        prices = []
        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) != 6:
                continue  # 헤더/빈 행 건너뜀
            price = clean(cols[1].get_text())
            if price is not None:
                prices.append(price)
            if len(prices) >= 2:
                break

        if len(prices) < 2:
            raise ValueError("최근 2개 거래일 데이터를 못 모음")

        today, prev = prices[0], prices[1]
        diff = today - prev
        rate = round(diff / prev * 100, 2) if prev else None

        return {"price": today, "change": round(diff, 2), "changeRate": rate}
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


def fetch_treasury_yield_10y() -> dict:
    """미국 10년물 국채금리. 야후 티커 ^TNX가 실제 금리의 10배로 오는 경우와
    이미 실제 %로 오는 경우가 둘 다 있어서, 결과값이 국채금리로는 비정상적으로 낮으면(1% 미만)
    10배 보정을 하지 않은 원래 값을 그대로 쓴다."""
    q = fetch_yf_quote("^TNX", "미국10년물")
    if q["price"] is None:
        return q

    corrected_price = round(q["price"] / 10, 2)
    if corrected_price < 1:
        # 10으로 나눈 값이 1% 미만이면 보정이 오히려 잘못된 것 → 원래 값을 그대로 사용
        return q

    return {
        "price": corrected_price,
        "change": round(q["change"] / 10, 3) if q["change"] is not None else None,
        "changeRate": q["changeRate"],  # 비율(%)은 10배 보정해도 동일
    }


def fetch_macro() -> dict:
    """코스피/코스닥 + 원달러 환율/나스닥/필라델피아반도체지수(SOX)/VIX/미국10년물을 모아온다."""
    import time as _time

    result = {
        "kospi": fetch_naver_index("KOSPI", "코스피"),
        "kosdaq": fetch_naver_index("KOSDAQ", "코스닥"),
        "usdkrw": fetch_yf_quote("KRW=X", "원/달러"),
    }
    _time.sleep(1)
    result["nasdaq"] = fetch_yf_quote("^IXIC", "나스닥")
    _time.sleep(1)
    result["sox"] = fetch_yf_quote("^SOX", "필라델피아반도체지수")
    _time.sleep(1)
    result["vix"] = fetch_yf_quote("^VIX", "VIX")
    _time.sleep(1)
    result["us10y"] = fetch_treasury_yield_10y()
    return result


def sanitize_for_json(obj):
    """dict/list를 재귀적으로 훑어서 NaN, Infinity 같은 비표준 JSON 값을 None으로 바꾼다.
    (파이썬의 json.dump는 NaN을 그대로 써버려서 브라우저 JSON.parse가 깨지는 문제를 막기 위함)"""
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    return obj


def main():
    os.makedirs("data", exist_ok=True)

    # V4-4부터는 이전 버전의 선택 데이터를 새 점수에 재사용하지 않는다.
    # 과거 기록은 archive에 보관하고, 새 Day 1은 실제 새 수집값만 사용한다.
    old_map = {}

    results = []
    stats = {
        "price": 0, "extra": 0, "volume": 0, "flow": 0,
        "financial": 0, "short": 0, "preserved": 0,
    }


    for name, sector in WATCHLIST.items():
        code = find_code(name)
        if not code:
            print(f"⚠️  코드 못 찾음: {name}")
            continue

        old = {}
        price_info = fetch_price(code)
        if not price_info:
            # 가격 자체가 실패하면 기존 종목 전체를 보존한다.
            if old:
                preserved = dict(old)
                preserved["name"] = name
                preserved["sector"] = sector
                results.append(preserved)
                stats["preserved"] += 1
                print(f"[가격 실패→기존값 보존] {name}({code})")
            continue
        stats["price"] += 1

        extra_info = fetch_extra(code)
        time.sleep(0.2)
        volume_info = fetch_volume_surge(code)
        time.sleep(0.2)
        flow_info = fetch_foreign_institution(code)
        time.sleep(0.2)
        financial_info = fetch_financials(code)
        time.sleep(0.2)
        short_info = fetch_short_selling(code)

        if any(v is not None for v in extra_info.values()): stats["extra"] += 1
        if any(v is not None for v in volume_info.values()): stats["volume"] += 1
        if any(v is not None for v in flow_info.values()): stats["flow"] += 1
        if financial_info.get("debtRatio") is not None or financial_info.get("roe") is not None: stats["financial"] += 1
        if short_info.get("shortSellingRatio") is not None: stats["short"] += 1

        results.append({
            "name": name,
            "code": code,
            "sector": sector,
            **price_info,
            **extra_info,
            **volume_info,
            **flow_info,
            **financial_info,
            **short_info,
        })
        time.sleep(0.6)

    kst = timezone(timedelta(hours=9))
    output = {
        "updatedAt": datetime.now(kst).isoformat(),
        "stocks": results,
    }

    with open("data/stocks.json", "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(output), f, ensure_ascii=False, indent=2)

    print(f"✅ {len(results)}개 종목 저장 완료")
    print(
        "📊 수집 성공 현황: "
        f"시세 {stats['price']}/{len(results)}, "
        f"추가정보 {stats['extra']}/{len(results)}, "
        f"거래량 {stats['volume']}/{len(results)}, "
        f"수급 {stats['flow']}/{len(results)}, "
        f"재무 {stats['financial']}/{len(results)}, "
        f"공매도 {stats['short']}/{len(results)}, "
        f"기존값 보존 {stats['preserved']}"
    )

    macro_output = {
        "updatedAt": datetime.now(kst).isoformat(),
        "nextEarningsEstimate": estimate_next_earnings(),
        **fetch_macro(),
    }
    with open("data/macro.json", "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(macro_output), f, ensure_ascii=False, indent=2)

    print("✅ 매크로 지표 저장 완료")


if __name__ == "__main__":
    main()
