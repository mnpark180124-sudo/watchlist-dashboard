"""
관심종목 뉴스·공시 수집/영향도 분석기

V4-3.5 변경점
- 기존처럼 Gemini의 Google 검색 결과에만 의존하지 않는다.
- Google News RSS에서 최근 3일 뉴스를 먼저 직접 수집한다.
- DART 회사별 검색 페이지에서도 최근 공시를 보조 수집한다.
- Gemini API가 정상일 때만 수집된 기사/공시를 요약·영향도 분석한다.
- Gemini 호출이 실패해도 수집한 원문 제목/출처/링크를 그대로 저장한다.
- 따라서 AI 검색 실패가 "오늘 뉴스 없음"으로 둔갑하지 않는다.
"""

import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from scraper import WATCHLIST

MODEL = "gemini-3.1-flash-lite"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; watchlist-dashboard/1.0; +https://github.com/)"
}
DEFAULT_ITEM = {
    "hasImportantNews": False,
    "source": "",
    "date": "",
    "summary": "",
    "impactPct": 0,
    "direction": "neutral",
    "url": "",
    "title": "",
    "articles": [],
}
KST = timezone(timedelta(hours=9))

IMPORTANT_KEYWORDS = [
    "실적", "영업이익", "매출", "수주", "계약", "공급", "투자", "증설", "인수", "합병",
    "유상증자", "무상증자", "자사주", "최대주주", "전환사채", "CB", "BW", "공개매수",
    "배당", "소송", "리콜", "허가", "승인", "임상", "상장", "거래정지", "감사의견",
    "해명", "횡령", "배임", "공시", "신규시설", "타법인", "풍문", "수주", "납품",
]
UP_KEYWORDS = [
    "호실적", "어닝서프라이즈", "수주", "계약", "증가", "개선", "흑자", "승인", "허가",
    "자사주", "배당", "증설", "신규", "성장", "상향", "최대", "공급계약",
]
DOWN_KEYWORDS = [
    "적자", "감소", "하락", "악화", "소송", "리콜", "철회", "취소", "유상증자", "전환사채",
    "횡령", "배임", "감사의견", "거래정지", "하향", "지연", "중단", "해명", "우려",
]

# 검색명이 실제 뉴스 제목에 쓰이는 법인명/약칭과 다를 수 있는 종목용 별칭
NEWS_ALIASES = {
    "TIGER 코리아AI전기전자": ["TIGER 코리아AI전력기기TOP3플러스", "코리아AI전력기기TOP3플러스"],
    "KODEX AI반도체": ["KODEX AI반도체TOP2플러스", "AI반도체TOP2플러스"],
    "LIG디펜스앤에어로스페이스": ["LIG디펜스앤에어로스페이스", "LIG넥스원"],
    "HD건설기계": ["HD현대건설기계", "현대건설기계"],
    "HD현대마린솔루션": ["HD현대마린솔루션", "현대마린솔루션"],
    "삼성E&A": ["삼성E&A", "삼성엔지니어링"],
    "SFA넥셀": ["SFA넥셀"],
}

# 짧거나 일반명사에 가까운 종목은 회사/증권 맥락이 함께 있어야 통과시킨다.
CONTEXT_KEYWORDS = [
    "주가", "주식", "증권", "기업", "실적", "매출", "영업이익", "수주", "계약",
    "공시", "배당", "투자", "증설", "공급", "납품", "목표주가", "상장", "종목",
    "전망", "주주", "자사주", "유상증자", "무상증자", "인수", "합병",
]
SPAM_KEYWORDS = [
    "슬롯", "카지노", "토토", "홀덤", "바카라", "무료게임", "게임머니", "먹튀",
    "성인", "광고", "홍보", "쿠폰", "이벤트",
]

