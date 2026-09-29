NIGHT-SECTOR WORKFLOW FIX
적용 후 최종 경로: .github/workflows/night-sector.yml
- UTC 월~금 22:30 = KST 화~토 07:30
- workflow_dispatch 수동 실행
- concurrency: watchlist-data-writes
- night_sector.py 실행 및 sector-night.json 검증
- sector-night.json만 commit/push
- push 전 git pull --rebase
