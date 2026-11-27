"""이식 전략 백테스트 — 한투 봉(캐시 우선)으로 돌려 트레이딩뷰 Strategy Tester 거래 목록과 대조할 표를 만든다.

실행(이 폴더에서, .venv 파이썬으로):
  python -X utf8 scripts/strategy_backtest.py --code <종목코드> --tf 1d --days 365 --strategy supertrend --param atrPeriod=10 --param factor=3
  python -X utf8 scripts/strategy_backtest.py --code <종목코드> --tf 1d --no-fetch            # 캐시(data/bars/)만, API 호출 없음
  python -X utf8 scripts/strategy_backtest.py --code <종목코드> --tf 1m --date 20261031      # 과거 하루 1분봉(120봉씩 거꾸로 받음)
  python -X utf8 scripts/strategy_backtest.py ... --commission-pct 0.015 --sell-tax-pct 0.20 --slippage-ticks 1   # 비용을 넣은 예(율은 계좌 · 해마다 다름)
옵션:
  --csv       봉 CSV 파일(열 time, open, high, low, close, volume). 주면 --code · 한투 조회를 쓰지 않는다(API 호출 없음).
  --code      종목코드 6자리(기본값 없음). --csv 도 --code 도 없으면 사용법 오류로 끝난다.
  --strategy  이름(supertrend) 또는 파일 경로(strategy/ported/xxx.py)
  --amount    한 번 진입 금액(원, 기본 1,000,000) — 수량 = 금액 // 신호 봉 종가(정수 주)
  --mode      봉 조회 키(real 기본 = 실전 키 조회만, paper) — 이 스크립트에는 주문 코드가 없다
  --keep-today 오늘 일봉이 아직 안 끝났어도 넣기(기본은 20:05 전이면 뺌)
  --dump      봉·지표·신호를 CSV 로 함께 저장(트레이딩뷰 데이터 창 값과 봉별 대조용)
출력: 화면 요약 + data/backtest/{code}_{tf}_{전략}_{시각}_거래목록.csv(트레이딩뷰 형식) · _거래.csv(1건 1줄) · _요약.json
비용 기본 0 은 트레이딩뷰 기본 속성과 맞춘 대조용 값이다. 종목 코드는 쓰는 사람이 넣는다(이 프로그램은 종목을 고르거나 권하지 않는다).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kis import config  # noqa: E402,F401  (stdout UTF-8 고정)
from strategy import data  # noqa: E402
from strategy.backtest import run_backtest  # noqa: E402
from strategy.base import parse_params  # noqa: E402
from strategy.ported import load_strategy  # noqa: E402

OUT = ROOT / "data" / "backtest"
NOTICE = "※ 종목 코드는 예시이며 추천이 아닙니다. 과거 결과는 앞으로의 수익을 보장하지 않습니다."


def load_bars(a) -> "data.pd.DataFrame":
    if a.csv:
        return data.read_bars_csv(a.csv)
    kind, minutes = data.parse_tf(a.tf)
    if kind == "d":
        df = data.load_daily(a.code, a.days, a.mode, refresh=a.refresh, fetch=not a.no_fetch)
        if not a.keep_today:
            df, dropped = data.drop_unfinished_daily(df)
            if dropped:
                print("  오늘 일봉은 아직 끝나지 않아 뺐습니다(애프터마켓 20:00 끝난 뒤 확정, --keep-today 로 넣기)")
        return df
    # 분봉: --date 하루(과거 분봉 120봉씩 거꾸로) + 캐시
    df = data.read_cache(a.code, "1m")
    if a.date and not a.no_fetch:
        parts, hour = [], "200000"
        for _ in range(a.max_calls):
            part = data.fetch_minute_past(a.code, a.date, hour, a.mode)
            if part.empty:
                break
            parts.append(part)
            first = part["time"].iloc[0]
            if first.strftime("%H%M") <= "0900":
                break
            hour = (first - timedelta(minutes=1)).strftime("%H%M") + "59"
        if parts:
            df = data.merge_save(a.code, "1m", data.pd.concat(parts))
    if a.date:
        df = df[df["time"].dt.strftime("%Y%m%d") == a.date]
    return data.resample_minutes(df.reset_index(drop=True), minutes)


def main():
    ap = argparse.ArgumentParser(description="이식 전략 백테스트(트레이딩뷰 대조용)")
    ap.add_argument("--csv", help="봉 CSV 파일(네트워크 없이)")
    ap.add_argument("--code", help="종목코드 6자리(쓰는 사람이 넣는다 — 기본값 없음). --csv 를 주면 쓰지 않는다")
    ap.add_argument("--tf", default="1d", help="1d 또는 1m·5m·15m…")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--date", help="분봉일 때 날짜 YYYYMMDD")
    ap.add_argument("--strategy", default="supertrend")
    ap.add_argument("--param", action="append", default=[], help="이름=값 (여러 번)")
    ap.add_argument("--amount", type=float, default=1_000_000)
    ap.add_argument("--commission-pct", type=float, default=0.0)
    ap.add_argument("--sell-tax-pct", type=float, default=0.0)
    ap.add_argument("--slippage-ticks", type=int, default=0)
    ap.add_argument("--qty-basis", choices=["signal_close", "fill_open"], default="signal_close")
    ap.add_argument("--mode", choices=["real", "paper"], default="real")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="캐시 무시하고 다시 받기")
    ap.add_argument("--keep-today", action="store_true")
    ap.add_argument("--max-calls", type=int, default=6, help="분봉 하루 받을 때 최대 호출 수")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--brief", action="store_true", help="화면용: 거래 목록은 진입·청산 열만, 요약은 기간·봉 수·거래 수만(손익·수익률 출력 안 함)")
    a = ap.parse_args()
    if not a.csv and not a.code:
        ap.error("종목 코드(--code) 또는 봉 파일(--csv)을 주세요")

    strat = load_strategy(a.strategy, **parse_params(a.param))
    label, tf_label = (Path(a.csv).stem, "csv") if a.csv else (a.code, a.tf)
    print(f"[백테스트] {label} (예시·추천 아님)  봉 {tf_label}  전략 {strat}")
    bars = load_bars(a)
    if bars.empty:
        print("  봉이 없습니다")
        return
    print(f"  봉 {len(bars)}개  {bars['time'].iloc[0]:%Y-%m-%d %H:%M} ~ {bars['time'].iloc[-1]:%Y-%m-%d %H:%M}"
          f"  (API 호출 {data.PACER.calls}회)")
    sig = strat.run(bars)
    r = run_backtest(bars, sig, amount=a.amount, commission_pct=a.commission_pct, sell_tax_pct=a.sell_tax_pct,
                     slippage_ticks=a.slippage_ticks, qty_basis=a.qty_basis)

    print(f"  신호: 진입 {int(sig['long_entry'].sum())}번 · 청산 {int(sig['long_exit'].sum())}번 (봉 마감 기준)")
    print("\n[거래 목록] 체결 = 신호 다음 봉 시가")
    cols = ["거래번호", "진입시각", "진입가", "청산시각", "청산가", "상태"] if a.brief else ["거래번호", "진입시각", "진입가", "청산시각", "청산가", "수량", "손익", "수익률%", "상태"]
    show = r.trades[cols]
    print(show.to_string(index=False) if len(show) else "  거래 없음")
    print("\n[요약]")
    for k, v in r.summary.items():
        if a.brief and k not in ("기간", "봉 수", "닫힌 거래 수"):
            continue
        print(f"  {k}: {v:,}" if isinstance(v, (int, float)) and not isinstance(v, bool) else f"  {k}: {v}")
    if r.pending:
        print(f"  대기: {r.pending}")
    for n in r.notes:
        print(f"  참고: {n}")

    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"{label}_{tf_label}_{strat.name}_{datetime.now():%Y%m%d_%H%M%S}"
    r.tv_list.to_csv(OUT / f"{stem}_거래목록.csv", index=False, encoding="utf-8-sig")
    r.trades.to_csv(OUT / f"{stem}_거래.csv", index=False, encoding="utf-8-sig")
    (OUT / f"{stem}_요약.json").write_text(json.dumps(
        {"code": label, "tf": tf_label, "strategy": strat.name, "params": strat.params, "summary": r.summary,
         "pending": r.pending, "api_calls": data.PACER.calls}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if a.dump:
        dump = data.pd.concat([bars, sig.drop(columns=[]).reset_index(drop=True)], axis=1)
        dump.to_csv(OUT / f"{stem}_봉신호.csv", index=False, encoding="utf-8-sig")
    print(f"\n  저장: data/backtest/{stem}_거래목록.csv (트레이딩뷰 형식) · _거래.csv · _요약.json{' · _봉신호.csv' if a.dump else ''}")
    print(f"\n{NOTICE}")


if __name__ == "__main__":
    main()