def relevance_score(name: str, item: dict) -> int:
    """뉴스 제목이 해당 종목과 실제로 관련 있는지 보수적으로 점수화한다."""
    title = clean_text(item.get("title", ""))
    source = item.get("source", "")
    if not title:
        return 0
    low = title.lower()
    # DART 원문은 이미 기업별 검색에서 나온 자료이므로 제목 필터를 통과시킨다.
    if item.get("type") == "dart" or source == "DART":
        return 100
    if any(k in low for k in SPAM_KEYWORDS):
        return 0

    candidates = [name] + NEWS_ALIASES.get(name, [])
    score = 0
    for candidate in candidates:
        c = clean_text(candidate).lower()
        if c and c in low:
            score = max(score, 4 if len(c) >= 4 else 3)

    # 짧은 이름은 동명이인/일반명사 오탐이 많아 맥락어를 추가로 요구한다.
    if len(clean_text(name)) <= 3:
        if any(k in low for k in CONTEXT_KEYWORDS):
            score += 1
        else:
            return 0
    elif score == 0:
        # 별칭을 못 찾더라도 제목에 핵심 회사명 일부가 함께 있고 증권 맥락이면 약하게 허용
        compact = re.sub(r"[^0-9a-zA-Z가-힣]", "", name).lower()
        if len(compact) >= 4 and compact[:4] in re.sub(r"[^0-9a-zA-Z가-힣]", "", low).lower() and any(k in low for k in CONTEXT_KEYWORDS):
            score = 2
    return score


def filter_news(name: str, items: list[dict]) -> list[dict]:
    kept = []
    rejected = 0
    seen_urls = set()
    for item in items:
        url = item.get("url", "")
        if url and url in seen_urls:
            continue
        score = relevance_score(name, item)
        if score <= 0:
            rejected += 1
            continue
        item = dict(item)
        item["relevanceScore"] = score
        kept.append(item)
        if url:
            seen_urls.add(url)
    kept.sort(key=lambda x: (x.get("relevanceScore", 0), x.get("datetime", "")), reverse=True)
    return kept, rejected


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST)
    except Exception:
        pass
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value[:10], fmt).replace(tzinfo=KST)
        except Exception:
            continue
    return None


def fetch_google_news(name: str, days: int = 3) -> list[dict]:
    """Google News RSS에서 최근 뉴스/공시 관련 검색 결과를 직접 가져온다."""
    q = f'"{name}" when:{days}d'
    url = "https://news.google.com/rss/search"
    try:
        res = requests.get(
            url,
            params={"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
            headers=HEADERS,
            timeout=12,
        )
        res.raise_for_status()
        root = ET.fromstring(res.content)
    except Exception as e:
        print(f"[Google News RSS 실패] {name}: {e}")
        return []

    cutoff = datetime.now(KST) - timedelta(days=days)
    items = []
    for node in root.findall("./channel/item"):
        title = clean_text(node.findtext("title", ""))
        link = clean_text(node.findtext("link", ""))
        pub = parse_date(node.findtext("pubDate", ""))
        source_node = node.find("source")
        source_name = clean_text(source_node.text if source_node is not None else "뉴스")
        if not title or not pub or pub < cutoff:
            continue
        items.append({
            "title": title,
            "url": link,
            "date": pub.strftime("%Y-%m-%d"),
            "datetime": pub.isoformat(),
            "source": source_name or "뉴스",
            "type": "news",
        })
    return items[:8]


def fetch_dart_search(name: str, days: int = 3) -> tuple[list[dict], bool]:
    """DART 회사별 검색 HTML에서 최근 공시를 보조 수집한다.

    OpenDART API 키가 없어도 동작하도록 공개 검색 페이지를 사용한다.
    API 키가 설정된 경우에도 이 함수는 보조 원천으로만 사용한다.
    """
    url = "https://dart.fss.or.kr/dsab001/main.do"
    last_error = None
    for attempt in range(3):
        try:
            res = requests.get(
                url,
                params={"autoSearch": "Y", "textCrpNm": name},
                headers={**HEADERS, "Connection": "close"},
                timeout=15,
            )
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            break
        except Exception as e:
            last_error = e
            time.sleep(1.0 * (attempt + 1))
    else:
        print(f"[DART 검색 실패] {name}: {last_error}")
        return [], False

    cutoff = datetime.now(KST) - timedelta(days=days)
    found = []
    seen = set()

    for a in soup.select("a"):
        href = a.get("href", "")
        if "dsaf001/main.do" not in href or "rcpNo=" not in href:
            continue
        title = clean_text(a.get_text(" ", strip=True))
        if not title or title in seen:
            continue
        parent = a.parent
        row_text = clean_text(parent.parent.get_text(" ", strip=True) if parent and parent.parent else "")
        date_match = re.search(r"(20\d{2})[.\-/](\d{2})[.\-/](\d{2})", row_text)
        if not date_match:
            # 링크 주변에 날짜가 없으면 제목만으로는 최근성 판단이 안 되므로 제외
            continue
        pub = datetime(
            int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)), tzinfo=KST
        )
        if pub < cutoff:
            continue
        full_url = href if href.startswith("http") else "https://dart.fss.or.kr" + href
        seen.add(title)
        found.append({
            "title": title,
            "url": full_url,
            "date": pub.strftime("%Y-%m-%d"),
            "datetime": pub.isoformat(),
            "source": "DART",
            "type": "dart",
        })
    return found[:8], True


