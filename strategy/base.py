"""전략 공통 형식 — 모든 이식 전략은 이 형식을 따른다.

입력(봉): pandas DataFrame, 열 = time, open, high, low, close, volume
  - time: KST(Asia/Seoul) tz-aware 또는 tz 없는 KST, 오름차순, 중복 없음
  - 봉 시각은 '봉이 시작한 시각'(한투 분봉 "14:25" = 14:25:00~14:25:59, 트레이딩뷰도 시작 시각으로 표시)
출력(신호): 입력과 같은 길이·같은 index 의 DataFrame
  - long_entry, long_exit, short_entry, short_exit (bool)
  - 국내 주식은 공매도를 하지 않으므로 short_* 는 틀만 두고 항상 False
  - 지표 값(예: supertrend, direction) 같은 열을 더 붙여도 된다 — 러너는 위 4개 열만 본다
신호의 뜻: '이 봉이 닫힌 순간 조건이 맞았다'. 체결 시점(다음 봉 시가)은 러너(backtest/live)가 정한다.

전략 만드는 법:
    class MyStrategy(Strategy):
        name = "my"
        params = {"length": 14}          # Pine input 기본값 그대로
        def signals(self, df):
            sig = empty_signals(df)
            ...
            return sig

이식 전략 파일 머리말에 적을 것(ported/ 의 파일 docstring):
  - Pine 원본: 어디서 왔는지(내장 스크립트 이름·공개 스크립트 주소·버전)
  - 이식 날짜, 이식한 사람/AI
  - 이식 때 바꾼 점(예: short 끔, 수량 규칙 다름)
"""
from __future__ import annotations

import pandas as pd

BAR_COLS = ["time", "open", "high", "low", "close", "volume"]
SIGNAL_COLS = ["long_entry", "long_exit", "short_entry", "short_exit"]
KST = "Asia/Seoul"


class Strategy:
    """이식 전략의 부모 클래스. 자식은 name·params·signals 만 구현한다."""

    name: str = "base"
    params: dict = {}

    def __init__(self, **overrides):
        unknown = set(overrides) - set(type(self).params)
        if unknown:
            raise ValueError(f"[{self.name}] 모르는 파라미터: {', '.join(sorted(unknown))} "
                             f"(쓸 수 있는 것: {', '.join(type(self).params) or '없음'})")
        # 클래스의 기본값은 그대로 두고, 인스턴스마다 덮어쓴 사본을 쓴다
        self.params = {**type(self).params, **overrides}

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """입력 봉을 검사하고 signals 를 돌린 뒤 출력 형식을 검사한다."""
        bars = validate_bars(df)
        sig = self.signals(bars)
        return validate_signals(sig, bars)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({', '.join(f'{k}={v}' for k, v in self.params.items())})"


def validate_bars(df: pd.DataFrame) -> pd.DataFrame:
    """입력 봉 형식 검사. index 는 0..n-1 로 다시 매긴다(신호와 같은 index)."""
    missing = [c for c in BAR_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"봉에 열이 없습니다: {', '.join(missing)}")
    out = df[BAR_COLS].copy().reset_index(drop=True)
    out["time"] = pd.to_datetime(out["time"])
    if out["time"].duplicated().any():
        raise ValueError("봉 시각이 중복됩니다")
    if not out["time"].is_monotonic_increasing:
        raise ValueError("봉 시각은 오름차순이어야 합니다")
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = pd.to_numeric(out[c], errors="raise").astype(float)
    return out


def empty_signals(df: pd.DataFrame) -> pd.DataFrame:
    """모든 신호가 False 인 출력 틀."""
    return pd.DataFrame({c: False for c in SIGNAL_COLS}, index=df.index)


def validate_signals(sig: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(sig, pd.DataFrame):
        raise TypeError("signals() 는 DataFrame 을 돌려줘야 합니다")
    missing = [c for c in SIGNAL_COLS if c not in sig.columns]
    if missing:
        raise ValueError(f"신호에 열이 없습니다: {', '.join(missing)}")
    if len(sig) != len(bars) or not sig.index.equals(bars.index):
        raise ValueError(f"신호 길이·index 가 봉과 다릅니다 (봉 {len(bars)}, 신호 {len(sig)})")
    for c in SIGNAL_COLS:
        if sig[c].dtype != bool:
            raise TypeError(f"신호 열 {c} 은 bool 이어야 합니다 (지금 {sig[c].dtype})")
    if sig["short_entry"].any() or sig["short_exit"].any():
        raise ValueError("국내 주식은 공매도를 하지 않습니다 — short_entry/short_exit 는 False 여야 합니다")
    return sig


def parse_params(items: list[str] | None) -> dict:
    """CLI 의 --param k=v 목록 → dict. 숫자는 int/float 로 바꾼다."""
    out: dict = {}
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--param 은 이름=값 형식입니다: {it}")
        k, v = it.split("=", 1)
        v = v.strip()
        for cast in (int, float):
            try:
                out[k.strip()] = cast(v)
                break
            except ValueError:
                continue
        else:
            out[k.strip()] = {"true": True, "false": False}.get(v.lower(), v)
    return out
