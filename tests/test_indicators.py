"""indicators.py 검증 — 손으로 계산한 작은 수열 (계산 과정은 각 테스트 주석).

실행: python -X utf8 -m pytest tests -q   (이 프로그램 폴더에서)
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import indicators as ta  # noqa: E402

S = pd.Series


def close_list(s, expected):
    got = list(s)
    assert len(got) == len(expected)
    for g, e in zip(got, expected):
        if e is None:
            assert math.isnan(g), f"na 여야 함: {got}"
        else:
            assert g == pytest.approx(e, abs=1e-9), f"{got} != {expected}"


def test_sma():
    # (1+2+3)/3=2, (2+3+4)/3=3, (3+4+5)/3=4
    close_list(ta.sma(S([1, 2, 3, 4, 5]), 3), [None, None, 2, 3, 4])


def test_rma_sma_seed():
    # 첫 값 = sma(1,2,3)=2 / 다음 = 4/3 + 2/3*2 = 8/3 / 다음 = 5/3 + 2/3*8/3 = 31/9
    close_list(ta.rma(S([1, 2, 3, 4, 5]), 3), [None, None, 2, 8 / 3, 31 / 9])


def test_ema_doc_seed_first():
    # v6 문서 예시: 첫 값 = 첫 source. alpha = 2/(3+1) = 0.5
    # 1 → 0.5*2+0.5*1=1.5 → 0.5*3+0.5*1.5=2.25 → 3.125 → 4.0625
    close_list(ta.ema(S([1, 2, 3, 4, 5]), 3), [1, 1.5, 2.25, 3.125, 4.0625])


def test_ema_sma_seed_option():
    # seed="sma": 첫 값 = sma(1,2,3)=2 → 0.5*4+0.5*2=3 → 0.5*5+0.5*3=4
    close_list(ta.ema(S([1, 2, 3, 4, 5]), 3, seed="sma"), [None, None, 2, 3, 4])


# 손 계산용 봉 7개
H = S([10, 12, 13, 12, 15, 14, 11], dtype=float)
L = S([8, 9, 11, 9, 14, 10, 8], dtype=float)
C = S([9, 11, 12, 10, 14.5, 10.5, 8.5], dtype=float)


def test_tr_first_bar_high_minus_low():
    # tr0 = 10-8 = 2 (앞 종가 없음 → high-low)
    # tr1 = max(3, |12-9|, |9-9|) = 3 / tr2 = max(2, 2, 0) = 2 / tr3 = max(3, 0, |9-12|) = 3
    # tr4 = max(1, |15-10|=5, |14-10|=4) = 5 (갭) / tr5 = max(4, 0.5, 4.5) = 4.5 / tr6 = max(3, 0.5, 2.5) = 3
    close_list(ta.tr(H, L, C), [2, 3, 2, 3, 5, 4.5, 3])
    assert math.isnan(ta.tr(H, L, C, handle_na=False).iloc[0])


def test_atr_is_rma_of_tr():
    # n=2: 첫 값 (2+3)/2=2.5 → 0.5*2+0.5*2.5=2.25 → 0.5*3+0.5*2.25=2.625
    #      → 0.5*5+0.5*2.625=3.8125 → 0.5*4.5+0.5*3.8125=4.15625 → 0.5*3+0.5*4.15625=3.578125
    close_list(ta.atr(H, L, C, 2), [None, 2.5, 2.25, 2.625, 3.8125, 4.15625, 3.578125])


def test_supertrend_hand_calc():
    # factor=1, atrPeriod=2, hl2 = [9, 10.5, 12, 10.5, 14.5, 12, 9.5]
    # 위 띠(원래) = hl2 + atr = [-, 13, 14.25, 13.125, 18.3125, 16.15625, 13.078125]
    # 아래 띠(원래) = hl2 - atr = [-, 8, 9.75, 7.875, 10.6875, 7.84375, 5.921875]
    # i0: atr 없음 → 띠는 nz(앞)=0, direction 1, 선 = 위 띠 0
    # i1: 아래 8(>0), 위 13(앞 종가 9 > 0), atr[0] 없음 → direction 1, 선 13
    # i2: 아래 9.75(>8), 위 14.25<13 아님·앞 종가 11>13 아님 → 13 유지. 앞 선==앞 위 띠 → 종가 12>13? 아니오 → 1, 선 13
    # i3: 아래 7.875<9.75·앞 종가 12<9.75 아님 → 9.75 유지, 위 13 유지. 종가 10>13? 아니오 → 1, 선 13
    # i4: 아래 10.6875(>9.75), 위 13 유지. 종가 14.5>13 → -1(상승 전환), 선 = 아래 띠 10.6875
    # i5: 아래 7.84375 → 10.6875 유지, 위 16.15625(앞 종가 14.5>13). 앞 선은 아래 띠 → 종가 10.5<10.6875 → 1, 선 16.15625
    # i6: 아래 5.921875(앞 종가 10.5<10.6875 → 새 값), 위 13.078125(<16.15625). 앞 선==앞 위 띠 → 8.5>13.08? 아니오 → 1
    st, d = ta.supertrend(H, L, C, 1.0, 2)
    assert list(d) == [1, 1, 1, 1, -1, 1, 1]
    close_list(st, [0, 13, 13, 13, 10.6875, 16.15625, 13.078125])


def test_supertrend_strategy_signals():
    from strategy.ported.supertrend_strategy import SupertrendStrategy
    df = pd.DataFrame({"time": pd.date_range("2026-01-01", periods=7, freq="D"),
                       "open": C, "high": H, "low": L, "close": C, "volume": 1.0})
    sig = SupertrendStrategy(atrPeriod=2, factor=1.0).run(df)
    # direction [1,1,1,1,-1,1,1] → change <0 은 i4(진입), >0 은 i5(청산)
    assert list(sig.index[sig["long_entry"]]) == [4]
    assert list(sig.index[sig["long_exit"]]) == [5]
    assert not sig["short_entry"].any() and not sig["short_exit"].any()


def test_crossover_crossunder():
    a = S([1, 2, 3, 2, 1], dtype=float)
    # i2: 3>2 이고 앞 봉 2<=2 → True. i1: 2>2 아님
    assert list(ta.crossover(a, 2)) == [False, False, True, False, False]
    # i4: 1<2 이고 앞 봉 2>=2 → True. i3: 2<2 아님
    assert list(ta.crossunder(a, 2)) == [False, False, False, False, True]
    b = S([2, 2, 2, 2, 2], dtype=float)
    assert list(ta.crossover(a, b)) == [False, False, True, False, False]


def test_highest_lowest():
    s = S([3, 1, 4, 1, 5], dtype=float)
    close_list(ta.highest(s, 3), [None, None, 4, 4, 5])
    close_list(ta.lowest(s, 3), [None, None, 1, 1, 1])


def test_rsi():
    # x=[1,2,3,2,3], n=2 → 오름 u=[na,1,1,0,1], 내림 d=[na,0,0,1,0]
    # rma(u)=[-,-,1,0.5,0.75], rma(d)=[-,-,0,0.5,0.25] → RSI i2: 내림 0 → 100, i3: rs=1 → 50, i4: rs=3 → 75
    close_list(ta.rsi(S([1, 2, 3, 2, 3], dtype=float), 2), [None, None, 100, 50, 75])


def test_no_external_indicator_library():
    src = (Path(ta.__file__)).read_text(encoding="utf-8")
    assert "pandas_ta" not in src and "talib" not in src
