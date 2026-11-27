"""실행기 한 바퀴(step) · 체결 백테스트 하루(run_day)를 끝까지 부르는 시험 — 네트워크 없이 가짜 봉으로.

막힌 자리: 시험이 _act 만 직접 불러, 봉 받기 → 닫힌 봉 → 신호 → 위험 점검 → 주문 → 기록으로 이어지는 흐름 전체와
백테스트 하루 돌리기는 사람이 돌리는 재현 비교에만 기대고 있었다.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import live  # noqa: E402
from tests.step_fakes import events, kinds, make_runner  # noqa: E402


def test_step_entry_then_exit_flow(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch)
    st.next = "long_entry"
    acts = r.step()
    assert [a["side"] for a in acts] == ["buy", "buy"] and all(a["qty"] == 1_000_000 // 61300 for a in acts)
    k = kinds(r)
    assert k.index("bar") < k.index("signal") < k.index("order_intent") < k.index("order_plan")
    assert r.oms.held("005930") == r.oms.held("000660") == 1_000_000 // 61300
    st.next = "long_exit"
    r.last_bar.clear()
    acts = r.step()
    assert [a["side"] for a in acts] == ["sell", "sell"]
    assert r.oms.held("005930") == r.oms.held("000660") == 0
    assert r.entries_today() == 2                                          # 청산은 진입 상한에 세지 않음


def test_step_same_bar_only_once(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch)
    st.next = "long_entry"
    r.step()
    r.step()                                                               # 같은 마지막 봉 — 이미 처리함
    assert len(events(r, "signal")) == 2 and len(events(r, "order_intent")) == 2


def test_step_entry_cap_in_flow(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, max_orders=1)
    st.next = "long_entry"
    r.step()
    assert r.oms.held("005930") > 0 and r.oms.held("000660") == 0
    assert any(e["event"] == "order_skip" and "상한" in e["reason"] for e in r.log.today_events())


def test_step_one_error_others_go_on(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch)
    good = r._update_bars

    def upd(code):
        if code == "005930":
            raise RuntimeError("가짜 조회 오류")
        return good(code)
    r._update_bars = upd
    st.next = "long_entry"
    r.step()
    err = events(r, "error")
    assert len(err) == 1 and err[0]["code"] == "005930" and err[0]["where"] == "step"
    assert r.oms.held("000660") > 0


def test_step_too_few_bars_is_recorded(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, codes=("005930",))
    one = pd.DataFrame({"time": pd.date_range("2026-11-11 10:04", periods=1, freq="min", tz="Asia/Seoul"),
                        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})

    def upd(code):
        r.bars[code] = one
        return one, 0, 0
    r._update_bars = upd
    r.step()
    assert events(r, "bar")[0]["note"] == "봉 부족" and not events(r, "signal")


class TimeStrat:
    """09:03 봉에 진입 · 09:06 봉에 청산(백테스트 하루 시험용)."""
    params: dict = {}

    def run(self, bars):
        hm = bars["time"].dt.strftime("%H:%M")
        return pd.DataFrame({"long_entry": hm == "09:03", "long_exit": hm == "09:06"}, index=bars.index)


def _trades(day="2026-11-11"):
    t0 = int(pd.Timestamp(f"{day} 09:00:00", tz="Asia/Seoul").timestamp())
    n = 12 * 60
    px = [61000 + 100 * ((i // 60) % 5) for i in range(n)]               # 분마다 100원씩 오르내림
    return pd.DataFrame({"s": [t0 + i for i in range(n)], "px": px, "qty": 10,
                         "side": [1 if i % 2 else -1 for i in range(n)]})


def test_run_day_small_synthetic_day(tmp_path, monkeypatch):
    BacktestRunner = pytest.importorskip("sim.backtest").BacktestRunner    # 체결 백테스트가 있는 판에서만
    monkeypatch.setattr(live, "LOG_DIR", live.LOG_DIR)                     # BacktestRunner 가 바꾼 전역을 시험 뒤 되돌림
    monkeypatch.setattr(live, "STATE_DIR", live.STATE_DIR)
    r = BacktestRunner(["005930"], TimeStrat(), {"005930": _trades()}, "20261111", out_dir=tmp_path,
                       order_mode="market", amount=1_000_000)
    s = r.run_day("09:00", "09:10")
    rt = r.round_trips()
    assert s["entries_today"] == 1 and len(rt) == 1
    fills = events(r, "fill")
    assert [f["side"] for f in fills] == ["buy", "sell"]
    sig = events(r, "signal")
    assert [x["kind"] for x in sig] == ["long_entry", "long_exit"]
    assert sig[0]["bar_time"].startswith("2026-11-11T09:03")
    assert kinds(r)[-1] == "stop"