def classify_fallback(item: dict) -> dict:
    """Gemini 실패 시에도 화면에 의미 있는 결과가 남도록 하는 안전한 로컬 분류."""
    title = item.get("title", "")
    low = title.lower()
    important = any(k.lower() in low for k in IMPORTANT_KEYWORDS)
    up = sum(k.lower() in low for k in UP_KEYWORDS)
    down = sum(k.lower() in low for k in DOWN_KEYWORDS)
    direction = "up" if up > down else "down" if down > up else "neutral"
    impact = 10
    if important:
        impact = min(70, 20 + 10 * max(up, down))
    return {
        "hasImportantNews": True,
        "source": "공시" if item.get("type") == "dart" else "뉴스",
        "date": item.get("date", ""),
        "summary": title,
        "impactPct": impact,
        "direction": direction,
        "url": item.get("url", ""),
        "title": title,
    }


def collect_raw() -> dict:
    results = {}
    stats = {"stocksWithNews": 0, "rawNews": 0, "rawDart": 0, "rejectedNews": 0, "dartOk": 0, "dartFailed": 0}
    for i, name in enumerate(WATCHLIST, 1):
        news = fetch_google_news(name)
        dart, dart_ok = fetch_dart_search(name)
        merged = news + dart
        merged.sort(key=lambda x: x.get("datetime", ""), reverse=True)
        # 같은 제목 중복 제거
        unique = []
        seen = set()
        for item in merged:
            key = re.sub(r"\s+", " ", item.get("title", "")).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        filtered, rejected = filter_news(name, unique)
        # 관련성 점수가 높은 자료를 우선하되, 최신 자료도 충분히 반영한다.
        results[name] = sorted(filtered, key=lambda x: (x.get("relevanceScore", 0), x.get("datetime", "")), reverse=True)[:5]
        stats["rawNews"] += len(news)
        stats["rawDart"] += len(dart)
        stats["dartOk"] += 1 if dart_ok else 0
        stats["dartFailed"] += 0 if dart_ok else 1
        stats["rejectedNews"] += rejected
        stats["stocksWithNews"] += 1 if results[name] else 0
        print(f"[{i}/{len(WATCHLIST)}] {name}: 뉴스 {len(news)} / DART {len(dart)} / 관련 {len(results[name])} / 제외 {rejected}")
        time.sleep(0.15)
    return results, stats


def gemini_summarize(raw: dict) -> dict:
    """수집된 제목만 Gemini로 요약한다. 검색 grounding은 여기서 사용하지 않는다."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("[Gemini] GEMINI_API_KEY 없음 → 원문 제목 fallback 사용")
        return {}

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[Gemini 초기화 실패] {e}")
        return {}

    compact = {}
    for name, items in raw.items():
        compact[name] = [
            {"title": x["title"], "date": x["date"], "source": x["source"]}
            for x in items[:3]
        ]

    prompt = f"""너는 한국 주식 뉴스·공시를 요약하는 보조원이다.
아래는 이미 RSS/DART에서 직접 수집한 최근 3일 자료다. 검색하지 말고 아래 자료만 사용해라.
각 종목에 대해 가장 중요한 1건을 골라 2문장 이내 한국어로 요약하고, 주가 영향 방향과 중요도(0~100)를 평가해라.
자료가 있으면 has_important_news=true로 하라. 자료가 없으면 false로 하라.
반드시 JSON 객체만 출력하라.

입력:
{json.dumps(compact, ensure_ascii=False)}

