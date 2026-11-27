# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""봉 재생 백테스트 — 실시간 실행기와 같은 판단 코드를 지난 1분봉 위에서 가상 시계로 돌린다(키 · 네트워크 없음).

실행 예(이 폴더에서):
  python -X utf8 scripts/bar_replay.py --csv sample_data/fake_minute_bars.csv --day 20990105
  ... --ext sample_data/fake_judge_replay.jsonl     기록해 둔 판단(신호 · 거르기)을 가상 시계대로 다시 튼다
  ... --amount 500000 --max-orders 3 --order-mode market --strategy supertrend
판단 기록을 재생 입력으로 바꾸기: python -X utf8 -m judge.replay logs/judge_YYYYMMDD.jsonl --out 판단재현.jsonl
출력: 화면 요약 + --out 폴더(기본 data/bt_replay_<날짜>_<시각>/)에 실행기 기록 all/strategy_live_*.jsonl · round_trips.csv
  (화면 백테스트 탭이 이 폴더를 목록에 보인다). --out 폴더는 비어 있어야 한다(남은 상태를 이어받아 결과가 섞이지 않게).
수수료 · 세금 전. 1분봉 종가로 바로 체결한 값이라 실거래와 다를 수 있다. 과거 자료로 계산한 값이며 미래 결과를 뜻하지 않는다.
지난 날 자료로 AI 에 새로 묻는 것은 검증이 아니다(결과를 아는 모델) — 이 재생은 기록한 답만 다시 튼다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kis import config  # noqa: E402,F401  (stdout UTF-8 고정)
from execution.modes import MODES  # noqa: E402
from replay.bars import BarReplayRunner, read_bars  # noqa: E402
from strategy.base import parse_params  # noqa: E402
from strategy.ported import load_strategy  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="봉 재생 백테스트(가상 시계 · 간단 장부, 키 · 네트워크 없음)")
    ap.add_argument("--csv", type=Path, action="append", required=True, help="1분봉 CSV(여러 번). code 열이 없으면 --code")
    ap.add_argument("--code", default=None, help="CSV 에 code 열이 없을 때 그 종목 코드")
    ap.add_argument("--codes", default="", help="쉼표로(비우면 CSV 의 모든 종목)")
    ap.add_argument("--day", default="", help="YYYYMMDD(비우면 CSV 의 마지막 날). 앞 날짜 봉은 지표 예열로 쓴다")
    ap.add_argument("--strategy", default="supertrend")
    ap.add_argument("--param", action="append", default=[], help="전략 값 이름=값(여러 번)")
    ap.add_argument("--tf", type=int, default=1)
    ap.add_argument("--order-mode", choices=list(MODES), default="limit")
    ap.add_argument("--amount", type=float, default=1_000_000)
    ap.add_argument("--max-amount", type=float, default=None)
    ap.add_argument("--max-orders", type=int, default=5)
    ap.add_argument("--loss-limit", type=float, default=30_000)
    ap.add_argument("--settle", type=float, default=3)
    ap.add_argument("--start", default="09:00")
    ap.add_argument("--until", default="15:20")
    ap.add_argument("--ext", type=Path, default=None, metavar="JSONL",
                    help="기록해 둔 바깥 판단 파일(한 줄 = type signal · gate · review, at_ms) — 주지 않으면 문 꺼짐")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    bars = read_bars(a.csv, a.code)
    codes = [c.strip().zfill(6) for c in a.codes.split(",") if c.strip()] or sorted(bars)
    missing = [c for c in codes if c not in bars]
    if missing:
        print(f"CSV 에 없는 종목: {', '.join(missing)}")
        return 2
    day = a.day or max(b["time"].iloc[-1] for b in bars.values()).strftime("%Y%m%d")
    out = a.out or ROOT / "data" / f"bt_replay_{day}_{datetime.now():%H%M%S}"
    if out.exists() and any(out.iterdir()):
        print(f"--out 폴더가 비어 있지 않습니다: {out.name} — 빈 폴더를 주세요")
        return 2
    strat = load_strategy(a.strategy, **parse_params(a.param))
    print(f"[봉 재생] {day} · 종목 {len(codes)}개 · {a.order_mode} · 전략 {strat}" + (" · 문 켬(기록한 판단)" if a.ext else ""))
    r = BarReplayRunner(codes, strat, bars, day, out_dir=out / "all", ext_replay=a.ext, tf_minutes=a.tf,
                        order_mode=a.order_mode, amount=a.amount, max_amount=a.max_amount, max_orders=a.max_orders,
                        daily_loss_limit=a.loss_limit, settle_sec=a.settle)
    s = r.run_day(a.start, a.until)
    rt = r.round_trips()
    rt.to_csv(out / "round_trips.csv", index=False, encoding="utf-8-sig")
    print(f"  한 번 사고팔기 {len(rt)}건 · 실현 {s['realized']:,.0f}원 · 오늘 진입 {s['entries_today']}회 (수수료 · 세금 전)")
    try:                                                        # 이 프로그램 폴더 안이면 폴더 기준 경로(data/…), 밖이면 폴더 이름만
        shown = out.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        shown = out.name
    print(f"  기록: {shown}/all/{r.log.path.name} · {shown}/round_trips.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
