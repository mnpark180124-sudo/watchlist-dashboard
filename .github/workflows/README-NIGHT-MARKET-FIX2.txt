야간시장 수집 FIX2

첨부 원본에서 확인한 문제:
night-sector.yml이 저장소 최상위에 있어 Actions 실행 대상에서 빠짐.
data/sector-night.json은 updatedAt=null, markets={}인 초기 상태.

적용:
1. ZIP 압축을 풀고 저장소 루트에 병합합니다.
2. 반드시 최종 경로가 .github/workflows/night-sector.yml인지 확인합니다.
   GitHub 웹 업로드를 쓸 때도 이 경로에 직접 파일을 만드세요.
3. 기본 브랜치에 커밋합니다. 최상위 night-sector.yml은 실행되지 않는 기존 파일입니다.
4. Actions > Update Night Sector > Run workflow > 기본 브랜치 > Run workflow.
5. 성공 후 data/sector-night.json의 updatedAtKST와 markets를 확인하고 화면을 새로고침합니다.

자동 실행 예정: 한국시간 화~토 오전 07:30 (미국 월~금 거래 마감 후).
GitHub 예약 실행은 지연될 수 있습니다. 휴장일에는 마지막 완료 거래일을 표시합니다.
전체 수집 실패 시 기존 JSON을 보존하고 작업을 실패 처리합니다.

이 패치는 workflow 경로를 바로잡고 기본 브랜치를 자동 선택하며 push를 최대 3회 시도합니다.
기존 수집 스크립트는 그대로 포함합니다. 야간시장은 평가점수에 반영하지 않습니다.
data/ 파일 및 archive는 ZIP에 포함하지 않아 덮어쓰지 않습니다.

검증: YAML 구조, 내장 Python/Bash 문법, 완료 거래일 선별, 전체 실패 시 보존,
ZIP 재압축 해제 및 파일 일치 확인. 실제 GitHub 실행/외부 시세 수집은 미검증입니다.
적용 후 작업이 빨간색이면 실패한 단계의 로그가 필요합니다.
