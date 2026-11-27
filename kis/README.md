# kis/ — 한국투자증권 KIS Open API 연결 패키지

모든 모듈은 설정을 `config.load("paper"|"real")` 로 받은 `KisConfig` 하나로 동작한다(기본 모의). 비밀값은 출력하지 않는다.

## 파일

| 파일 | 주요 이름 | 하는 일 |
|---|---|---|
| `config.py` | `load(mode)`, `status()`, `mask_account()`, `KisConfig`(`summary()`·`is_paper`·`tr_prefix`·`product_name`·`token_file`), `CONFIG_DIR` | `~/KIS/config/kis_devlp.yaml` 읽기, 모의/실전 전환, 계좌 가림(끝 2자리 + "종합"). stdout UTF-8 고정 |
| `auth.py` | `get_token(cfg)` | 토큰 캐시(`tk_v.json`·`tk_p.json`, `{"tok","exp"}`) 재사용. **만료 시각이 지났을 때만** 발급, 캐시가 깨지면 멈춤 |
| `client.py` | `get()`, `post()`, `KisError` | REST 공통 헤더·`rt_cd` 확인. 오류엔 `msg_cd`·`msg1` 만 |
| `quote.py` | `current_price()`, `volume_rank()`, `minute_bars_past()` | 시세 조회 |
| `account.py` | `balance()`, `daily_orders()`, `order_status()`, `today_orders()`, `parse_order_row()` | 잔고·주문체결 조회, 주문 하나의 체결 수량·평균가·남은 수량(모의 실제 응답 칸으로 확인) |
| `order.py` | `limit_order()`, `market_order()`, `best_order()`, `cancel_order()`, `check()`, `tick_size()`, `MAX_ORDER_WON`, `DUP_WINDOW_SEC` | **모의 전용** 지정가·시장가 주문·취소 + 안전장치, 기록 `logs/orders.jsonl` |
| `realtime.py` | `approval_key()`, `stream()`, `_sub_msg()`, `CCNL`, `ASP` | 웹소켓 접속키(23시간 캐시 `ws_v.json`·`ws_p.json`)·등록·수신 (공개판에는 없음 — 실시간 시뮬레이션 · 체결통보용) |

## 쓰는 TR (검색용)

| TR / 경로 | 이름 | 실전 | 모의 | 함수 |
|---|---|---|---|---|
| `POST /oauth2/tokenP` | 접근 토큰 | ○ | ○ | `auth._issue` |
| `POST /oauth2/Approval` | 웹소켓 접속키(`secretkey`) | ○ | ○ | `realtime.approval_key` |
| `FHKST01010100` | 주식현재가 시세 | ○ | ○ | `quote.current_price` |
| `FHKST01010200` | 호가·예상체결 | ○ | ○ | (이 프로그램은 쓰지 않음) |
| `FHPST01710000` | 거래량순위 | ○ | 미지원 | `quote.volume_rank` |
| `FHKST03010230` | 주식일별분봉조회(과거 분봉) | ○ | 문서상 미지원·**실측 됨** | `quote.minute_bars_past` |
| `TTTC8434R` / `VTTC8434R` | 주식잔고조회 | ○ | ○ | `account.balance` |
| `TTTC0081R` / `VTTC0081R` | 주식일별주문체결조회(3개월 이내) | ○ | ○ | `account.daily_orders` |
| `TTTC0012U` / `VTTC0012U` | 현금 매수 | 없음(모의만) | ○ | `order.limit_order` · `order.market_order` |
| `TTTC0011U` / `VTTC0011U` | 현금 매도 | 없음(모의만) | ○ | `order.limit_order` · `order.market_order` |
| `TTTC0013U` / `VTTC0013U` | 정정취소(취소만 씀) | 없음(모의만) | ○ | `order.cancel_order` |
| `H0STCNT0` | 실시간 체결가(KRX) | ○ | ○ | `realtime.stream` |
| `H0STASP0` | 실시간 호가(KRX) | ○ | ○ | `realtime.stream` |
| `H0STCNI0` / `H0STCNI9` | 실시간 체결통보(HTS ID) | — | 시도·미수신 | `strategy/live.py` `--notice`(선택) |

## 안전장치 (`order.check`)
- **모의 설정이 아니면 어떤 주문·취소도 보내지 않는다**(실전 주문 경로가 없음).
- 1회 매수 최대 300만 원(코드의 절대 상한 `MAX_ORDER_WON` — 설정 `max_amount` 로 더 낮출 수 있음). 매도는 한도 없음 —
  매도까지 묶으면 산 뒤 값이 올라 금액이 한도를 넘는 순간 청산이 거절돼 포지션이 남는다.
- 같은 주문 60초 안 재요청 차단, 지정가는 호가 단위 검사(현재가는 중간값일 수 있음). 시장가는 추정가로 금액만 검사.
- 주문 상태 추적·취소·재시작 복원·정지 파일·일 손실 한도는 `oms/` 에 있다. 모의 호출 간격 0.5초(공식 예제 `kis_auth.py` 의 모의 값)는 실행기가 `strategy.data.Pacer(0.5)` 로 지킨다.
