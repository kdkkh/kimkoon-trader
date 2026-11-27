# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""주문 방식(공개판) — 전략 신호를 '어떤 주문을 낼지'(Intent)로 바꾼다. 방식은 둘: limit · market.

  limit   현재가를 호가 단위로 맞춘 값(매수 올림 · 매도 내림)을 주문 가격으로 적는다.
  market  시장가. 매수 수량은 '현재가 올림 + 1호가'로 정한다(시장가는 현재가보다 비싸게 체결될 수 있어 금액 안에 들게).
체결은 장부(oms/simple.py)가 정한다: 간단 시뮬레이션은 둘 다 신호 순간 현재가로 바로 체결, 모의투자 연결은 둘 다 시장가로 보낸다.
수량은 금액 기준: 수량 = amount // 1주 기준가(정수 주). 청산은 들고 있는 수량 전부.
이 파일은 공개판에만 있다. 이름 · 서명(OrderSettings · Intent · make_intent · round_tick · check_kinds · MODES)은
  실행기(strategy/live.py) · 장부 · 설정 편집(control/settings_edit.py)이 부르는 그대로다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from kis.order import tick_size

MODES = ("limit", "market")
LATER: tuple = ()


def round_tick(price: float, side: str) -> int:
    """호가 단위에 맞춘 가격: 매수는 위로, 매도는 아래로."""
    p = int(price)
    t = tick_size(p)
    if side == "buy":
        p = -(-p // t) * t
        t2 = tick_size(p)          # 올리다 단위 경계를 넘었으면 다시
        return -(-p // t2) * t2
    return (p // t) * t


@dataclass(frozen=True)
class OrderSettings:
    mode: str = "limit"
    amount: float = 1_000_000
    wait_s: float = 50.0

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"주문 방식은 {', '.join(MODES)} 중 하나(이 판에는 다른 방식이 없습니다)")
        if self.amount <= 0 or self.wait_s <= 0:
            raise ValueError("금액·기다림은 0보다 커야 합니다")


@dataclass(frozen=True)
class Intent:
    code: str
    side: str            # buy / sell
    qty: int
    mode: str            # limit / market
    price: int           # 호가 단위로 맞춘 가격(limit) 또는 금액 검사용 추정가(market)
    current: int         # 신호 때 현재가 — 간단 시뮬레이션은 이 값으로 체결
    wait_s: float
    on_timeout: str
    reason: str          # long_entry / long_exit
    t_signal_ms: int = 0
    parent: str = ""
    steps: tuple = ()
    method: str = ""
    amount: float = 0.0  # 진입에 쓰기로 한 금액(원) — 장부가 주문 방식을 바꿔 보낼 때 수량을 다시 세는 데 쓴다(0 = 모름)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Intent":
        kw = {k: d[k] for k in Intent.__dataclass_fields__ if k in d}
        if "steps" in kw:
            kw["steps"] = tuple(kw["steps"] or ())
        return Intent(**kw)


def make_intent(kind: str, code: str, current: int, held_qty: int, s: OrderSettings, t_signal_ms: int = 0,
                opp: float | None = None) -> tuple[Intent | None, str]:
    """신호(long_entry / long_exit) → (Intent, '') 또는 (None, 건너뛴 이유)."""
    if current <= 0:
        return None, "현재가 없음"
    if kind == "long_entry":
        base = round_tick(current, "buy")
        price = base if s.mode == "limit" else base + tick_size(base)
        qty = int(s.amount // price)
        if qty <= 0:
            return None, f"금액 {s.amount:,.0f}원으로 1주도 못 삼(기준가 {price:,})"
        return Intent(code, "buy", qty, s.mode, price, int(current), s.wait_s if s.mode == "limit" else 0.0,
                      "cancel" if s.mode == "limit" else "none", kind, t_signal_ms, method=s.mode,
                      amount=float(s.amount)), ""
    if kind == "long_exit":
        if held_qty <= 0:
            return None, "들고 있는 수량 없음"
        price = round_tick(current, "sell")
        return Intent(code, "sell", int(held_qty), s.mode, price, int(current), s.wait_s if s.mode == "limit" else 0.0,
                      "market" if s.mode == "limit" else "none", kind, t_signal_ms, method=s.mode), ""
    return None, f"모르는 신호 {kind}"


def check_kinds(s: OrderSettings, broker_name: str) -> None:
    """실행 중 설정 바꾸기 검사(실행기 · 설정 편집이 부름). limit · market 은 어느 연결에서도 받는다
    (모의투자 연결은 장부가 시장가로 보낸다) — 방식 이름은 OrderSettings 가 이미 검사했다."""
    return None
