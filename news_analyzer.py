"""
관심종목 뉴스·공시 영향도 분석기 (Gemini 무료 티어 버전 - 배치 처리)
- 전체 종목을 한 번의 Gemini 호출에 다 묶어서 물어본다 (무료 티어 하루 요청수(RPD) 한도가
  실측 기준 20개로 매우 낮아서, 종목마다 따로 호출하면 바로 초과된다).
  종목 1번 호출 + 지정학적 리스크 1번 = 실행 1번당 2번 호출, 하루 2번 돌려도 4번으로
  20개 한도 안에 여유 있게 들어간다 (디버깅용 수동 재실행 여유분도 넉넉히 남음).
- Google 검색 그라운딩으로 "최근 3일 이내 국내+해외 뉴스 또는 전자공시(DART) 중 이 종목에
  영향을 줄만한 게 있는지" 판단시키고, 있으면 요약 + 영향도(%) + 출처(뉴스/공시)를 받아온다.
- 결과를 data/news.json 에 저장한다.

주의: 여기서 나오는 impact_pct는 실제 주가 변동을 예측하는 수치가 아니라,
Gemini가 뉴스/공시를 읽고 "이 정도 중요도/영향력일 것 같다"고 판단한 추정치입니다.
투자 참고용이며, 이 수치를 매매 신호로 그대로 쓰면 안 됩니다.
"""

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

from google import genai
from google.genai import types

from scraper import WATCHLIST  # 종목명: 섹터 딕셔너리 재사용

MODEL = "gemini-3.1-flash-lite"
BATCH_SIZE = 40  # 종목 수보다 넉넉하게 잡아서 사실상 한 번의 호출로 전체 처리 (무료 티어 RPD=20 한도 안에서 최대한 여유 확보)

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

DEFAULT_ITEM = {"hasImportantNews": False, "source": "", "date": "", "summary": "", "impactPct": 0, "direction": "neutral"}

BATCH_PROMPT_TEMPLATE = """\
너는 한국 주식 투자자를 위한 뉴스·공시 분석 보조원이다.
아래 종목들 각각에 대해 Google 검색으로 최근 3일 이내(오늘 포함) 뉴스와 전자공시를 확인해라.

종목 목록: {names}

각 종목마다 이렇게 검색해라:
1. "{{종목명}} 공시" "{{종목명}} dart.fss.or.kr" 로 DART 전자공시를 찾아라.
   공시 유형 예시: 실적공시, 수주공시, 유상증자, 자사주, 최대주주변경, 신규시설투자,
   타법인주식취득, 풍문또는보도에대한해명, 전환사채발행 등. 사소해 보여도 놓치지 말고 찾아라.
2. "{{종목명}} 뉴스"로 국내/해외 일반 뉴스도 찾아라.

찾았으면 종목마다 주가에 영향을 줄 만큼 중요한 게 있는지 판단해라.

아래 JSON 형식으로만 답하라. 종목명을 key로 쓰고, 목록에 있는 종목을 전부 포함해라.
다른 설명 문장은 절대 붙이지 마라. 마크다운 코드블록도 쓰지 마라.

{{
  "종목명1": {{
    "has_important_news": true 또는 false,
    "source": "뉴스" 또는 "공시",
    "date": "YYYY-MM-DD" (모르면 빈 문자열),
    "summary": "2문장 이내 한국어 요약 (없으면 빈 문자열)",
    "impact_pct": 0~100 사이 숫자 (중요한 게 없으면 0),
    "direction": "up" 또는 "down" 또는 "neutral"
  }},
  "종목명2": {{ ... }}
}}

최근 3일 이내에 공시나 뉴스가 확인되면 사소해 보여도 일단 has_important_news를 true로 하고
impact_pct를 낮게(예: 10~20) 매겨라. 정말 아무것도 검색되지 않을 때만 false로 답하라.
"""


def analyze_batch(names: list[str]) -> dict:
    """종목 이름 리스트를 한 번에 넣어서 종목명 -> 결과 딕셔너리를 받는다."""
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=BATCH_PROMPT_TEMPLATE.format(names=", ".join(names)),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                max_output_tokens=8192,  # 종목 수가 많아 응답이 길어질 수 있어 여유 있게 설정
            ),
        )

        text = response.text or ""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"JSON을 못 찾음: {text[:200]}")

        parsed = json.loads(match.group())
        results = {}
        for name in names:
            item = parsed.get(name)
            if not item:
                results[name] = dict(DEFAULT_ITEM)
                continue
            results[name] = {
                "hasImportantNews": bool(item.get("has_important_news", False)),
                "source": item.get("source", ""),
                "date": item.get("date", ""),
                "summary": item.get("summary", ""),
                "impactPct": float(item.get("impact_pct", 0)),
                "direction": item.get("direction", "neutral"),
            }
        return results
    except Exception as e:
        print(f"[뉴스 배치 분석 실패] {names}: {e}")
        return {name: dict(DEFAULT_ITEM) for name in names}


GEOPOLITICAL_PROMPT = """\
너는 한국 주식시장에 영향을 주는 지정학적 리스크를 확인하는 보조원이다.

Google 검색을 이용해서 최근 3일 이내 미국-이란 관련 군사적 충돌/전쟁 리스크 뉴스가 있는지 확인해라.
(공습, 호르무즈 해협 봉쇄, 유가 급등, 미군 파병, 확전 우려 등)

아래 JSON 형식으로만 답하라. 다른 설명 문장은 절대 붙이지 마라. 마크다운 코드블록도 쓰지 마라.

{
  "has_risk": true 또는 false,
  "direction": "down" (확전/악재로 시장에 부정적) 또는 "up" (완화/호재로 시장에 긍정적) 또는 "neutral",
  "summary": "2문장 이내 한국어 요약 (리스크 없으면 빈 문자열)"
}

확실한 근거가 없으면 has_risk를 false로 하라.
"""


def analyze_geopolitical_risk() -> dict:
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=GEOPOLITICAL_PROMPT,
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
            "hasRisk": bool(parsed.get("has_risk", False)),
            "direction": parsed.get("direction", "neutral"),
            "summary": parsed.get("summary", ""),
        }
    except Exception as e:
        print(f"[지정학적 리스크 분석 실패] {e}")
        return {"hasRisk": False, "direction": "neutral", "summary": ""}


def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def main():
    os.makedirs("data", exist_ok=True)

    names = list(WATCHLIST)
    results = {}
    batches = list(chunk(names, BATCH_SIZE))
    for i, batch in enumerate(batches, 1):
        print(f"배치 {i}/{len(batches)} 분석 중: {', '.join(batch)}")
        results.update(analyze_batch(batch))
        time.sleep(5)  # 무료 티어 분당 요청 제한(RPM)에 안전하게 걸리도록 대기

    with open("data/news.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"✅ {len(results)}개 종목 뉴스 분석 완료 ({len(batches)}번 호출)")

    print("지정학적 리스크(미국-이란) 확인 중...")
    geo = analyze_geopolitical_risk()
    kst = timezone(timedelta(hours=9))
    geo["updatedAt"] = datetime.now(kst).isoformat()
    with open("data/geopolitical.json", "w", encoding="utf-8") as f:
        json.dump(geo, f, ensure_ascii=False, indent=2)
    print("✅ 지정학적 리스크 분석 완료")


if __name__ == "__main__":
    main()
