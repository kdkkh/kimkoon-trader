"""단순 백테스트 러너 — 트레이딩뷰 Strategy Tester 기본 규칙에 맞춘다(대조용).

규칙
  1. 신호는 봉 마감에 확정된다(전략의 signals 가 그 봉 값으로 계산).
  2. 체결은 다음 봉 시가 — Pine 기본 process_orders_on_close=false 와 같다. 마지막 봉의 신호는 체결 안 됨(대기로 표시).
  3. 포지션 1개(피라미딩 없음): 비어 있을 때만 long_entry, 들고 있을 때만 long_exit. 같은 봉에서 체결 → 다음 신호 판단 순서.
  4. 수량 = 금액 // 기준가(정수 주). 기준가는 기본 '신호 봉 종가'(Pine 이 주문을 만드는 순간 아는 가격 — 트레이딩뷰가
     현금 기준 수량을 이 값으로 계산하는지는 미확인, qty_basis="fill_open" 으로 바꿔 대조 가능).
  5. 수수료·세금·슬리피지 기본 0(트레이딩뷰 기본과 같게). 실제 비용은 commission_pct(매수·매도 각각, %),
     sell_tax_pct(매도에만, %), slippage_ticks(호가 단위 수 — 매수는 위로·매도는 아래로)로 따로 준다.
출력
  - trades: 거래 1건 = 1줄(진입 시각·가격, 청산 시각·가격, 수량, 손익, 수익률). 열린 거래는 마지막 종가로 평가.
  - tv_list: 트레이딩뷰 '거래 목록'과 비슷한 형식(거래 1건 = 진입·청산 2줄: 거래 번호, 유형, 신호, 시각, 가격, 수량, 손익…)
  - summary: 닫힌 거래 수, 승률, 누적 수익률, 최대 낙폭(종가 기준 평가 자산) 등
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kis.order import tick_size
from strategy.base import validate_bars

ENTRY_NAME, EXIT_NAME = "진입 롱", "청산 롱"


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    tv_list: pd.DataFrame
    summary: dict
    equity: pd.Series
    pending: str | None = None
    notes: list[str] = field(default_factory=list)


def _fmt_time(t: pd.Timestamp, daily: bool) -> str:
    return t.strftime("%Y-%m-%d") if daily else t.strftime("%Y-%m-%d %H:%M")


def _is_daily(times: pd.Series) -> bool:
    if len(times) < 2:
        return True
    return bool((times.diff().dropna() >= pd.Timedelta(hours=20)).mean() > 0.5)


def run_backtest(bars: pd.DataFrame, sig: pd.DataFrame, amount: float = 1_000_000,
                 commission_pct: float = 0.0, sell_tax_pct: float = 0.0, slippage_ticks: int = 0,
                 qty_basis: str = "signal_close", entry_label: str = "long", exit_label: str = "exit") -> BacktestResult:
    df = validate_bars(bars)
    if len(sig) != len(df):
        raise ValueError("신호와 봉의 길이가 다릅니다")
    le = sig["long_entry"].to_numpy(bool)
    lx = sig["long_exit"].to_numpy(bool)
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = df["time"]
    daily = _is_daily(times)
    n = len(df)
    notes: list[str] = []

    pos_qty = 0
    entry = None            # dict: i, price, qty, cost
    pending = None          # ("buy", qty, signal_i) / ("sell", qty, signal_i)
    realized = 0.0
    trades: list[dict] = []
    equity = np.full(n, float(amount))

    def slip(price: float, side: str) -> float:
        if not slippage_ticks:
            return price
        t = tick_size(int(price))
        return price + slippage_ticks * t if side == "buy" else price - slippage_ticks * t

    for i in range(n):
        # 1) 앞 봉에서 만든 주문을 이 봉 시가에 체결
        if pending is not None:
            side, qty, si = pending
            px = slip(o[i], side)
            if side == "buy":
                if qty <= 0 and qty_basis == "fill_open":
                    qty = int(amount // px)
                if qty > 0:
                    fee = px * qty * commission_pct / 100
                    entry = {"i": i, "si": si, "price": px, "qty": qty, "fee": fee}
                    pos_qty = qty
                else:
                    notes.append(f"{_fmt_time(times[i], daily)} 진입 신호였지만 금액 {amount:,.0f}원으로 1주도 못 삼 — 건너뜀")
            else:
                fee = px * pos_qty * (commission_pct + sell_tax_pct) / 100
                pnl = (px - entry["price"]) * pos_qty - entry["fee"] - fee
                realized += pnl
                seg_h = np.r_[h[entry["i"]:i], px].max()
                seg_l = np.r_[l[entry["i"]:i], px].min()
                trades.append({
                    "거래번호": len(trades) + 1, "진입시각": _fmt_time(times[entry["i"]], daily), "진입가": entry["price"],
                    "청산시각": _fmt_time(times[i], daily), "청산가": px, "수량": pos_qty,
                    "진입신호시각": _fmt_time(times[entry["si"]], daily), "청산신호시각": _fmt_time(times[si], daily),
                    "손익": round(pnl, 2), "수익률%": round(pnl / (entry["price"] * pos_qty) * 100, 4),
                    "최대상승": round((seg_h - entry["price"]) * pos_qty, 2),
                    "최대하락": round((seg_l - entry["price"]) * pos_qty, 2),
                    "보유봉수": i - entry["i"], "상태": "닫힘"})
                pos_qty, entry = 0, None
            pending = None
        # 2) 이 봉 마감의 신호로 다음 봉 주문 만들기
        if pos_qty == 0 and le[i]:
            qty = int(amount // c[i]) if qty_basis == "signal_close" else 0
            pending = ("buy", qty, i)
        elif pos_qty > 0 and lx[i]:
            pending = ("sell", pos_qty, i)
        # 3) 종가 기준 평가 자산
        unreal = (c[i] - entry["price"]) * pos_qty - entry["fee"] if entry else 0.0
        equity[i] = amount + realized + unreal

    pending_txt = None
    if pending is not None:
        what = "진입" if pending[0] == "buy" else "청산"
        pending_txt = f"마지막 봉({_fmt_time(times[n - 1], daily)}) {what} 신호 — 다음 봉 시가에 체결될 주문(아직 미체결)"
    if entry is not None:                  # 열린 거래: 마지막 종가로 평가(트레이딩뷰의 'Open' 거래처럼)
        px = c[-1]
        pnl = (px - entry["price"]) * pos_qty - entry["fee"]
        trades.append({
            "거래번호": len(trades) + 1, "진입시각": _fmt_time(times[entry["i"]], daily), "진입가": entry["price"],
            "청산시각": "", "청산가": px, "수량": pos_qty,
            "진입신호시각": _fmt_time(times[entry["si"]], daily), "청산신호시각": "",
            "손익": round(pnl, 2), "수익률%": round(pnl / (entry["price"] * pos_qty) * 100, 4),
            "최대상승": round((h[entry["i"]:].max() - entry["price"]) * pos_qty, 2),
            "최대하락": round((l[entry["i"]:].min() - entry["price"]) * pos_qty, 2),
            "보유봉수": n - 1 - entry["i"], "상태": "열림(마지막 종가 평가)"})

    tr = pd.DataFrame(trades, columns=["거래번호", "진입시각", "진입가", "청산시각", "청산가", "수량", "진입신호시각",
                                       "청산신호시각", "손익", "수익률%", "최대상승", "최대하락", "보유봉수", "상태"])
    eq = pd.Series(equity, index=times, name="평가자산")
    return BacktestResult(tr, _tv_list(tr, entry_label, exit_label), _summary(tr, eq, amount, df, daily, commission_pct,
                                                                               sell_tax_pct, slippage_ticks),
                          eq, pending_txt, notes)


def _tv_list(tr: pd.DataFrame, entry_label: str, exit_label: str) -> pd.DataFrame:
    """트레이딩뷰 '거래 목록'처럼 거래마다 진입·청산 2줄."""
    rows, cum = [], 0.0
    for t in tr.to_dict("records"):
        closed = t["상태"] == "닫힘"
        if closed:
            cum += t["손익"]
        common = {"거래번호": t["거래번호"], "수량": t["수량"], "손익": t["손익"], "손익%": t["수익률%"],
                  "누적손익": round(cum, 2) if closed else "", "최대상승": t["최대상승"], "최대하락": t["최대하락"]}
        rows.append({**common, "유형": ENTRY_NAME, "신호": entry_label, "시각": t["진입시각"], "가격": t["진입가"]})
        rows.append({**common, "유형": EXIT_NAME if closed else f"{EXIT_NAME}(열림)", "신호": exit_label if closed else "Open",
                     "시각": t["청산시각"] if closed else "", "가격": t["청산가"]})
    cols = ["거래번호", "유형", "신호", "시각", "가격", "수량", "손익", "손익%", "누적손익", "최대상승", "최대하락"]
    return pd.DataFrame(rows, columns=cols)


def _summary(tr: pd.DataFrame, eq: pd.Series, amount: float, df: pd.DataFrame, daily: bool,
             commission_pct: float, sell_tax_pct: float, slippage_ticks: int) -> dict:
    closed = tr[tr["상태"] == "닫힘"]
    wins = int((closed["손익"] > 0).sum())
    net = float(closed["손익"].sum())
    comp = float(np.prod(1 + closed["수익률%"].to_numpy() / 100) - 1) if len(closed) else 0.0
    peak = eq.cummax()
    dd = eq - peak
    dd_pct = dd / peak
    open_tr = tr[tr["상태"] != "닫힘"]
    return {
        "기간": f"{_fmt_time(df['time'].iloc[0], daily)} ~ {_fmt_time(df['time'].iloc[-1], daily)}" if len(df) else "",
        "봉 수": len(df),
        "주문 금액(원)": amount,
        "닫힌 거래 수": len(closed),
        "이긴 거래": wins,
        "승률%": round(wins / len(closed) * 100, 2) if len(closed) else math.nan,
        "총 손익(원)": round(net, 2),
        "누적 수익률%(총손익/주문금액)": round(net / amount * 100, 4),
        "복리 수익률%(거래 수익률 곱)": round(comp * 100, 4),
        "평균 거래 수익률%": round(float(closed["수익률%"].mean()), 4) if len(closed) else math.nan,
        "최대 낙폭(원)": round(float(dd.min()), 2) if len(eq) else 0.0,
        "최대 낙폭%": round(float(dd_pct.min()) * 100, 4) if len(eq) else 0.0,
        "열린 거래": len(open_tr),
        "열린 거래 평가손익(원)": round(float(open_tr["손익"].sum()), 2) if len(open_tr) else 0.0,
        "수수료%(편도)": commission_pct, "매도세%": sell_tax_pct, "슬리피지(호가 단위)": slippage_ticks,
    }
