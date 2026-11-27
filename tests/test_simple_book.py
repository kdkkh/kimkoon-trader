# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""간단 장부(oms/simple.py) 시험 — 간단 시뮬레이션 즉시 체결 · 모의 연결(가짜 연결: 시장가 → 체결 조회 → 다음 확인 → 확인 필요).
네트워크 호출 없음. 종목 코드 999901 · 999902 는 가짜, 가격도 가짜."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution.modes import OrderSettings, make_intent  # noqa: E402
from oms.broker import Ack, DryRunBroker, OrderRejected, Status  # noqa: E402
from oms.risk import RiskGate  # noqa: E402
from oms.simple import PAPER_HOST, InstantBroker, SimpleBook  # noqa: E402

A, B = "999901", "999902"


class Log:
    def __init__(self):
        self.rows = []

    def write(self, event, **kw):
        self.rows.append({"event": event, **kw})
        return self.rows[-1]

    def names(self):
        return [r["event"] for r in self.rows]


class Clock:
    def __init__(self, t=1_000_000):
        self.t = t

    def __call__(self):
        return self.t


class FakePaper:
    """한투 모의 자리의 가짜 연결: place 는 주문번호, status 는 plan 에 넣어 둔 체결 수량을 차례로."""
    name, dry, instant = "kis-paper", False, False

    def __init__(self, plan=None, place_error=None, host=PAPER_HOST):
        self.cfg = SimpleNamespace(rest_url=f"https://{host}:1")
        self.plan, self.place_error, self.placed, self.hold = list(plan or []), place_error, [], {}

    def place(self, o):
        if self.place_error:
            raise self.place_error
        self.placed.append((o.intent.mode, o.side, o.qty))
        return Ack(f"P{len(self.placed):04d}", "ORG", "", "모의 접수(가짜)")

    def status(self, o):
        f = self.plan.pop(0) if self.plan else None
        if f is None:
            return None
        return Status(o.qty, f, 50_000.0 if o.side == "buy" else 51_000.0, o.qty - f)

    def holdings(self):
        return dict(self.hold)


def book(tmp, broker, **kw):
    kw.setdefault("sleep", lambda s: None)
    b = SimpleBook(broker, Log(), tmp / "oms.json", say=lambda *a: None, clock=Clock(), today=lambda: "20990105", **kw)
    b.restore()
    return b


def buy(code=A, px=50_000, mode="market", amount=1_000_000):
    i, _ = make_intent("long_entry", code, px, 0, OrderSettings(mode, amount, 50))
    return i


def sell(b, code=A, px=51_000, mode="market"):
    i, _ = make_intent("long_exit", code, px, b.held(code), OrderSettings(mode, 1_000_000, 50))
    return i


def test_instant_fills_at_current(tmp_path):
    b = book(tmp_path, DryRunBroker())
    i = buy(mode="limit")
    b.submit(i)
    assert b.held(A) == i.qty and b.positions[A]["avg"] == 50_000       # 신호 순간 현재가(현재가 50,000)
    assert b.entries_today == 1 and not b.has_active()
    b.current[A] = 51_000
    b.submit(sell(b))
    assert b.held(A) == 0 and b.realized == (51_000 - 50_000) * i.qty
    assert [r["state"] for r in b.log.rows if r["event"] == "order_done"] == ["filled", "filled"]


def test_instant_broker_same_as_dry(tmp_path):
    b = book(tmp_path, InstantBroker())
    b.submit(buy(px=20_000))
    assert b.held(A) == 1_000_000 // 20_100 and b.summary()["orders_done"] == {"filled": 1}
    assert b.reconcile() == {}


def test_paper_market_filled_first_check(tmp_path):
    br = FakePaper(plan=[19])
    b = book(tmp_path, br)
    i = buy(mode="limit")                                              # limit 를 골라도 모의 연결은 시장가로
    b.submit(i)
    assert br.placed[0][0] == "market" and "kind_substituted" in b.log.names()
    assert b.held(A) == 19 and not b.has_active() and b.entries_today == 1


