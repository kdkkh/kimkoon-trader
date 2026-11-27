"""이식 전략 검증기 — 어떤 AI(클로드·GPT·제미나이…)가 옮긴 파일이든 같은 기준으로 검사한다. 통과한 파일만 쓴다.

실행(이 폴더에서, .venv 파이썬으로):
  python -X utf8 scripts/strategy_validate.py strategy/ported/supertrend_strategy.py            # ①②③ (합성 봉, API 호출 없음)
  python -X utf8 scripts/strategy_validate.py supertrend --code <종목코드> --tf 1d --days 365      # 실제 봉도(캐시 우선)
  python -X utf8 scripts/strategy_validate.py supertrend --code <종목코드> --tf 1d --days 365 --tv-trades 내려받은_거래목록.csv
  python -X utf8 scripts/strategy_validate.py my.py --tv-trades x.csv --tv-cols "trade=Trade #,type=Type,time=Date/Time,price=Price KRW"
단계: ① 규격(import·Strategy 상속·params dict·signals 열/길이/bool·short=False)
      ② 금지(외부 지표 라이브러리·네트워크·주문 import 금지, 대응표 밖 ta 함수 경고)
      ③ 견본 봉 실행(합성 봉 + 실제 봉) ④ --tv-trades 가 있으면 트레이딩뷰 거래 목록 대조 ⑤ 판정 한 줄
옵션: --csv 봉 CSV 파일(네트워크 없이) · --param 이름=값 · --amount · --warmup 대조에서 뺄 앞 봉 수(기본: 일봉 0, 지표 예열이 걱정되면 50 등)
      --no-fetch 캐시만(API 호출 없음) · --mode 봉 조회 키(real 기본, 조회만)
트레이딩뷰 거래 목록 CSV: Strategy Tester → 거래 목록(List of trades) → 내보내기. 차트 시간대를 Asia/Seoul 로 두고 내보낼 것.
종목 코드는 예시이며 추천이 아니다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kis import config  # noqa: E402,F401  (stdout UTF-8 고정)
from strategy.base import parse_params  # noqa: E402
from strategy.validate import parse_tv_cols, validate  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="이식 전략 검증기")
    ap.add_argument("target", help="전략 파일 경로 또는 이름")
    ap.add_argument("--csv", help="실제 봉 대신 쓸 봉 CSV 파일(네트워크 없이)")
    ap.add_argument("--code", help="실제 봉 종목코드(예시, 추천 아님)")
    ap.add_argument("--tf", default="1d")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--date", help="분봉일 때 날짜 YYYYMMDD(캐시에서)")
    ap.add_argument("--param", action="append", default=[])
    ap.add_argument("--tv-trades", help="트레이딩뷰 거래 목록 CSV")
    ap.add_argument("--tv-cols", help='열 이름 지정 "trade=…,type=…,time=…,price=…"')
    ap.add_argument("--amount", type=float, default=1_000_000)
    ap.add_argument("--warmup", type=int, default=None)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--mode", choices=["real", "paper"], default="real")
    ap.add_argument("--keep-today", action="store_true")
    a = ap.parse_args()

    bars, tf_min = None, 1
    if a.csv or a.code:
        from strategy import data
        kind, tf_min = data.parse_tf(a.tf)
        if a.csv:
            bars = data.read_bars_csv(a.csv)
        elif kind == "d":
            bars = data.load_daily(a.code, a.days, a.mode, fetch=not a.no_fetch, verbose=True)
            if not a.keep_today:
                bars, _ = data.drop_unfinished_daily(bars)
        else:
            bars = data.read_cache(a.code, "1m")
            bars = bars[bars["volume"] > 0]
            if a.date:
                bars = bars[bars["time"].dt.strftime("%Y%m%d") == a.date]
            bars = data.resample_minutes(bars.reset_index(drop=True), tf_min)
        print(f"[봉] {Path(a.csv).name if a.csv else a.code} (예시·추천 아님) {a.tf} {len(bars)}개")

    print(f"[검증] {a.target}")
    rep = validate(a.target, bars=bars, params=parse_params(a.param), tv_trades=a.tv_trades,
                   tv_cols=parse_tv_cols(a.tv_cols), amount=a.amount, tf_minutes=tf_min, warmup_bars=a.warmup)
    print("\n".join(rep.lines))
    extra = f" · 경고 {len(rep.warns)}" if rep.warns else ""
    extra += f" · 실패 {len(rep.fails)}" if rep.fails else ""
    print(f"⑤ 판정: {rep.verdict}{extra}")
    sys.exit(1 if rep.verdict == "실패" else 0)


if __name__ == "__main__":
    main()
