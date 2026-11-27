"""desk/ — 통합 화면(127.0.0.1 만, 파이썬 내장 서버, 바깥 패키지 없음).

  core/     서버(라우팅 · 세션 토큰 · Host/Origin 검사 · 정적 파일 · 보안 헤더) · 새 줄만 읽기 · 자식 프로세스 · 가림 · 탭 등록표
  static/   껍데기 화면(탭 줄 · 녹화 틀 · 아래 고지 띠) · 공통 css/js · 글꼴(Pretendard, OFL)
  tabs/<id>/ 탭 하나 = 폴더 하나(tab.py + view.html + view.js). 설정(desk.yaml)의 tabs 목록에 이름을 적으면 켜진다.

지키는 것
  - 화면은 증권사 API 를 직접 부르지 않는다. 상태 · 조작은 control 함수로, API 가 필요한 일은 기존 스크립트를 자식 프로세스로.
  - 응답에 계좌 · 주문번호 · 조직번호 · 원응답(raw) · 키 · 토큰 · 사용자 폴더 경로를 넣지 않는다(서버가 마지막에 한 번 더 거른다).
실행: python -X utf8 scripts/desk.py [--port 8790] [--root …] [--config desk.yaml]
"""
