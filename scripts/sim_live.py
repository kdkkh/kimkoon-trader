# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""간단 시뮬레이션 실시간 실행 — 실시간 시세로 봉 · 신호를 보고, 주문은 어디에도 보내지 않고 신호 순간 현재가로 바로 체결된 것으로 적는다.

실행(이 폴더에서):
  python -X utf8 scripts/sim_live.py                 config.yaml 설정으로 장중 간단 시뮬레이션
  python -X utf8 scripts/sim_live.py --once          지금 닫힌 봉 한 번
  python -X utf8 scripts/sim_live.py --ext           바깥 신호 문을 연다(state/sim/ext/, 기본 꺼짐)
설정은 strategy_live.py 와 같은 config.yaml 을 읽는다(명령줄이 우선). 기록 logs/sim/, 상태 state/sim/(정지 파일 state/sim/stop).
봉 · 현재가는 한투 조회(실전 키 · 조회만)라 설정 파일(kis_devlp.yaml)이 필요하다. 키 없이 돌리는 길은 scripts/bar_replay.py(봉 재생).
바로 체결되는 시뮬레이션이라 호가 · 체결 지연 · 일부 체결이 없다 — 결과는 실거래와 다를 수 있다.
종목 코드는 쓰는 사람이 넣는다. 신호는 전략 규칙의 기계적 결과이며 매매 권유가 아니다.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oms.simple import InstantBroker, SimpleBook  # noqa: E402
from strategy import data, live  # noqa: E402
from strategy.ported import load_strategy  # noqa: E402

_spec = importlib.util.spec_from_file_location("strategy_live_settings", ROOT / "scripts" / "strategy_live.py")
_sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sl)


def main() -> int:
    ap = argparse.ArgumentParser(description="간단 시뮬레이션(주문 안 보냄 · 신호 순간 현재가로 바로 체결)")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--codes")
    ap.add_argument("--order-mode", choices=list(_sl.MODES))
    ap.add_argument("--until")
    ap.add_argument("--start")
    ap.add_argument("--max-orders", type=int)
    ap.add_argument("--tag", default="", help="따로 저장할 이름(logs/sim_<tag>/, state/sim_<tag>/)")
    ap.add_argument("--amount", type=float)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--ext", action="store_true", help="바깥 신호 문을 연다(state/sim/ext/ — 모의 실행기의 문과 따로). 기본 꺼짐")
    a = ap.parse_args()
    s, cfg_name = _sl.load_settings(a.config)
    if a.codes:
        s["codes"] = [c.strip().zfill(6) for c in a.codes.split(",") if c.strip()]
    for k, v in (("order_mode", a.order_mode), ("until", a.until), ("start", a.start), ("max_orders", a.max_orders),
                 ("amount", a.amount)):
        if v is not None:
            s[k] = v
    if a.once and not a.tag:
        a.tag = "once"                                # 한 번 시험은 그날 기록과 섞이지 않게
    sub = "sim" + (f"_{a.tag}" if a.tag else "")
    live.LOG_DIR, live.STATE_DIR = ROOT / "logs" / sub, ROOT / "state" / sub     # 이 프로세스 안에서만
    live.LOG_DIR.mkdir(parents=True, exist_ok=True)
    live.STATE_DIR.mkdir(parents=True, exist_ok=True)
    strat = load_strategy(s["strategy"], **(s.get("params") or {}))
    print(f"[간단 시뮬레이션] {datetime.now():%Y-%m-%d %H:%M:%S}  주문 안 보냄 · 신호 순간 현재가로 바로 체결  "
          f"종목 {','.join(s['codes'])}  전략 {strat}" + (f"  설정 {cfg_name}" if cfg_name else ""))
    r = live.LiveRunner(s["codes"], strat, tf_minutes=int(s["tf"]), amount=float(s["amount"]),
                        max_orders=int(s["max_orders"]), lookback=int(s["lookback"]), data_mode=s["data_mode"],
                        settle_sec=float(s["settle"]), order_mode=s["order_mode"], wait_s=float(s["wait"]),
                        daily_loss_limit=float(s["loss_limit"]), poll_s=float(s["poll"]), broker=InstantBroker(),
                        book=SimpleBook, ext=a.ext, max_amount=s.get("max_amount"))
    print(f"  기록 logs/{sub}/{r.log.path.name} · 멈추기 state/{sub}/stop"
          f" · 바깥 신호 문 {'state/' + sub + '/ext/' if r.ext else '꺼짐'}")
    if cfg_name:
        r.watch_settings(a.config or ROOT / "config.yaml", lambda p: _sl.load_settings(p)[0], s)
    from kis import auth
    auth.warm(s["data_mode"])                         # 토큰은 시작 때 미리(다른 실행기와 첫 봉에 함께 발급하다 막히지 않게)
    r.run(start=s["start"], until=s["until"], once=a.once)
    print(f"  API 호출 {data.PACER.calls}회 · 신호는 전략 규칙의 기계적 결과이며 매매 권유가 아닙니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