def test_paper_qty_from_amount(tmp_path):
    """지정가 → 시장가로 바꿔 보낼 때 수량은 금액에서 바로 센다(2026-11-26 장중: 2주 살 금액에 1주만 나갔다)."""
    br = FakePaper(plan=[2])
    b = book(tmp_path, br)
    b.submit(buy(px=1_064_000, mode="limit", amount=3_000_000))        # 지정가 수량 2 · 시장가 기준가 1,065,000
    assert br.placed[0] == ("market", "buy", 3_000_000 // 1_065_000) == ("market", "buy", 2)
    br2 = FakePaper(plan=[11])
    b2 = book(tmp_path / "b2", br2)
    b2.submit(buy(px=264_000, mode="limit", amount=3_000_000))         # 11주(예전 식은 10주)
    assert br2.placed[0][2] == 11


def test_paper_partial_then_recheck_fills(tmp_path):
    br = FakePaper(plan=[5, 19])
    b = book(tmp_path, br, recheck_s=60)
    b.submit(buy())
    assert b.held(A) == 5 and b.has_active() and b.active_for(A)        # 덜 채워짐 → 확인 중(새 주문 안 냄)
    b.poll()
    assert b.held(A) == 5                                              # 아직 다음 확인 시각 전
    b.clock.t += 60_000
    b.poll()
    assert b.held(A) == 19 and not b.has_active()
    assert b.log.names().count("fill") == 2 and "check_needed" not in b.log.names()


def test_paper_unfilled_twice_check_needed(tmp_path):
    br = FakePaper(plan=[3, 3])
    b = book(tmp_path, br)
    b.submit(buy())
    b.clock.t += 60_000
    b.poll()
    cn = [r for r in b.log.rows if r["event"] == "check_needed"]
    assert len(cn) == 1 and cn[0]["filled"] == 3 and b.held(A) == 3      # 체결된 수량만 포지션
    assert not b.has_active() and b.summary()["orders_done"] == {"partial": 1}


def test_paper_no_answer_blocks_code(tmp_path):
    br = FakePaper(place_error=TimeoutError("응답 없음"))
    b = book(tmp_path, br)
    b.submit(buy())
    assert b.held(A) == 0 and "order_unknown" in b.log.names() and "check_needed" in b.log.names()
    assert b.active_for(A) and b.submit(buy()) is None                   # 다시 보내지 않는다
    assert b.entries_today == 1 and not b.active_for(B)


def test_paper_rejected(tmp_path):
    b = book(tmp_path, FakePaper(place_error=OrderRejected("가짜 거부")))
    b.submit(buy())
    assert b.held(A) == 0 and b.summary()["orders_done"] == {"rejected": 1} and b.entries_today == 0


def test_paper_sell_partial_pending_exit(tmp_path):
    br = FakePaper(plan=[19, 10, 10])
    b = book(tmp_path, br)
    b.submit(buy())
    b.submit(sell(b))
    b.clock.t += 60_000
    b.poll()
    assert b.held(A) == 9 and A in b.pending_exit                       # 실행기가 다음 봉에 다시 낸다


def test_refuses_non_paper_host(tmp_path):
    with pytest.raises(PermissionError):
        book(tmp_path, FakePaper(host="example.invalid"))


def test_save_restore_same_day(tmp_path):
    b = book(tmp_path, DryRunBroker())
    b.submit(buy())
    b.current[A] = 51_000
    b.submit(sell(b))
    b.submit(buy(B, 20_000))
    d = json.loads((tmp_path / "oms.json").read_text(encoding="utf-8"))
    assert d["book"] == "simple" and d["entries_today"] == 2 and B in d["positions"]
    b2 = book(tmp_path, DryRunBroker())
    assert b2.entries_today == 2 and b2.realized == b.realized and b2.held(B) == b.held(B)
    b3 = SimpleBook(DryRunBroker(), Log(), tmp_path / "oms.json", say=lambda *a: None, today=lambda: "20990106")
    b3.restore()
    assert b3.entries_today == 0 and b3.realized == 0 and b3.held(B) == b.held(B)   # 날이 바뀌면 진입 수 · 실현만 0


def test_risk_gate_uses_simple_book(tmp_path):
    b = book(tmp_path, DryRunBroker())
    gate = RiskGate(tmp_path / "stop", max_entries=1, daily_loss_limit=10_000)
    assert gate.check_entry(b) is None
    b.submit(buy())
    assert "진입 상한" in gate.check_entry(b)
    b.current[A] = 40_000                                              # 평가 손실 −10,000 × 수량
    gate.max_entries = 9
    assert "손실 한도" in gate.check_entry(b)


def test_reconcile_shrinks_to_holdings(tmp_path):
    br = FakePaper(plan=[19])
    b = book(tmp_path, br)
    b.submit(buy())
    br.hold = {A: 10}
    rep = b.reconcile()
    assert b.held(A) == 10 and rep["diffs"][0]["fix"] == "잔고에 맞춰 줄임"


def test_unconfirmed_buy_blocks_code(tmp_path):
    """두 번 조회해도 0주인 매수는 '확인 필요'로 닫고 그 종목을 막는다 — 늦게 체결될 수 있는데 또 사지 않게.
    날이 바뀐 뒤 잔고와 장부가 같으면 풀고, 다르면(늦게 체결된 주식이 계좌에 있음) 계속 막는다."""
    br = FakePaper(plan=[0, 0])
    b = book(tmp_path, br, recheck_s=60)
    b.submit(buy())
    b.clock.t += 61_000
    b.poll()
    assert "check_needed" in b.log.names() and A in b.blocked and b.held(A) == 0
    n = len(br.placed)
    assert b.submit(buy()) is None and len(br.placed) == n                # 같은 종목은 다시 보내지 않는다
    b.reconcile()
    assert A in b.blocked                                                 # 막은 날에는 풀지 않는다(아직 체결될 수 있다)
    b2 = SimpleBook(br, Log(), tmp_path / "oms.json", say=lambda *a: None, clock=Clock(), today=lambda: "20990106",
                    sleep=lambda s: None)
    b2.restore()
    assert A in b2.blocked                                                # 날을 넘겨도 이어 받는다
    br.hold = {A: 19}                                                     # 늦게 체결돼 계좌에는 있다 · 장부는 0
    b2.reconcile()
    assert A in b2.blocked and b2.submit(buy()) is None
    br.hold = {}                                                          # 앱에서 정리함 → 잔고 0 = 장부 0
    b2.reconcile()
    assert A not in b2.blocked and "unblocked" in b2.log.names()


def test_sell_rejected_then_blocks(tmp_path):
    """매도 거부: 잔고와 맞춰 보고(앱에서 이미 팔았으면 장부를 줄임), 그래도 잇달아 거부되면 막는다 — 봉마다 되풀이하지 않게."""
    br = FakePaper(plan=[19])
    b = book(tmp_path, br)
    b.submit(buy())
    br.hold = {"000660": 1}                                               # 사용자가 앱에서 이미 팔았다(다른 종목은 들고 있음)
    br.place_error = OrderRejected("주문가능수량 부족")
    b.submit(sell(b))
    assert b.held(A) == 0 and A not in b.pending_exit and A not in b.blocked

    (tmp_path / "b2").mkdir()
    br2 = FakePaper(plan=[19])
    b2 = book(tmp_path / "b2", br2)
    b2.submit(buy())
    br2.hold = {A: 19}                                                    # 계좌에는 그대로 있는데 거부만 되풀이
    br2.place_error = OrderRejected("거부")
    for _ in range(3):
        b2.submit(sell(b2))
    assert A in b2.blocked and A not in b2.pending_exit and b2.held(A) == 19
    n = len(br2.placed)
    assert b2.submit(sell(b2)) is None and len(br2.placed) == n           # 막힌 뒤에는 보내지 않는다
