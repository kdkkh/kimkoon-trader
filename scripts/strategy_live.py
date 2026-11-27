"""이식 전략 실시간 실행 — 기본은 dry-run(주문 계획만), 모의 주문은 --paper-order 를 줄 때만. 실전 주문 경로는 없다.

실행(이 폴더에서, .venv 파이썬으로):
  python -X utf8 scripts/strategy_live.py --codes <종목코드> --strategy supertrend --once            # 지금 닫힌 봉 한 번만 보고 끝(dry-run)
  python -X utf8 scripts/strategy_live.py --codes <종목코드>,<종목코드> --tf 1 --until 15:20            # 장중 1분봉마다(dry-run)
  python -X utf8 scripts/strategy_live.py --codes <종목코드> --paper-order --amount 500000 --max-orders 3   # 모의 주문
  python -X utf8 scripts/strategy_live.py ... --notice     # 모의 체결통보 연결 시도(HTS ID 필요, 없으면 건너뜀)
옵션:
  --param 이름=값(여러 번) · --tf 봉 분(기본 1) · --lookback 처음 받을 1분봉 수(기본 120) · --settle 봉 마감 뒤 기다릴 초(기본 3)
  --amount 1회 금액(원, 기본 1,000,000 — kis.order.MAX_ORDER_WON(300만 원)을 넘지 않음) · --max-orders 하루 진입(매수) 상한(기본 5, 청산은 상한 밖)
  --data-mode 봉·현재가 조회 키(real 기본 = 실전 키 조회만)
  --ext 바깥 신호 문을 연다(state/ext/, 형식은 strategy/ext.py). 없으면 문을 만들지도 읽지도 않는다(예전과 같음)
  --order-mode limit(기본)/market · --wait 지정가 기다림 상한 초(기본 50) · --loss-limit 일 손실 한도 원(기본 30,000) · --poll 주문 조회 간격 초(기본 2)
멈추기: state/stop 파일을 만들면 새 주문을 멈추고 진행 중 주문을 취소한 뒤 끝낸다(있으면 시작도 안 함).
기록: logs/strategy_live_YYYYMMDD.jsonl (신호·계획·주문·체결통보·오류, 단계별 ms 시각)
종목 코드는 예시이며 추천이 아니다. 신호는 전략 규칙의 기계적 결과일 뿐 매매 권유가 아니다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kis import config  # noqa: E402,F401  (stdout UTF-8 고정)
from execution.modes import MODES  # noqa: E402  (이 판에 있는 주문 방식 — 판마다 다르다)
from strategy import data  # noqa: E402
from strategy.base import parse_params  # noqa: E402
from strategy.live import LiveRunner  # noqa: E402
from strategy.ported import load_strategy  # noqa: E402


DEFAULTS = {"codes": [], "strategy": "supertrend", "params": {}, "tf": 1, "order_mode": "limit",
            "amount": 1_000_000, "wait": 50.0, "max_orders": 5, "loss_limit": 30_000, "start": "09:00",
            "until": "15:20", "poll": 2.0, "settle": 3.0, "lookback": 120, "data_mode": "real",
            "apply_to_open": False, "max_amount": None}
# codes 는 기본값이 없다 — 종목은 쓰는 사람이 설정 파일이나 --codes 로 넣는다.
# max_amount = 1회 금액 상한(원). 비우면 코드의 절대 상한(kis.order.MAX_ORDER_WON). 시작할 때만 읽는다 — 실행 중에 바꾸면
#   '다시 시작해야 반영'으로 남고, 금액(amount)이 이 값을 넘으면 실행기가 시작을 거부한다(깎아서 돌지 않음).


def _check_max_amount(v) -> None:
    from kis.order import MAX_ORDER_WON
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= MAX_ORDER_WON:
        raise SystemExit(f"max_amount 는 0보다 크고 {MAX_ORDER_WON:,}원 이하인 숫자여야 합니다(비우면 {MAX_ORDER_WON:,}원)")


def load_settings(path: Path | None) -> tuple[dict, str]:
    """기본값 ← 설정 파일(config.yaml). 반환: (설정, 읽은 파일 이름 또는 '')."""
    s = dict(DEFAULTS)
    if path is None:
        path = ROOT / "config.yaml"
        if not path.exists():
            return s, ""
    import yaml
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
    unknown = sorted(set(d) - set(DEFAULTS))
    if unknown:
        raise SystemExit(f"설정 파일에 모르는 항목이 있습니다: {', '.join(unknown)}")
    if isinstance(d.get("codes"), str):
        d["codes"] = [c.strip() for c in d["codes"].split(",") if c.strip()]
    codes = d.get("codes", s["codes"])
    if any(not isinstance(c, str) for c in codes):
        # YAML 은 따옴표 없는 코드를 숫자로 읽는다(0 으로 시작하면 8진수로 읽히거나 앞의 0 이 사라짐) → 엉뚱한 종목이 될 수 있어 막는다
        raise SystemExit('종목 코드는 따옴표로 감싸 주세요. 예: codes: ["999901", "999902"] (가짜 코드)')
    d["codes"] = [c.strip().zfill(6) for c in codes]
    _check_max_amount(d.get("max_amount"))
    s.update(d)
    return s, Path(path).name


def main():
    ap = argparse.ArgumentParser(description="이식 전략 실시간 실행(기본 dry-run, 모의 전용). 설정은 config.yaml ← 명령줄 순서로 덮어씀")
    ap.add_argument("--config", type=Path, default=None, help="설정 파일(기본: 이 폴더의 config.yaml 이 있으면 그것)")
    ap.add_argument("--codes", help="쉼표로 여러 개(예: <종목코드>,<종목코드>)")
    ap.add_argument("--strategy")
    ap.add_argument("--param", action="append", default=[], help="전략 값 이름=값(여러 번)")
    ap.add_argument("--tf", type=int, help="봉 분")
    ap.add_argument("--amount", type=float, help="금액 기준 수량: 1회 매수 금액(원)")
    ap.add_argument("--max-orders", type=int, help="하루 진입(매수) 상한 — 청산은 세지 않음")
    ap.add_argument("--lookback", type=int)
    ap.add_argument("--settle", type=float)
    ap.add_argument("--start")
    ap.add_argument("--until")
    ap.add_argument("--once", action="store_true", help="지금 닫힌 봉 한 번만 처리하고 끝")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True, help="주문 계획만(기본)")
    g.add_argument("--paper-order", action="store_true", help="모의 주문을 실제로 보냄")
    ap.add_argument("--notice", action="store_true")
    ap.add_argument("--data-mode", choices=["real", "paper"])
    ap.add_argument("--order-mode", choices=list(MODES),
                    help="limit = 현재가 지정가(기다림 상한 뒤 진입 취소·청산 시장가), market = 시장가")
    ap.add_argument("--wait", type=float, help="지정가 기다림 상한(초)")
    ap.add_argument("--loss-limit", type=float, help="일 손실 한도(원) — 닿으면 그날 새 진입 중단")
    ap.add_argument("--poll", type=float, help="진행 중 주문 상태 조회 간격(초)")
    ap.add_argument("--ext", action="store_true",
                    help="바깥 신호 문을 연다(state/ext/ — AI 판단 · 텔레그램 신호 · 거르기 · 검토). 기본 꺼짐")
    a = ap.parse_args()

    s, cfg_name = load_settings(a.config)
    cli = {"codes": a.codes, "strategy": a.strategy, "tf": a.tf, "amount": a.amount, "max_orders": a.max_orders,
           "lookback": a.lookback, "settle": a.settle, "start": a.start, "until": a.until, "data_mode": a.data_mode,
           "order_mode": a.order_mode, "wait": a.wait, "loss_limit": a.loss_limit, "poll": a.poll}
    for k, v in cli.items():
        if v is not None:
            s[k] = [c.strip() for c in v.split(",") if c.strip()] if k == "codes" else v
    if not s["codes"]:
        raise SystemExit("종목 코드가 없습니다 — config.yaml 의 codes 또는 --codes 로 넣으세요(예: --codes <종목코드>)")
    params = {**(s.get("params") or {}), **parse_params(a.param)}
    strat = load_strategy(s["strategy"], **params)
    mode = "모의 주문(--paper-order)" if a.paper_order else "dry-run(주문 계획만, 실제 주문 없음)"
    print(f"[실시간] {datetime.now():%Y-%m-%d %H:%M:%S}  {mode}  종목 {','.join(s['codes'])} (예시·추천 아님)  "
          f"전략 {strat}  봉 {s['tf']}분" + (f"  설정 {cfg_name}" if cfg_name else ""))
    runner = LiveRunner(s["codes"], strat, tf_minutes=int(s["tf"]), amount=float(s["amount"]),
                        max_orders=int(s["max_orders"]), lookback=int(s["lookback"]), paper_order=a.paper_order,
                        data_mode=s["data_mode"], settle_sec=float(s["settle"]), notice=a.notice,
                        order_mode=s["order_mode"], wait_s=float(s["wait"]), daily_loss_limit=float(s["loss_limit"]),
                        poll_s=float(s["poll"]), notify=not a.once, ext=a.ext, max_amount=s.get("max_amount"))
    print(f"  주문 방식 {s['order_mode']}" + (f"(기다림 {float(s['wait']):.0f}초)" if s["order_mode"] == "limit" else "")
          + f" · 1회 금액 {runner.amount:,.0f}원 · 하루 진입 상한 {s['max_orders']}회(오늘 {runner.entries_today()}회 사용, 청산은 상한 밖)"
          f" · 일 손실 한도 {float(s['loss_limit']):,.0f}원")
    print(f"  이 실행기가 들고 있는 것(체결 기준) {runner.positions or '없음'} · 멈추려면 state/stop 파일을 만드세요")
    print(f"  바깥 신호 문: {'켜짐(state/' + runner.ext.root.name + '/)' if runner.ext else '꺼짐'}"
          f" · 1회 금액 상한 {runner.max_amount:,.0f}원")
    from notify import telegram
    print(f"  기록: logs/{runner.log.path.name} · 텔레그램 알림: {'켜짐' if runner.notifier else telegram.status()}")
    if cfg_name:                                      # 실행 중 설정 바꾸기: 봉마다 설정 파일을 확인(명령줄로 준 값은 파일이 다시 덮어씀)
        runner.watch_settings(a.config or ROOT / "config.yaml", lambda p: load_settings(p)[0], s)
    # 토큰은 시작 때 미리 — 첫 봉에서 여러 실행기가 한꺼번에 발급하다 1분당 1회 제한에 걸린 결함
    from kis import auth
    auth.warm(s["data_mode"])
    if a.paper_order:
        auth.warm("paper")
    runner.run(start=s["start"], until=s["until"], once=a.once)
    print(f"  API 호출 {data.PACER.calls}회 · 신호는 전략 규칙의 기계적 결과이며 매매 권유가 아닙니다.")


if __name__ == "__main__":
    main()
