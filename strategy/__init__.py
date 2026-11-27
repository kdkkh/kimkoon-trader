"""strategy/ — 트레이딩뷰 Pine 전략을 파이썬으로 옮겨 꽂는 틀(유튜브 6편에서 만듦).

- base.py        전략 공통 형식(Strategy 클래스·입력 봉 검사·신호 열)
- indicators.py  Pine ta.* 대응 함수(공식 문서 정의대로)
- ported/        사람(클로드 등 AI)이 전략마다 한 번 옮긴 결과
- data.py        한투 봉 로더(일봉·당일 분봉·과거 분봉, data/bars/ 캐시)
- backtest.py    트레이딩뷰 Strategy Tester 기본 규칙에 맞춘 단순 백테스트
- live.py        실시간 실행기(기본 --dry-run, 모의 주문은 --paper-order 일 때만)
자세한 설명은 strategy/README.md.
"""
