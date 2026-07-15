"""
관심종목 뉴스·공시 영향도 분석기 (Gemini 무료 티어 버전)
- 종목별로 Gemini에게 Google 검색 그라운딩으로 "최근 3일 이내 국내+해외 뉴스 또는
  전자공시(DART) 중 이 종목에 영향을 줄만한 중요한 게 있는지" 판단시키고,
  있으면 요약 + 영향도(%) + 출처(뉴스/공시)를 받아온다.
- gemini-3.1-flash-lite 는 무료 티어에서 하루 500회 요청 + 검색 그라운딩을 지원한다.
  (23종목 x 하루 2번 = 46회 정도면 넉넉하게 무료 범위 안)
- 결과를 data/news.json 에 저장한다.

주의: 여기서 나오는 impact_pct는 실제 주가 변동을 예측하는 수치가 아니라,
Gemini가 뉴스/공시를 읽고 "이 정도 중요도/영향력일 것 같다"고 판단한 추정치입니다.
투자 참고용이며, 이 수치를 매매 신호로 그대로 쓰면 안 됩니다.
"""

import json
import os
import re
import time

from google import genai
from google.genai import types

from scraper import WATCHLIST  # 종목명: 섹터 딕셔너리 재사용

MODEL = "gemini-3.1-flash-lite"  # 무료 티어 RPD가 가장 넉넉한 최신 모델 (2.5-flash-lite는 신규 사용자에게 막혀서 교체함)

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

PROMPT_TEMPLATE = """\
너는 한국 주식 투자자를 위한 뉴스·공시 분석 보조원이다.
종목명: {name}

Google 검색을 이용해서 오늘로부터 최근 3일 이내(오늘 포함)에 나온, 이 종목과 직접 관련된
뉴스와 전자공시(DART 공시: 실적공시, 수주공시, 유상증자, 자사주, 최대주주 변경 등)를 모두 찾아라.
국내/해외 뉴스와 공시를 둘 다 확인하고, 그중 주가에 영향을 줄 만큼 중요한 것이 있는지 판단해라.

찾았으면 아래 JSON 형식으로만 답하라. 다른 설명 문장은 절대 붙이지 마라. 마크다운 코드블록도 쓰지 마라.

{{
  "has_important_news": true 또는 false,
  "source": "뉴스" 또는 "공시" (둘 다 있으면 더 중요한 쪽 하나만),
  "date": "YYYY-MM-DD" (해당 뉴스/공시 발생일, 모르면 빈 문자열),
  "summary": "중요 뉴스/공시를 2문장 이내 한국어로 요약 (없으면 빈 문자열)",
  "impact_pct": 0~100 사이 숫자 (이 뉴스/공시가 주가에 줄 수 있는 영향력의 크기를 네가 판단한 추정치, 중요한 게 없으면 0),
  "direction": "up" 또는 "down" 또는 "neutral"
}}

최근 3일 이내에 중요한 뉴스나 공시가 딱히 없으면 has_important_news를 false로 하고 impact_pct는 0으로 답하라.
확실하지 않은 추측은 하지 말고, 검색으로 확인되지 않으면 has_important_news를 false로 하라.
"""


def analyze_stock(name: str) -> dict:
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=PROMPT_TEMPLATE.format(name=name),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )

        text = response.text or ""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"JSON을 못 찾음: {text[:200]}")

        parsed = json.loads(match.group())
        return {
            "hasImportantNews": bool(parsed.get("has_important_news", False)),
            "source": parsed.get("source", "뉴스"),
            "date": parsed.get("date", ""),
            "summary": parsed.get("summary", ""),
            "impactPct": float(parsed.get("impact_pct", 0)),
            "direction": parsed.get("direction", "neutral"),
        }
    except Exception as e:
        print(f"[뉴스 분석 실패] {name}: {e}")
        return {"hasImportantNews": False, "source": "", "date": "", "summary": "", "impactPct": 0, "direction": "neutral"}


def main():
    os.makedirs("data", exist_ok=True)

    results = {}
    for name in WATCHLIST:
        print(f"분석 중: {name}")
        results[name] = analyze_stock(name)
        time.sleep(4)  # 무료 티어 분당 요청 제한(RPM)에 안전하게 걸리도록 대기

    with open("data/news.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"✅ {len(results)}개 종목 뉴스 분석 완료")


if __name__ == "__main__":
    main()
