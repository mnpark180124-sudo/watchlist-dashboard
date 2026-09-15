# KRX 공매도 데이터 설정

2026년 KRX 데이터 조회 방식 변경으로 pykrx의 로그인 필요 API는 `KRX_ID`, `KRX_PW` 환경변수가 필요합니다.

## GitHub Actions 설정

1. GitHub 저장소 → **Settings**
2. **Secrets and variables → Actions**
3. **New repository secret**
4. 이름 `KRX_ID` / KRX 계정 ID
5. 이름 `KRX_PW` / KRX 계정 비밀번호

비밀번호를 코드나 `scraper.py`에 직접 넣지 마세요.

인증정보가 없으면 scraper는 공매도 API를 호출하지 않고 `blocked` 상태로 기록합니다. 이 경우 다른 데이터 수집은 계속 진행됩니다.
