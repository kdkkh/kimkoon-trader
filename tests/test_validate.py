"""검증기 시험 — 정답 예시(supertrend_strategy.py)·트레이딩뷰 형식 샘플 CSV·일부러 잘못 옮긴 파일.

tests/fixtures/tv_trades_sample.csv 는 합성 봉(validate.sample_bars, 시드 7)에 기본 파라미터 Supertrend 를 돌린 결과를
트레이딩뷰 거래 목록 형식(최근 거래가 위, 거래 안에서 청산 줄이 위)으로 적은 '형식 견본'이다. 실제 트레이딩뷰 결과가 아니다.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategy.validate import read_tv_trades, validate  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "tv_trades_sample.csv"
GOOD = ROOT / "strategy" / "ported" / "supertrend_strategy.py"


def test_reference_strategy_passes():
    rep = validate(str(GOOD))
    assert rep.verdict == "통과", rep.lines


def test_tv_sample_all_match():
    rep = validate(str(GOOD), tv_trades=str(FIX))
    assert rep.verdict == "통과", rep.lines
    assert rep.data["entry_time_match_pct"] == 100.0
    assert rep.data["tv_trades"] == rep.data["our_trades"] == 4


def test_tv_divergence_first_bad_trade(tmp_path):
    df = pd.read_csv(FIX)
    i = df.index[(df["Trade #"] == 2) & (df["Type"] == "Entry long")][0]
    df.loc[i, "Price KRW"] = df.loc[i, "Price KRW"] * 1.01          # 2번 거래 진입가 1% 어긋나게
    bad = tmp_path / "tv_bad.csv"
    df.to_csv(bad, index=False)
    rep = validate(str(GOOD), tv_trades=str(bad))
    assert rep.verdict == "경고"
    assert any("처음 갈라진 거래: 2번째 (진입가)" in w for w in rep.warns), rep.lines


def test_tv_cols_mapping(tmp_path):
    df = pd.read_csv(FIX).rename(columns={"Trade #": "No", "Type": "Kind", "Date/Time": "When", "Price KRW": "Px"})
    p = tmp_path / "renamed.csv"
    df.to_csv(p, index=False)
    tv = read_tv_trades(p, {"trade": "No", "type": "Kind", "time": "When", "price": "Px"})
    assert len(tv) == 4 and list(tv["trade"]) == [1, 2, 3, 4]


BAD_SRC = '''"""Pine 원본: 시험용 / 이식 날짜: 2026-11-03 / 바꾼 점: 없음"""
import requests
import pandas as pd
from strategy import indicators as ta
from strategy.base import Strategy, empty_signals


class Bad(Strategy):
    name = "bad"
    params = {"n": 3}

    def signals(self, df):
        sig = empty_signals(df)
        sig["long_entry"] = ta.vwap(df["close"]) > 0
        sig["short_entry"] = True
        return sig


STRATEGY = Bad
'''


def test_bad_port_fails(tmp_path):
    p = tmp_path / "bad_port.py"
    p.write_text(BAD_SRC, encoding="utf-8")
    rep = validate(str(p))
    assert rep.verdict == "실패"
    assert any("금지된 import" in f for f in rep.fails)
    assert any("대응표 밖" in w for w in rep.warns)


def test_tv_only_trades_are_not_a_pass(tmp_path):
    # 트레이딩뷰에만 있는 거래(= 우리 쪽이 신호를 빠뜨림)가 있으면 '통과'가 나오면 안 된다.
    # 견본 목록에 지어낸 거래 3건(진입 · 청산 한 쌍씩)을 더해 트레이딩뷰 7건 · 우리 4건으로 만든다.
    df = pd.read_csv(FIX)
    cols = list(df.columns)
    num_col = cols[0]
    time_col = [c for c in cols if "date" in c.lower() or "time" in c.lower() or "시" in c][0]
    one = df[df[num_col] == df[num_col].min()].copy()
    extra = []
    for k, day in enumerate(("2024-03-11", "2024-03-18", "2024-03-25")):
        t = one.copy()
        t[num_col] = 100 + k
        t[time_col] = [f"{pd.Timestamp(day) + pd.Timedelta(days=2 - j):%Y-%m-%d}" for j in range(len(t))]
        extra.append(t)
    p = tmp_path / "tv_more.csv"
    pd.concat([df] + extra).to_csv(p, index=False)
    rep = validate(str(GOOD), tv_trades=str(p))
    assert rep.verdict != "통과", rep.lines
    assert rep.data["entry_time_match_pct"] < 100.0
    assert rep.data["tv_trades"] == 7 and rep.data["our_trades"] == 4
