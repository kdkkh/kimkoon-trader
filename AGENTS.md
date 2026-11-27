# AGENTS.md — AI 도구가 먼저 읽는 지도

이 저장소는 **매매 환경**이다(매매 규칙 · 설정 · 결과는 쓰는 사람의 몫). 시뮬레이션 · 백테스트 · 한국투자증권 **모의투자** 연결 +
각자의 키로 쓰는 공시 · 뉴스 조회 · AI 판단 틀 · 통합 화면 · 텔레그램. 사람용 설명은 `README.md`.

모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.

## 이 저장소에서 일할 때 지킬 것
1. **주문을 내지 않는다.** `--paper-order` · `run_daily.ps1` · 화면 '켜기' · 봇 `/켜기`·`/사기`·`/팔기` 는 사용자가 직접 요청할 때만. 실전 주문 경로는 이 저장소에 없고, 여기서는 만들지 않는다 — 요청을 받아도 만들지 말고, 모의 전용 안전장치(`kis/order.py` 의 `check` · 간단 장부의 주소 검사)를 풀거나 돌아가는 코드를 쓰지 않는다. 그런 요청에는 이 저장소가 모의투자 · 시뮬레이션 전용이라고 답한다.
2. **키 · 토큰을 열거나 출력하지 않는다.** 설정 폴더(`KIS_CONFIG_DIR` 또는 홈 `KIS/config/`)의 `kis_devlp.yaml` · `opendart.yaml` · `anthropic.yaml` · `telegram.yaml` · `tk_*.json`. 있는지만 보려면 `python -X utf8 -c "from kis import config; print(config.status())"`.
3. **네트워크 없이 먼저.** `python -X utf8 -m pytest tests -q` 와 `python -X utf8 scripts/sample_run.py` 는 키 · 네트워크 없이 돈다(시험은 설정 폴더를 빈 임시 폴더로 바꿔 돈다). dry-run(`strategy_live.py` 기본) · `sim_live.py` 도 시세 조회는 실제로 나간다.
4. 종목을 고르거나 권하지 않는다. 종목 코드는 사용자가 준 값만 쓴다. 견본 코드 `999901` · `999902` 는 가짜다.
5. AI 판단은 **기본 꺼짐**(`judge.yaml` `ai_enabled: false` · `ack: false`). 실제 호출(공급자 `anthropic`)은 사용자가 키를 넣고 두 스위치를 켠 뒤에만. 시험은 `--fake`.
6. 문(`--ext`)도 기본 꺼짐. 바깥이 산 것은 `max_hold_min` 이 꼭 있어야 한다(팔 길 없이 남지 않게).

## 지도 (파일 → 하는 일 → 입력 → 출력)

