NIGHT-SECTOR FIX

적용 파일
1) night_sector.py
2) .github/workflows/night-sector.yml
3) data/sector-night.json

목적
- 미국 월~금 장 마감 후 다음날 07:30 KST 자동 수집
- Actions > Update Night Sector > Run workflow 수동 실행 지원
- 최근 완료된 미국 거래일 종가만 사용
- data/sector-night.json만 커밋
- 야간 미국시장 자료는 평가점수에 반영하지 않음
- 기존 score_history/backtest/timing_history/stocks 등 누적 데이터는 포함하지 않음

적용 후 첫 확인
- GitHub Actions에서 Update Night Sector가 보이는지 확인
- 최초 적용 직후에는 Run workflow를 한 번 실행하면 바로 data/sector-night.json 생성/갱신 가능
- 이후 예약 실행은 미국 월~금 장 마감 다음날 07:30 KST
