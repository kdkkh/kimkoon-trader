"""backtest.py 검증 — 합성 봉으로 '다음 봉 시가 체결'과 '포지션 1개' 규칙."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy.backtest import run_backtest  # noqa: E402
from strategy.base import empty_signals  # noqa: E402


def bars(n=8):
    # 시가는 100, 110, 120 … (봉마다 10씩), 종가 = 시가 + 5 → 체결가가 어느 봉 값인지 바로 보인다
    o = [100.0 + 10 * i for i in range(n)]
    return pd.DataFrame({"time": pd.date_range("2026-01-05 09:00", periods=n, freq="min", tz="Asia/Seoul"),
                         "open": o, "high": [x + 8 for x in o], "low": [x - 3 for x in o],
                         "close": [x + 5 for x in o], "volume": 1000.0})


def test_fill_at_next_bar_open():
    df = bars()
    sig = empty_signals(df)
    sig.loc[1, "long_entry"] = True       # 1번 봉 마감 신호 → 2번 봉 시가 120 체결
    sig.loc[4, "long_exit"] = True        # 4번 봉 마감 신호 → 5번 봉 시가 150 체결
    r = run_backtest(df, sig, amount=1000)
    t = r.trades.iloc[0]
    assert t["진입가"] == 120 and t["청산가"] == 150
    assert t["진입시각"].endswith("09:02") and t["청산시각"].endswith("09:05")
    assert t["수량"] == 1000 // 115        # 수량 = 금액 // 신호 봉 종가(115) = 8
    assert t["손익"] == pytest.approx((150 - 120) * 8)
    assert r.summary["닫힌 거래 수"] == 1 and r.pending is None


def test_single_position_no_pyramiding():
    df = bars()
    sig = empty_signals(df)
    sig.loc[[0, 2, 3], "long_entry"] = True   # 들고 있는 동안의 진입 신호(2, 3)는 무시
    sig.loc[[1], "long_exit"] = False
    sig.loc[4, "long_exit"] = True
    sig.loc[6, "long_exit"] = True            # 비어 있을 때 청산 신호는 무시
    r = run_backtest(df, sig, amount=10_000)
    assert len(r.trades) == 1
    t = r.trades.iloc[0]
    assert t["진입가"] == 110 and t["청산가"] == 150
    assert set(r.tv_list["유형"]) == {"진입 롱", "청산 롱"} and len(r.tv_list) == 2


def test_exit_reenter_last_bar_pending():
    df = bars()
    sig = empty_signals(df)
    sig.loc[0, "long_entry"] = True     # → 1번 봉 시가 110
    sig.loc[2, "long_exit"] = True      # → 3번 봉 시가 130
    sig.loc[3, "long_entry"] = True     # 청산 체결된 봉(3) 마감에 다시 진입 → 4번 봉 시가 140
    sig.loc[7, "long_exit"] = True      # 마지막 봉 신호 → 체결 안 됨(대기), 거래는 열린 채 마지막 종가 평가
    r = run_backtest(df, sig, amount=10_000)
    assert list(r.trades["진입가"]) == [110, 140]
    assert r.trades.iloc[0]["청산가"] == 130
    assert r.trades.iloc[1]["상태"].startswith("열림") and r.trades.iloc[1]["청산가"] == 175
    assert r.pending and "청산" in r.pending
    assert r.summary["닫힌 거래 수"] == 1 and r.summary["열린 거래"] == 1


def test_costs_reduce_pnl():
    df = bars()
    sig = empty_signals(df)
    sig.loc[1, "long_entry"] = True
    sig.loc[4, "long_exit"] = True
    base = run_backtest(df, sig, amount=1000).trades.iloc[0]["손익"]
    cost = run_backtest(df, sig, amount=1000, commission_pct=0.1, sell_tax_pct=0.2).trades.iloc[0]
    # 수수료 120*8*0.1% + 150*8*(0.1+0.2)% = 0.96 + 3.6
    assert cost["손익"] == pytest.approx(base - 0.96 - 3.6)
