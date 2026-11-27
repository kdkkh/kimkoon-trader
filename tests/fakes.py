"""시험용 가짜 증권사·시계·기록 — 네트워크 없이 주문 사고 상황을 만든다."""
from __future__ import annotations

from datetime import datetime

from oms.broker import Ack, OrderRejected, OrderUnknown, Status


class Clock:
    def __init__(self, t0_ms: int | None = None):          # 기본 2026-11-10 09:00(이 PC 시간대) — 시험의 today 와 같은 날
        self.t = t0_ms if t0_ms is not None else int(datetime(2026, 11, 10, 9, 0).timestamp() * 1000)

    def __call__(self) -> int:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += int(sec * 1000)


class MemLog:
    def __init__(self):
        self.events: list[dict] = []

    def write(self, event: str, **kw) -> dict:
        rec = {"event": event, **kw}
        self.events.append(rec)
        return rec

    def kinds(self) -> list[str]:
        return [e["event"] for e in self.events]

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["event"] == kind]


class FakeBroker:
    """주문 장부를 직접 조작해 체결·취소·거부·응답 없음을 흉내 낸다."""
    name = "fake"
    dry = False
    instant = False

    def __init__(self):
        self.book: dict[str, dict] = {}
        self.hold: dict[str, int] = {}
        self.n = 0
        self.placed: list = []
        self.cancels: list[str] = []
        self.next_place_error: Exception | None = None
        self.place_but_lose_reply = False      # 들어가긴 했는데 응답을 잃음
        self.cancel_reject_if_filled = True

    # --- 시험이 쓰는 조작 ---
    def fill(self, odno: str, qty: int, price: float) -> None:
        b = self.book[odno]
        tot = b["filled"] + qty
        b["avg"] = (b["avg"] * b["filled"] + price * qty) / tot
        b["filled"] = tot
        b["remaining"] = b["qty"] - tot - b["cancelled"]
        self.hold[b["code"]] = self.hold.get(b["code"], 0) + (qty if b["side"] == "buy" else -qty)

    def confirm_cancel(self, odno: str) -> None:
        b = self.book[odno]
        b["cancelled"] = b["qty"] - b["filled"]
        b["remaining"] = 0

    def last(self) -> str:
        return f"{self.n:010d}"

    # --- Broker 모양 ---
    def place(self, o) -> Ack:
        if self.next_place_error:
            e, self.next_place_error = self.next_place_error, None
            raise e
        self.n += 1
        odno = f"{self.n:010d}"
        self.book[odno] = {"code": o.code, "side": o.side, "qty": o.qty, "filled": 0, "avg": 0.0,
                           "remaining": o.qty, "cancelled": 0, "mode": o.intent.mode}
        self.placed.append(o.intent)
        if self.place_but_lose_reply:
            self.place_but_lose_reply = False
            raise OrderUnknown("ReadTimeout")
        return Ack(odno, "91234", "090000", "주문 전송 완료")

    def status(self, o) -> Status | None:
        b = self.book.get(o.odno)
        if not b:
            return None
        return Status(b["qty"], b["filled"], b["avg"], b["remaining"], 0, {"odno": o.odno})

    def find(self, o, known):
        hits = [k for k, b in self.book.items() if b["code"] == o.code and b["side"] == o.side and b["qty"] == o.qty
                and k.lstrip("0") not in known]
        return Ack(hits[-1], "91234", "090000", "조회로 찾음") if len(hits) == 1 else None

    def cancel(self, o, remaining: int) -> Ack:
        b = self.book[o.odno]
        if self.cancel_reject_if_filled and b["remaining"] == 0:
            raise OrderRejected("취소할 잔량 없음")
        self.cancels.append(o.odno)
        return Ack("C" + o.odno, "", "", "취소 접수")

    def holdings(self) -> dict[str, int]:
        return {k: v for k, v in self.hold.items() if v > 0}
