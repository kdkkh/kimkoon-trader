"""live.py 시험 — 네트워크 없이(조회 함수를 가짜로 바꿔) dry-run 은 주문 함수를 부르지 않는지, 가드가 도는지."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import live  # noqa: E402
from strategy.ported import load_strategy  # noqa: E402


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LOG_DIR", tmp_path)
    monkeypatch.setattr(live, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(live.order, "limit_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("주문 호출됨")))
    monkeypatch.setattr(live.data.PACER, "call", lambda fn, *a, **k: {"stck_prpr": "61234"})
    monkeypatch.setattr(live.data, "cfg_of", lambda mode: None)
    return live.LiveRunner(["005930"], load_strategy("supertrend"), max_orders=1, printer=lambda *a: None)


def test_round_tick():
    assert live.round_tick(61234, "buy") == 61300      # 50,000~200,000 원은 100원 단위 → 위로
    assert live.round_tick(61234, "sell") == 61200     # 아래로
    assert live.round_tick(1999, "buy") == 1999


def test_dry_run_plans_without_ordering(runner):
    p = runner._act("005930", "long_entry", live.now_ms())
    assert p["side"] == "buy" and p["price"] == 61300 and p["qty"] == 1_000_000 // 61300
    assert runner._act("005930", "long_entry", live.now_ms()) is None          # 종목당 1포지션
    s = runner._act("005930", "long_exit", live.now_ms())
    assert s["side"] == "sell" and s["qty"] == p["qty"]
    assert runner._act("005930", "long_entry", live.now_ms()) is None          # 하루 진입 상한 1회(청산은 안 셈)
    kinds = [e["event"] for e in runner.log.today_events()]
    assert kinds.count("order_plan") == 2 and "order_sent" not in kinds
    assert runner.entries_today() == 1


def test_exit_allowed_after_entry_cap(runner):
    # 매수·매도를 같이 세면 상한에 닿은 뒤 청산 신호가 막혀 포지션이 남는다 → 청산은 상한 밖
    assert runner._act("005930", "long_entry", live.now_ms())["side"] == "buy"
    assert runner._act("035420", "long_entry", live.now_ms()) is None        # 진입 상한
    s = runner._act("005930", "long_exit", live.now_ms())
    assert s is not None and s["side"] == "sell"                              # 상한에 닿아도 청산은 나간다


def test_exit_without_position_skipped(runner):
    assert runner._act("005930", "long_exit", live.now_ms()) is None
    assert runner.log.today_events()[-1]["event"] == "order_skip"


def test_amount_capped_by_order_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LOG_DIR", tmp_path)
    monkeypatch.setattr(live, "STATE_DIR", tmp_path / "state")
    r = live.LiveRunner(["005930"], load_strategy("supertrend"), amount=5_000_000, printer=lambda *a: None)
    assert r.amount == live.order.MAX_ORDER_WON


def test_closed_bars_drop_open_and_empty(runner):
    t = pd.date_range("2026-11-03 10:00", periods=4, freq="min", tz="Asia/Seoul")
    runner.bars["005930"] = pd.DataFrame({"time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                                          "volume": [5.0, 0.0, 5.0, 5.0]})
    out = runner.closed_bars("005930", now=pd.Timestamp("2026-11-03 10:03:30").to_pydatetime())
    assert list(out["time"].dt.strftime("%H:%M")) == ["10:00", "10:02"]       # 10:01 거래 없음, 10:03 진행 중


def test_stop_file_refuses_start(runner):
    runner.risk.stop_path.parent.mkdir(parents=True, exist_ok=True)
    runner.risk.stop_path.write_text("")
    runner.run(once=True)
    assert runner.log.today_events()[-1]["event"] == "start_refused"


def test_dry_run_state_file_separate(runner):
    assert runner.oms.state_path.name == "oms_dry.json" and runner.broker.dry


def test_dry_run_log_separate_from_paper(runner):
    assert runner.log.path.name.startswith("strategy_live_dry_")      # 모의 운행 기록과 섞이지 않게
