"""실행기 step() 시험용 가짜 — 네트워크 없이 봉 · 현재가 · 전략 신호를 시험이 정한다."""
from __future__ import annotations

import pandas as pd

from strategy import live

BAR_T0 = "2026-11-11 10:00"
NOW = "2026-11-11 10:05:30"


class FakeStrat:
    """마지막 봉의 신호를 시험이 정한다(next = 'long_entry' · 'long_exit' · None)."""
    params: dict = {}

    def __init__(self):
        self.next = None

    def run(self, bars):
        s = pd.DataFrame({"long_entry": False, "long_exit": False}, index=bars.index)
        if self.next:
            s.iloc[-1, s.columns.get_loc(self.next)] = True
        return s


def bars(close: float = 61234.0, n: int = 5) -> pd.DataFrame:
    t = pd.date_range(BAR_T0, periods=n, freq="min", tz="Asia/Seoul")
    return pd.DataFrame({"time": t, "open": 61000.0, "high": 61500.0, "low": 60900.0, "close": close,
                         "volume": 10.0})


def make_runner(tmp_path, monkeypatch, codes=("005930", "000660"), price: int = 61234, **kw):
    """dry-run 실행기 + 가짜 봉(모든 종목 같은 5개) + 가짜 현재가. 반환 (실행기, 전략)."""
    monkeypatch.setattr(live, "LOG_DIR", tmp_path)
    monkeypatch.setattr(live, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(live.data.PACER, "call", lambda fn, *a, **k: {"stck_prpr": str(price)})
    monkeypatch.setattr(live.data, "cfg_of", lambda mode: None)
    st = FakeStrat()
    kw.setdefault("max_orders", 2)
    r = live.LiveRunner(list(codes), st, printer=lambda *a: None, **kw)
    b = bars(float(price))

    def upd(code):
        r.bars[code] = b
        return b, 0, 0
    r._update_bars = upd
    r.now_dt = lambda: pd.Timestamp(NOW).to_pydatetime()
    r.save_bars = lambda: None                    # run() 이 끝에 부르는 봉 캐시 저장(data/bars)을 시험에서는 하지 않는다
    return r, st


def kinds(r) -> list[str]:
    return [e["event"] for e in r.log.today_events()]


def events(r, name: str) -> list[dict]:
    return [e for e in r.log.today_events() if e["event"] == name]
