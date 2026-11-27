"""Supertrend strategy — 트레이딩뷰 Pine 전략 이식

[Pine 원본]
- 출처: 트레이딩뷰 내장 전략 "Supertrend strategy"(shorttitle "Supertrend str", 내장 ID STD;Supertrend%Strategy)
- 원본 버전: 9.0 (트레이딩뷰 갱신 2026-09-29), 원문 확인: 2026-11-03 트레이딩뷰 공개 스크립트 저장소
  (pine-facade, scriptAccess=open_no_auth)에서 받아 그대로 옮김 — 원문 확인함
- 원문(그대로):
    //@version=6
    strategy("Supertrend strategy", shorttitle="Supertrend str", overlay=true, default_qty_type=strategy.percent_of_equity, default_qty_value=15)

    atrPeriod = input(10, "ATR Length")
    factor = input.float(3.0, "Factor", step = 0.01)

    [_, direction] = ta.supertrend(factor, atrPeriod)

    if ta.change(direction) < 0
        strategy.entry("My Long Entry Id", strategy.long)

    if ta.change(direction) > 0
        strategy.entry("My Short Entry Id", strategy.short)

    //plot(strategy.equity, title="equity", color=color.red, linewidth=2, style=plot.style_areabr)
- 원본의 전략 속성 기본값(같은 저장소의 컴파일 정보): 초기 자본 100000, 주문 수량 자기자본의 15%, 피라미딩 1,
  수수료 0, 슬리피지 0, process_orders_on_close=false(다음 봉 시가 체결), 통화 NONE

[이식]
- 이식 날짜: 2026-11-03
- 이식한 AI: Claude (Opus 5.5)
- 바꾼 점:
  1. 공매도 없음: 원본은 하락 전환 때 short 진입(= long 을 뒤집어 short)한다. 국내 주식은 공매도를 하지 않으므로
     같은 시점을 long 청산(long_exit)으로만 쓰고 short_entry/short_exit 는 항상 False.
  2. 수량: 원본은 자기자본의 15%. 우리는 러너(backtest/live)가 금액 기준(기본 100만 원)·정수 주로 정한다 — 전략은 신호만.
  3. 체결 시점: 원본과 같다(신호는 봉 마감, 체결은 다음 봉 시가) — 러너가 처리.
"""
from __future__ import annotations

import pandas as pd

from strategy import indicators as ta
from strategy.base import Strategy, empty_signals


class SupertrendStrategy(Strategy):
    name = "supertrend"
    params = {
        "atrPeriod": 10,     # atrPeriod = input(10, "ATR Length")
        "factor": 3.0,       # factor = input.float(3.0, "Factor", step = 0.01)
    }

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = empty_signals(df)

        # [_, direction] = ta.supertrend(factor, atrPeriod)
        supertrend, direction = ta.supertrend(df["high"], df["low"], df["close"], p["factor"], p["atrPeriod"])

        # if ta.change(direction) < 0 → strategy.entry("My Long Entry Id", strategy.long)
        sig["long_entry"] = (ta.change(direction) < 0).astype(bool)
        # if ta.change(direction) > 0 → strategy.entry("My Short Entry Id", strategy.short)
        #   공매도 없음 → 같은 시점에 long 청산만 (바꾼 점 1)
        sig["long_exit"] = (ta.change(direction) > 0).astype(bool)

        # 대조용 지표 열(러너는 보지 않음) — 트레이딩뷰 데이터 창의 Supertrend 값과 비교
        sig["supertrend"] = supertrend
        sig["direction"] = direction
        return sig


STRATEGY = SupertrendStrategy

# ── 자기 점검 표 ────────────────────────────────────────────────────────────
# [사용한 Pine 함수 → 대응 함수]
#   input(10, "ATR Length")                    → params["atrPeriod"] = 10
#   input.float(3.0, "Factor", step = 0.01)    → params["factor"] = 3.0
#   [_, direction] = ta.supertrend(f, n)       → supertrend, direction = ta.supertrend(df["high"], df["low"], df["close"], f, n)
#   ta.change(direction)                       → ta.change(direction)
#   strategy.entry(id, strategy.long)          → sig["long_entry"]
#   strategy.entry(id, strategy.short)         → sig["long_exit"] (공매도 없음 — 바꾼 점 1)
#   strategy(... default_qty_type, default_qty_value) → 러너 인자 --amount (바꾼 점 2)
# [미지원 항목]
#   없음 (//plot(strategy.equity ...) 는 원본에서도 주석이라 옮기지 않음)
# [의도적으로 바꾼 점]
#   1. short 진입 → long 청산만   2. 수량 15% → 금액 기준 정수 주
