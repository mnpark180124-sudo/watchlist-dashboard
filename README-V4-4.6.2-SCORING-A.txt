V4-4.6.2 SCORING-A PATCH

덮어쓸 파일은 2개뿐입니다.
- index.html
- scoring.py

변경 내용
- 목표가/52주/ROE/공매도 점수 포화 완화
- 금융 섹터는 일반기업 부채비율 감점 제외, ROE 중심
- 결측 데이터는 가중치 재정규화하지 않고 중립 50점 처리
- 뉴스는 materialImpact=true 이벤트만 점수 반영 유지
- 시장환경 조정폭 최대 ±10점
- 야간 미국 섹터 동향은 점수에 미반영

보존
- data/stocks.json
- data/score_history.json
- data/backtest.json
- data/timing_history.json
- archive 및 기타 누적 데이터