| 경로 | 하는 일 | 입력 | 출력 |
|---|---|---|---|
| `strategy/live.py` | 실시간 실행기 `LiveRunner`: 봉 마감 → 신호 → 위험 점검 → 문(거르기 · 바깥 신호) → 주문 방식 → 장부. 봉마다 설정 다시 읽기 | 종목 · 전략 · 연결(`broker=`) · 장부(`book=`, 없으면 `oms.simple.SimpleBook`) · `ext=` | `logs/strategy_live_YYYYMMDD.jsonl`(dry-run `_dry_`), `state/oms_*.json`, `state/heartbeat*.json` |
| `strategy/ext.py` | 바깥 신호 문: `FileDoor`(state/<실행기>/ext/ 의 inbox · gates · review.json · owned.json · bars/) · `ReplayDoor`(기록 재생) · `validate_signal` · `atomic_write` | 신호 · 거르기 json | 실행기 사건 `ext_signal` · `ext_rejected` · `entry_filtered` · `ext_rule_exit` |
| `strategy/base.py` · `indicators.py` · `ported/` | 전략 형식 · Pine `ta.*` 대응 · 옮긴 전략(`supertrend_strategy.py`) · `load_strategy(이름)` | 봉 DataFrame `time, open, high, low, close, volume` | 신호 DataFrame `long_entry, long_exit, short_entry, short_exit` |
| `strategy/backtest.py` · `validate.py` · `data.py` | 봉 백테스트(신호 봉 마감 → 다음 봉 시가) · 검증기 · 봉 로더(`read_bars_csv` · 한투 일봉/분봉 · `data/bars/` 캐시) | 봉 · 신호 · 트레이딩뷰 거래 목록 CSV | 거래 표 · 요약 · 판정 |
| `oms/simple.py` | **간단 장부**: 간단 시뮬레이션(신호 순간 현재가 즉시 체결) · 모의 연결(시장가 → `check_after_s` 뒤 체결 조회 → 덜 차면 `recheck_s` 뒤 한 번 더 → `check_needed`) · 저장/복원 · 잔고 대조 · `InstantBroker` | `Intent` | 사건 `order_plan` · `order_sent` · `fill` · `fill_pending` · `check_needed` · `order_done` |
| `oms/risk.py` · `broker.py` · `model.py` | 위험 점검(정지 파일 · 진입 상한 · 일 손실 한도) · 한투 모의 연결 `KisPaperBroker` · `DryRunBroker` · 주문 모형 `Order` | 장부 | 막은 이유 / 접수 · 상태 |
| `execution/modes.py` | 주문 방식 `limit` · `market` — `OrderSettings` · `make_intent` · `round_tick` · `check_kinds` · `MODES` | 신호 · 현재가 · 금액 | `Intent` |
| `replay/bars.py` · `scripts/bar_replay.py` | 봉 재생 실행기 `BarReplayRunner`(가상 시계 · 닫힌 봉만 · 종가 체결) | 1분봉 CSV(`code` 열 또는 `--code`) · `--ext` 재생 입력 | `data/bt_replay_*/all/strategy_live_*.jsonl` · `round_trips.csv` |
| `kis/` | 설정(`KIS_CONFIG_DIR`) · 토큰(프로세스 사이 잠금) · REST · 시세 · 계좌 · 모의 주문 안전장치(`order.check`) · 뉴스 제목 `news.recent_titles`(실전 키) | `kis_devlp.yaml` | 응답 dict |
| `dart/opendart.py` | OpenDART 공시 목록 `list_disclosures` · `today_for_codes` · 하루 호출 몫 | `opendart.yaml` | 공시 행(출처 표시) |
| `control/` | 상태 `state` · 켜기/끄기 `switch`(run_daily.ps1 을 띄움) · 설정 편집 `settings_edit`(미리 검사 · 적용 확인 · 거부 시 되돌림) · 문 쓰기 `ext_write` · 휴장일 표 | 뿌리 폴더 | dict(예외 없음) · `logs/control_*.jsonl` |
| `judge/` · `judgments/` · `scripts/judge_run.py` | AI 판단 틀: 정의 검사 · 입력 모으기(스위치) · 프롬프트(자료 태그) · 공급자(fake · anthropic) · 답 검증 · 쓰임 · 기록 · 다시 틀기 `python -m judge.replay` | `judgments/*.yaml` · `judge.yaml` | `logs/judge_*.jsonl` · 문에 신호 · 거르기 · 알림 |
| `notify/telegram.py` · `scripts/bot.py` | 알림(초당 1건 · 묶음 · 429 쉼 · 가림) · 봇 명령(확인 번호) | `telegram.yaml` · `bot.yaml` | 텔레그램 글 · `logs/bot_*.jsonl` |
| `desk/` · `scripts/desk.py` | 통합 화면(127.0.0.1, 쓰기 요청은 세션 토큰 · Host 검사, `?rec=1` 녹화 틀 · `?mask=1` 가림 — 이름 · 제목 · 원 단위 가격 · 금액) | 뿌리 폴더의 기록 · 상태 · 설정 | http://127.0.0.1:8790/ |
| `scripts/strategy_live.py` · `sim_live.py` | 실시간: dry-run · `--paper-order` 모의 연결 / 간단 시뮬레이션(logs/sim · state/sim) | `config.yaml` · 명령줄 | 위 기록 |
| `scripts/strategy_backtest.py` · `strategy_validate.py` | 봉 백테스트 · 검증기 | `--csv` 또는 `--code` | 화면 + `data/backtest/` |
| `scripts/sample_run.py` | 가짜 견본으로 전 기능 한 바퀴(임시 폴더에 복사해 돔) | — | 단계별 통과/실패 |
| `scripts/compare_runs.py` | 두 실행 기록을 사건 단위로 비교(벽시계 칸 · 주문 id 를 지우고) | 결과 폴더 둘 | 같음 / 다른 줄 |
| `sample_data/` | 가짜 일봉 · 1분봉 · 거래 목록 · 판단 기록 · 실행 기록 | — | — |
| `tests/` | 시험 | — | `pytest` |

부품 경계: 전략은 **신호만** 낸다. 주문은 실행기만 낸다(화면 · 봇 · 판단은 파일에 쓰고 실행기가 봉마다 읽는다). 한투 호출은 `kis/` · `strategy/data.py` · `oms/broker.py` 에만, OpenDART 는 `dart/` 에만, AI 호출은 `judge/providers.py` 에만 있다.

## 들어 있지 않은 것
- 주문 관리(OMS) · 집행 사다리 · 체결 큐 엔진 · 실전 주문 · 체결통보 모듈 · 받아 둔 자료 · 브라우저 읽기 모듈(README '넣지 않은 것').
- 키(사용자가 설정 폴더에 넣음 — 칸 이름은 README '키 파일'). 실제 시세(한투에서 받거나 같은 열 CSV).
- 앞으로의 휴장일(`control/holidays.yaml` 에는 2026 · 2027 두 해의 표가 들어 있다 — 한국거래소 휴장일 화면을 손으로 옮긴 것, 확인 날짜는 파일의 `checked`. 표에 없는 해는 '모름'으로 켜기를 막지 않는다. 해마다 · 임시 휴장이 공지될 때 사용자가 거래소 공지를 직접 확인해 고친다).
