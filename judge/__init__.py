"""AI 판단 틀 — 판단 정의(judgments/*.yaml) 하나 = 판단 하나. 실행기와 다른 프로세스(scripts/judge_run.py)에서 돈다.

흐름: 정의 읽기 · 검사(defs) → 언제 묻나(schedule) → 입력 모으기(inputs, 파일 · 각자 키 조회만) → 프롬프트(prompt)
  → 공급자(providers: fake / anthropic) → 답 검증(check, 어긋나면 버림) → 쓰임 넷(outputs: 신호 · 거르기 · 설정 · 알림, 검토 답)
  → 기록(record: logs/judge_YYYYMMDD.jsonl). 전역 상한 · 단가표는 limits, 기록 → 백테스트 재현 입력은 replay.
지키는 선
  - AI 는 주문하지 않는다. 정해 둔 형식의 답(보기 하나 · 점수)만 받고, 쓰임은 control 함수(실행기 문 · 설정 편집)를 거친다.
    주문은 언제나 실행기의 위험 점검을 지난다. 모의 전용(mode: paper_only 만 받음).
  - 프롬프트를 만드는 함수는 입력 모으개가 준 자료만 받는다(설정 · 키 · 계좌 · 토큰 객체를 인자로 받지 않는다).
  - 자료 묶음마다 'AI 로 보냄' 스위치(judge.yaml send). 꺼진 입력을 쓰는 판단은 묻지 않고 이유를 기록한다.
  - 가짜 공급자(fake)의 답은 한투 모의 문에 쓰지 않는다(control 이 기록만 함) — 가짜 응답이 실제 모의 주문이 되지 않게.
"""