출력 형식:
{{
  "종목명": {{
    "has_important_news": true,
    "source": "뉴스" 또는 "공시",
    "date": "YYYY-MM-DD",
    "summary": "2문장 이내",
    "impact_pct": 0,
    "direction": "up" 또는 "down" 또는 "neutral"
  }}
}}
"""

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=8192,
            ),
        )
        text = response.text or ""
        parsed = json.loads(text)
        out = {}
        for name, items in raw.items():
            item = parsed.get(name) if isinstance(parsed, dict) else None
            if not item or not items:
                continue
            first = items[0]
            out[name] = {
                "hasImportantNews": bool(item.get("has_important_news", True)),
                "source": item.get("source") or ("공시" if first.get("type") == "dart" else "뉴스"),
                "date": item.get("date") or first.get("date", ""),
                "summary": clean_text(item.get("summary", "")) or first.get("title", ""),
                "impactPct": max(0, min(100, float(item.get("impact_pct", 10)))),
                "direction": item.get("direction", "neutral"),
                "url": first.get("url", ""),
                "title": first.get("title", ""),
            }
        print(f"[Gemini] {len(out)}개 종목 요약 완료")
        return out
    except Exception as e:
        print(f"[Gemini 요약 실패] {e} → 35개 종목 원문 fallback 사용")
        return {}


def analyze_geopolitical_risk() -> dict:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {"hasRisk": False, "direction": "neutral", "summary": ""}
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        prompt = """최근 3일 이내 미국-이란 관련 군사적 충돌/전쟁 리스크가 한국 주식시장에 영향을 줄 만한 수준인지 확인해라. JSON만 출력: {\"has_risk\":true/false,\"direction\":\"down\"/\"up\"/\"neutral\",\"summary\":\"2문장 이내\"}"""
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        text = response.text or ""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("JSON 없음")
        parsed = json.loads(match.group())
        return {
            "hasRisk": bool(parsed.get("has_risk", False)),
            "direction": parsed.get("direction", "neutral"),
            "summary": clean_text(parsed.get("summary", "")),
        }
    except Exception as e:
        print(f"[지정학 분석 실패] {e}")
        return {"hasRisk": False, "direction": "neutral", "summary": ""}


def main():
    os.makedirs("data", exist_ok=True)
    raw, stats = collect_raw()
    ai = gemini_summarize(raw)

    results = {}
    total_items = 0
    for name in WATCHLIST:
        items = raw.get(name, [])
        if items:
            total_items += len(items)
            # Gemini 결과가 있으면 그것을 쓰되, 링크/제목은 직접 수집값으로 보강
            result = ai.get(name) or classify_fallback(items[0])
            result["url"] = result.get("url") or items[0].get("url", "")
            result["title"] = result.get("title") or items[0].get("title", "")
            result["articles"] = [
                {"title": x.get("title", ""), "date": x.get("date", ""), "source": x.get("source", ""), "url": x.get("url", ""), "type": x.get("type", "news")}
                for x in items[:5]
            ]
            results[name] = result
        else:
            results[name] = dict(DEFAULT_ITEM)

    # 기존 scoring.py / snapshot.py와의 호환성을 위해 종목명을 최상위 key로 그대로 유지한다.
    # 메타데이터는 _meta 아래에 별도로 저장한다.
    news_output = dict(results)
    news_output["_meta"] = {
        "updatedAt": datetime.now(KST).isoformat(),
        "sourceStatus": {
            "googleNewsRss": True,
            "dartSearch": stats.get("dartOk", 0) > 0,
            "geminiSummary": bool(ai),
            "dartFailed": stats.get("dartFailed", 0),
        },
        "itemCount": total_items,
        "collectionStats": stats,
        "filterPolicy": "종목명/별칭 + 증권 맥락 + 스팸 차단",
    }
    with open("data/news.json", "w", encoding="utf-8") as f:
        json.dump(news_output, f, ensure_ascii=False, indent=2)
    collected_stocks = sum(1 for name in WATCHLIST if raw.get(name))
    print(f"✅ 35개 종목 원문 수집 완료: {collected_stocks}/{len(WATCHLIST)}개 종목 / {total_items}개 항목")
    if not ai:
        print("ℹ️ Gemini 요약을 사용할 수 없어 원문 제목 기반 fallback 결과를 저장합니다.")

    geo = analyze_geopolitical_risk()
    geo["updatedAt"] = datetime.now(KST).isoformat()
    with open("data/geopolitical.json", "w", encoding="utf-8") as f:
        json.dump(geo, f, ensure_ascii=False, indent=2)
    print("✅ 지정학적 리스크 분석 완료")


if __name__ == "__main__":
    main()
