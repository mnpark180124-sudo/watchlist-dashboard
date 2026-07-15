"""
관심종목 뉴스 영향도 분석기 (Gemini 무료 티어 버전)
- 종목별로 Gemini에게 Google 검색 그라운딩으로 "어제자 국내+해외 뉴스 중 이 종목에
  영향을 줄만한 중요 뉴스가 있는지" 판단시키고, 있으면 요약 + 영향도(%)를 받아온다.
- gemini-2.5-flash-lite 는 무료 티어에서 하루 500회 요청 + 검색 그라운딩을 지원한다.
  (23종목 x 하루 2번 = 46회 정도면 넉넉하게 무료 범위 안)
- 결과를 data/news.json 에 저장한다.

주의: 여기서 나오는 impact_pct는 실제 주가 변동을 예측하는 수치가 아니라,
Gemini가 뉴스를 읽고 "이 정도 중요도/영향력일 것 같다"고 판단한 추정치입니다.
투자 참고용이며, 이 수치를 매매 신호로 그대로 쓰면 안 됩니다.
"""

import json
import os
import re
import time

from google import genai
from google.genai import types

from scraper import WATCHLIST  # 종목명: 섹터 딕셔너리 재사용

MODEL = "gemini-2.5-flash-lite"  # 무료 티어 RPD가 가장 넉넉한 모델

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

PROMPT_TEMPLATE = """\
너는 한국 주식 투자자를 위한 뉴스 분석 보조원이다.
종목명: {name}

Google 검색을 이용해서 어제(전일) 기준 이 종목과 직접 관련된 국내/해외 뉴스를 찾아라.
(실적, 수주, 규제, 소송, 경영진 변동, 업황, 거시경제 이슈 중 이 종목에 직접 영향 있는 것 위주)

찾았으면 아래 JSON 형식으로만 답하라. 다른 설명 문장은 절대 붙이지 마라. 마크다운 코드블록도 쓰지 마라.

{{
  "has_important_news": true 또는 false,
  "summary": "중요 뉴스를 2문장 이내 한국어로 요약 (없으면 빈 문자열)",
  "impact_pct": 0~100 사이 숫자 (이 뉴스가 주가에 줄 수 있는 영향력의 크기를 네가 판단한 추정치, 중요 뉴스 없으면 0),
  "direction": "up" 또는 "down" 또는 "neutral"
}}

중요 뉴스가 딱히 없으면 has_important_news를 false로 하고 impact_pct는 0으로 답하라.
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
            "summary": parsed.get("summary", ""),
            "impactPct": float(parsed.get("impact_pct", 0)),
            "direction": parsed.get("direction", "neutral"),
        }
    except Exception as e:
        print(f"[뉴스 분석 실패] {name}: {e}")
        return {"hasImportantNews": False, "summary": "", "impactPct": 0, "direction": "neutral"}


def main():
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
