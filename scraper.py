"""
관심종목 실시간 시세 크롤러
V4-4.5.1 데이터 보존 패치: V4-4.5 KOSPI/KOSDAQ 수집 보강 유지 + 누적 data 파일 비포함
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
        roe = _clean_number(info_value({"roe", "ROE", "returnOnEquity"}))

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
        trend_date = _find_key_recursive(latest, {"bizdate", "localDate", "date", "tradeDate", "localTradedAt"})

        return {
            "week52High": int(high) if high is not None else None,
            "week52Low": int(low) if low is not None else None,
            "targetPrice": int(target) if target is not None else None,
            "opinion": opinion,
            "foreignNet": int(foreign) if foreign is not None else None,
            "instNet": int(inst) if inst is not None else None,
            "indivNet": int(indiv) if indiv is not None else None,
            "investorTrendDate": trend_date or None,
            "roe": roe,
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


def _norm_label(value) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


def _extract_finance_payload(data: dict) -> tuple[list, list]:
    """네이버 finance/annual 응답의 작은 구조 변화에 대응해 기간/행 목록을 추출한다."""
    fin = data.get("financeInfo", {}) if isinstance(data, dict) else {}
    if not isinstance(fin, dict):
        fin = {}
    periods = fin.get("trTitleList") or fin.get("titleList") or fin.get("periods") or data.get("trTitleList") or []
    rows = fin.get("rowList") or fin.get("rows") or data.get("rowList") or []
    return periods if isinstance(periods, list) else [], rows if isinstance(rows, list) else []


def _finance_period_keys(periods: list) -> list:
    out=[]
    for p in periods:
        if not isinstance(p, dict):
            continue
        key = p.get("key") or p.get("period") or p.get("date")
        if key is None:
            continue
        if str(p.get("isConsensus", "N")).upper() == "Y" or str(p.get("isForecast", "N")).upper() in ("Y", "TRUE"):
            continue
        out.append(str(key))
    return out


def _finance_row_values(row: dict, actual_keys: list) -> dict:
    """재무 행의 값 구조가 dict/list 어느 쪽이어도 최대한 보수적으로 읽는다."""
    sources = []
    for key in ("columns", "values", "data", "cells", "valueList", "items"):
        value = row.get(key)
        if value is not None:
            sources.append(value)
    out = {}
    keyset = {str(k) for k in actual_keys}

    def cell_value(cell):
        if isinstance(cell, dict):
            for k in ("value", "rawValue", "displayValue", "val", "amount"):
                if k in cell:
                    v = _clean_number(cell.get(k))
                    if v is not None:
                        return v
            # 값 객체가 한 단계 더 감싸진 경우
            for v in cell.values():
                if isinstance(v, (dict, list)):
                    got = cell_value(v)
                    if got is not None:
                        return got
        elif isinstance(cell, (str, int, float)):
            return _clean_number(cell)
        return None

    for src in sources:
        if isinstance(src, dict):
            for k, cell in src.items():
                val = cell_value(cell)
                if val is not None:
                    out[str(k)] = val
        elif isinstance(src, list):
            for idx, cell in enumerate(src):
                if isinstance(cell, dict):
                    key = cell.get("key") or cell.get("period") or cell.get("date") or cell.get("title")
                    val = cell_value(cell)
                    if key is not None and val is not None:
                        out[str(key)] = val
                    elif val is not None:
                        out[f"__idx_{idx}"] = val
                else:
                    val = cell_value(cell)
                    if val is not None:
                        out[f"__idx_{idx}"] = val
    # 기간 key가 문자열/숫자 타입 차이로 어긋난 경우에도 값이 있으면 반환
    return out


def _finance_row_tail_value(row: dict):
    """기간 키 매칭이 실패한 응답에서 해당 행의 마지막 수치값을 보조적으로 추출한다."""
    for key in ("columns", "values", "data", "cells", "valueList", "items"):
        src = row.get(key)
        vals=[]
        if isinstance(src, dict):
            iterable=list(src.values())
        elif isinstance(src, list):
            iterable=src
        else:
            continue
        for cell in iterable:
            if isinstance(cell, dict):
                for vk in ("value", "rawValue", "displayValue", "val", "amount"):
                    if vk in cell:
                        v=_clean_number(cell.get(vk))
                        if v is not None:
                            vals.append(v); break
            else:
                v=_clean_number(cell)
                if v is not None: vals.append(v)
        if vals:
            return vals[-1]
    return None

def _find_finance_row(rows: list, aliases: list[str]):
    wanted={_norm_label(x) for x in aliases}
    # exact normalized title first
    for row in rows:
        if not isinstance(row, dict):
            continue
        title=_norm_label(row.get("title") or row.get("name") or row.get("label"))
        if title in wanted:
            return row
    # then conservative prefix/contains match
    for row in rows:
        if not isinstance(row, dict):
            continue
        title=_norm_label(row.get("title") or row.get("name") or row.get("label"))
        if any(w and (title.startswith(w) or w in title) for w in wanted):
            return row
    return None


def fetch_financials(code: str) -> dict:
    """네이버 모바일 연간 재무 JSON에서 재무비율을 읽고, 실패 원인을 함께 저장한다.

    우선 API에 명시적인 부채비율/ROE 행이 있으면 그대로 사용한다.
    없으면 확정 연간(컨센서스 제외)의 부채총계/자본총계/당기순이익으로 계산한다.
    """
    url = f"https://m.stock.naver.com/api/stock/{code}/finance/annual"
    try:
        res = SESSION.get(url, timeout=15)
        res.raise_for_status()
        data = res.json()
        periods, rows = _extract_finance_payload(data)
        actual_keys = _finance_period_keys(periods)
        if not actual_keys:
            # 기간 메타가 없는 변형 응답은 행의 columns 키를 기간으로 추정
            candidates=[]
            for row in rows:
                if isinstance(row, dict):
                    cols=row.get("columns") or row.get("values") or {}
                    if isinstance(cols, dict): candidates.extend(str(k) for k in cols.keys())
            actual_keys=sorted(set(candidates))
        if not actual_keys:
            raise ValueError("확정 재무기간을 찾지 못함")
        latest_key=actual_keys[-1]

        def latest_row_value(aliases):
            row=_find_finance_row(rows, aliases)
            if not row:
                return None
            vals=_finance_row_values(row, actual_keys)
            for key in reversed(actual_keys):
                if key in vals:
                    return vals[key], key, str(row.get("title") or row.get("name") or row.get("label") or "")
            return None

        # 네이버 통합정보의 totalInfos에 ROE가 노출되는 종목은 재무 API가 흔들려도 보조적으로 확보한다.
        integration_roe = None
        try:
            integ = fetch_naver_integration(code)
            integration_roe = _clean_number(integ.get("roe"))
        except Exception:
            pass

        explicit_debt=latest_row_value(["부채비율", "Debt Ratio", "DebtRatio", "debtRatio"])
        explicit_roe=latest_row_value(["ROE", "자기자본이익률", "자기자본 이익률"])
        if explicit_roe is None and integration_roe is not None:
            explicit_roe=(integration_roe, latest_key, "totalInfos.roe")
        equity=latest_row_value(["자본총계", "자본총계(지배)", "지배기업소유주지분", "지배기업 소유주지분"])
        debt=latest_row_value(["부채총계"])
        net_income=latest_row_value(["당기순이익", "지배주주순이익", "당기순이익(지배)", "지배기업의소유주에게귀속되는당기순이익"])

        # 실제 응답에서 기간 key가 바뀐 경우를 대비한 마지막 수치값 fallback
        fallback_rows = [
            (["부채총계"], "debt", debt),
            (["자본총계", "자본총계(지배)", "지배기업소유주지분", "지배기업 소유주지분"], "equity", equity),
            (["당기순이익", "지배주주순이익"], "net", net_income),
        ]
        for aliases, holder, current in fallback_rows:
            if current is None:
                row=_find_finance_row(rows, aliases)
                tail=_finance_row_tail_value(row) if row else None
                if tail is not None:
                    value=(tail, latest_key, str(row.get("title") or row.get("name") or row.get("label") or ""))
                    if holder=="debt": debt=value
                    elif holder=="equity": equity=value
                    else: net_income=value

        debt_ratio = explicit_debt[0] if explicit_debt else None
        roe = explicit_roe[0] if explicit_roe else None
        method=[]
        if debt_ratio is not None:
            method.append("api-explicit-debtRatio")
        if roe is not None:
            method.append("api-explicit-roe")

        if debt_ratio is None and debt and equity and equity[0] != 0:
            debt_ratio=debt[0]/equity[0]*100
            method.append("computed-debt/equity")
        if roe is None and net_income and equity and equity[0] != 0:
            roe=net_income[0]/equity[0]*100
            method.append("computed-netincome/equity")

        status="ok" if debt_ratio is not None and roe is not None else ("partial" if debt_ratio is not None or roe is not None else "failed")
        message=("정상" if status=="ok" else "ROE/부채비율 중 일부만 확보" if status=="partial" else "재무비율 계산에 필요한 행을 찾지 못함")
        return {
            "debtRatio": round(debt_ratio,2) if debt_ratio is not None else None,
            "roe": round(roe,2) if roe is not None else None,
            "financialPeriod": latest_key,
            "financialSource": "m.stock.naver.com/finance/annual",
            "financialStatus": status,
            "financialMethod": "+".join(method) if method else None,
            "financialMessage": message,
        }
    except Exception as e:
        print(f"[재무제표 실패] {code}: {e}")
        return {
            "debtRatio": None, "roe": None, "financialPeriod": None,
            "financialSource": None, "financialStatus": "failed",
            "financialMethod": None, "financialMessage": str(e)[:180],
        }

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
        return {
            "foreignNet": current.get("foreignNet"),
            "instNet": current.get("instNet"),
            "indivNet": current.get("indivNet"),
            "investorTrendDate": current.get("investorTrendDate"),
            "flowSource": "Naver 투자자별 매매동향",
            "flowStatus": "ok",
        }

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
                return {
                    "foreignNet": f, "instNet": i,
                    "indivNet": -(f+i) if f is not None and i is not None else None,
                    "investorTrendDate": str(row.iloc[0]) if len(row) else None,
                    "flowSource": "Naver 투자자별 매매동향(HTML 보조)",
                    "flowStatus": "ok",
                }
        raise ValueError("외국인/기관 표를 못 찾음")
    except Exception as e:
        print(f"[수급 실패] {code}: {e}")
        return {
            "foreignNet": None, "instNet": None, "indivNet": None,
            "investorTrendDate": None, "flowSource": None, "flowStatus": "failed",
        }


def _normalize_short_df(df):
    if df is None or df.empty:
        return None
    df=df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns=["".join(str(x) for x in tup if str(x) and "Unnamed" not in str(x)) for tup in df.columns]
    df.index=[str(x).zfill(6) if str(x).isdigit() else str(x) for x in df.index]
    return df


def _short_ratio_from_df(df, code):
    df=_normalize_short_df(df)
    if df is None or df.empty or str(code) not in df.index:
        return None
    row=df.loc[str(code)]
    if hasattr(row, "iloc") and getattr(row, "ndim", 1)>1:
        row=row.iloc[-1]
    columns=[str(c) for c in getattr(row,"index",[])]
    for col in columns:
        n=_norm_label(col)
        if n in {"비중","공매도비중","shortratio","shortsellingratio"} or "공매도비중" in n:
            val=_clean_number(row[col])
            if val is not None:
                return float(val)
    return None


def fetch_short_selling_batch(codes: list[str]) -> tuple[dict, dict]:
    """KRX 공매도 잔고비중을 시장별 전종목 조회로 한 번만 수집한다.
    반환: code -> result, batch diagnostics."""
    empty={c:{"shortSellingRatio":None,"shortSellingDate":None,"shortSellingStatus":"failed","shortSellingMessage":"데이터 없음"} for c in codes}
    diagnostics={"status":"failed","date":None,"marketSuccess":[],"marketErrors":[]}
    krx_id=os.getenv("KRX_ID")
    krx_pw=os.getenv("KRX_PW")
    if not krx_id or not krx_pw:
        diagnostics["status"]="blocked"
        diagnostics["message"]="KRX_ID/KRX_PW 미설정: 2026년 KRX 로그인 필요 정책으로 공매도 조회 불가"
        for code in codes:
            empty[code]["shortSellingStatus"]="blocked"
            empty[code]["shortSellingMessage"]="KRX 로그인 인증정보 필요"
        print("[공매도 차단] KRX_ID/KRX_PW 환경변수가 없어 KRX 조회를 건너뜁니다.")
        return empty, diagnostics
    try:
        from pykrx import stock as pykrx_stock
    except Exception as e:
        diagnostics["message"]=f"pykrx import 실패: {e}"
        return empty, diagnostics

    kst=timezone(timedelta(hours=9)); today=datetime.now(kst).date()
    candidates=[]
    for n in range(2,15):
        d=today-timedelta(days=n)
        if d.weekday()<5: candidates.append(d.strftime("%Y%m%d"))

    for date_str in candidates:
        for market in ("KOSPI","KOSDAQ"):
            try:
                df=pykrx_stock.get_shorting_balance_by_ticker(date_str, market)
                df=_normalize_short_df(df)
                if df is None or df.empty:
                    diagnostics["marketErrors"].append(f"{date_str}/{market}: empty")
                    continue
                found=0
                for code in codes:
                    ratio=_short_ratio_from_df(df, code)
                    if ratio is not None:
                        empty[code]={"shortSellingRatio":ratio,"shortSellingDate":date_str,"shortSellingStatus":"ok","shortSellingMessage":"KRX 시장단위 잔고비중"}
                        found+=1
                if found:
                    diagnostics["status"]="ok"
                    diagnostics["date"]=date_str
                    diagnostics["marketSuccess"].append(f"{date_str}/{market}:{found}")
                else:
                    diagnostics["marketErrors"].append(f"{date_str}/{market}: watchlist 0/{len(codes)}")
            except Exception as e:
                diagnostics["marketErrors"].append(f"{date_str}/{market}: {str(e)[:120]}")
        if any(v["shortSellingRatio"] is not None for v in empty.values()):
            # 계속 다른 시장도 채우되, 이미 확인된 기준일보다 오래된 날짜는 불필요하므로 중단
            break

    # 시장단위 조회가 전부 실패한 경우 종목별 기간 조회를 1회씩만 보조한다.
    if not any(v["shortSellingRatio"] is not None for v in empty.values()):
        from_date=(today-timedelta(days=30)).strftime("%Y%m%d"); to_date=today.strftime("%Y%m%d")
        for code in codes:
            try:
                df=pykrx_stock.get_shorting_balance_by_date(from_date,to_date,code)
                df=_normalize_short_df(df)
                if df is not None and not df.empty:
                    ratio_col=next((c for c in df.columns if "비중" in _norm_label(c) or _norm_label(c) in {"shortratio","shortsellingratio"}),None)
                    if ratio_col:
                        ser=pd.to_numeric(df[ratio_col],errors="coerce").dropna()
                        if not ser.empty:
                            date_value=str(ser.index[-1])[:10].replace("-","")
                            empty[code]={"shortSellingRatio":float(ser.iloc[-1]),"shortSellingDate":date_value,"shortSellingStatus":"ok","shortSellingMessage":"KRX 종목 기간조회"}
            except Exception as e:
                diagnostics["marketErrors"].append(f"{code}/fallback: {str(e)[:120]}")
    ok=sum(1 for v in empty.values() if v["shortSellingRatio"] is not None)
    diagnostics["coverage"]=f"{ok}/{len(codes)}"
    if ok==len(codes): diagnostics["status"]="ok"
    elif ok>0: diagnostics["status"]="partial"
    diagnostics["message"]="정상" if ok else (diagnostics.get("message") or "KRX 공매도 잔고비중 확보 실패")
    return empty, diagnostics


def fetch_short_selling(code: str) -> dict:
    # 호환용 단일 조회. main()에서는 batch를 사용한다.
    result, _ = fetch_short_selling_batch([code])
    return result[code]

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


def _to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_naver_index(code: str, label: str) -> dict:
    """KOSPI/KOSDAQ 지수를 네이버 모바일 JSON에서 수집한다.

    1차: /api/index/{code}/basic (현재 지수 + 등락률)
    2차: /api/index/{code}/price?pageSize=2&page=1 (최근 2거래일 종가로 직접 계산)
    3차: 기존 PC 일별시세 HTML (최후 fallback)

    PC HTML 구조 변경 때문에 지수가 '-'로 남는 문제를 피하기 위해 JSON API를 우선한다.
    """
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://m.stock.naver.com/",
        "Accept": "application/json,text/plain,*/*",
    }

    # 1) 모바일 basic JSON
    try:
        url = f"https://m.stock.naver.com/api/index/{code}/basic"
        res = SESSION.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        price = _to_float(data.get("closePrice") or data.get("nowVal") or data.get("price"))
        rate = _to_float(data.get("fluctuationsRatio") or data.get("changeRate"))
        change = _to_float(data.get("compareToPreviousClosePrice") or data.get("change"))
        if price is not None:
            return {"price": price, "change": change, "changeRate": rate}
    except Exception as e:
        print(f"[지수 JSON basic 실패] {label}({code}): {e}")

    # 2) 모바일 일별 가격 JSON — 필드명이 바뀌어도 closePrice/localTradedAt 중심으로 처리
    try:
        url = f"https://m.stock.naver.com/api/index/{code}/price?pageSize=2&page=1"
        res = SESSION.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        rows = data if isinstance(data, list) else (
            data.get("priceInfos") or data.get("prices") or data.get("datas") or data.get("result") or []
        )
        prices = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = _to_float(row.get("closePrice") or row.get("close") or row.get("nowVal"))
            if price is not None:
                prices.append(price)
            if len(prices) >= 2:
                break
        if len(prices) >= 2:
            latest, prev = prices[0], prices[1]
            diff = latest - prev
            rate = round(diff / prev * 100, 2) if prev else None
            return {"price": latest, "change": round(diff, 2), "changeRate": rate}
    except Exception as e:
        print(f"[지수 JSON price 실패] {label}({code}): {e}")

    # 3) 기존 PC HTML fallback
    url = f"https://finance.naver.com/sise/sise_index_day.naver?code={code}&page=1"
    try:
        res = SESSION.get(url, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        table = soup.find("table", class_="type_1")
        if table is None:
            raise ValueError("표를 못 찾음")
        prices = []
        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            price = _to_float(cols[1].get_text())
            if price is not None:
                prices.append(price)
            if len(prices) >= 2:
                break
        if len(prices) < 2:
            raise ValueError("최근 2개 거래일 데이터를 못 모음")
        latest, prev = prices[0], prices[1]
        diff = latest - prev
        rate = round(diff / prev * 100, 2) if prev else None
        return {"price": latest, "change": round(diff, 2), "changeRate": rate}
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

    # 공매도는 종목별 반복조회가 아니라 KRX 시장단위 전종목 조회를 1회 수행한다.
    code_map = {name: find_code(name) for name in WATCHLIST}
    short_codes = [c for c in code_map.values() if c]
    short_batch, short_diag = fetch_short_selling_batch(short_codes)

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
        short_info = short_batch.get(code, {"shortSellingRatio": None, "shortSellingDate": None, "shortSellingStatus": "failed", "shortSellingMessage": "배치 결과 없음"})

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
            "dataCollection": {
                "price": {"ok": price_info.get("price") is not None, "source": "polling.finance.naver.com"},
                "flow": {
                    "ok": flow_info.get("foreignNet") is not None or flow_info.get("instNet") is not None,
                    "source": flow_info.get("flowSource") or ("Naver 투자자별 매매동향" if (flow_info.get("foreignNet") is not None or flow_info.get("instNet") is not None) else None),
                    "date": flow_info.get("investorTrendDate"),
                    "status": flow_info.get("flowStatus") or ("ok" if (flow_info.get("foreignNet") is not None or flow_info.get("instNet") is not None) else "failed"),
                },
                "financial": {"ok": financial_info.get("financialStatus") == "ok", "status": financial_info.get("financialStatus"), "period": financial_info.get("financialPeriod"), "method": financial_info.get("financialMethod"), "message": financial_info.get("financialMessage")},
                "short": {"ok": short_info.get("shortSellingRatio") is not None, "status": short_info.get("shortSellingStatus"), "date": short_info.get("shortSellingDate"), "message": short_info.get("shortSellingMessage")},
            },
        })
        time.sleep(0.6)

    kst = timezone(timedelta(hours=9))
    output = {
        "updatedAt": datetime.now(kst).isoformat(),
        "stocks": results,
    }

    with open("data/stocks.json", "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(output), f, ensure_ascii=False, indent=2)

    def coverage(key):
        return sum(1 for x in results if x.get("dataCollection",{}).get(key,{}).get("ok"))
    validation = {
        "updatedAt": datetime.now(kst).isoformat(),
        "universe": len(results),
        "coverage": {
            "price": coverage("price"),
            "flow": coverage("flow"),
            "financial": coverage("financial"),
            "short": coverage("short"),
        },
        "shortSellingBatch": short_diag,
        "scoreReady": sum(1 for x in results if coverage("financial") and x.get("dataCollection",{}).get("short",{}).get("ok")),
        "stocks": [{
            "name": x.get("name"), "code": x.get("code"),
            "price": x.get("dataCollection",{}).get("price",{}),
            "flow": x.get("dataCollection",{}).get("flow",{}),
            "financial": x.get("dataCollection",{}).get("financial",{}),
            "short": x.get("dataCollection",{}).get("short",{}),
            "debtRatio": x.get("debtRatio"), "roe": x.get("roe"),
            "shortSellingRatio": x.get("shortSellingRatio"),
        } for x in results],
        "policy": "재무와 공매도는 값이 없으면 정상으로 간주하지 않으며, 기존 버전 값으로 대체하지 않는다.",
    }
    validation["scoreReady"] = sum(1 for x in results if x.get("dataCollection",{}).get("financial",{}).get("ok") and x.get("dataCollection",{}).get("short",{}).get("ok"))
    with open("data/validation.json", "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(validation), f, ensure_ascii=False, indent=2)

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
