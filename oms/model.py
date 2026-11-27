"""주문 하나의 상태.

  new ─보냄─▶ sending ─응답─▶ accepted ─체결 일부─▶ partial ─다 체결─▶ filled
                 │                 │                    │
                 │ 응답 없음        └──── 기다림 상한 ────┴─▶ cancel_req ─확정─▶ cancelled (남은 수량 0, 체결 < 주문)
                 ▼                                              └ 취소 전에 다 체결되면 filled
               unknown ─조회로 찾음─▶ accepted / 못 찾음(30초) ─▶ rejected
  거래소·증권사가 거부 ─▶ rejected
포지션은 '체결된 수량'으로만 바뀐다(보냄·접수로는 안 바뀜 — 보내자마자 산 것으로 적으면 안 채워진 주문도 포지션이 된다).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from execution.modes import Intent

ACTIVE = ("new", "sending", "unknown", "accepted", "partial", "cancel_req")
TERMINAL = ("filled", "cancelled", "rejected")


@dataclass
class Order:
    intent: Intent
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    state: str = "new"
    odno: str = ""
    orgno: str = ""
    filled: int = 0
    avg: float = 0.0
    t_created_ms: int = 0
    t_send_ms: int = 0
    t_ack_ms: int = 0
    t_deadline_ms: int = 0
    t_cancel_req_ms: int = 0
    t_done_ms: int = 0
    cancel_tries: int = 0
    note: str = ""

    @property
    def code(self) -> str:
        return self.intent.code

    @property
    def side(self) -> str:
        return self.intent.side

    @property
    def qty(self) -> int:
        return self.intent.qty

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    def to_dict(self) -> dict:
        d = asdict(self)
        d["intent"] = self.intent.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict) -> "Order":
        d = dict(d)
        intent = Intent.from_dict(d.pop("intent"))
        return Order(intent=intent, **{k: v for k, v in d.items() if k in Order.__dataclass_fields__})
