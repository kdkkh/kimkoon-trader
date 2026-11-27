"""증권사 연결 — OMS 는 이 모양으로만 증권사와 말한다. 실전 주문 연결은 없다.

  KisPaperBroker  한국투자 모의투자. 호출 간격 0.5초(공식 예제 kis_auth.py 의 모의 smart_sleep 값 = 1초 2건).
                  체결통보(웹소켓)를 못 받으므로(HTS ID 없음) 일별주문체결 조회로 상태를 읽는다.
  DryRunBroker    주문을 보내지 않는다. 계획한 가격에 바로 다 체결된 것으로 친다(주문 흐름 점검용, 손익 의미 없음).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime

import requests

from kis import account, order as kis_order
from kis.client import KisError

from .model import Order


class OrderRejected(RuntimeError):
    """증권사가 응답하며 거부했다(주문이 들어가지 않은 것이 확실)."""


class OrderUnknown(RuntimeError):
    """응답을 못 받았다(들어갔는지 모름) — 다시 보내지 말고 조회로 확인해야 한다."""


@dataclass
class Ack:
    odno: str
    orgno: str = ""
    ord_tmd: str = ""
    msg: str = ""


@dataclass
class Status:
    qty: int
    filled: int
    avg: float
    remaining: int
    rejected: int = 0
    raw: dict | None = None
    cancelled: int = 0


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


class KisPaperBroker:
    name = "kis-paper"
    dry = False
    instant = False

    def __init__(self, cfg, pacer):
        if cfg is None or not cfg.is_paper:
            raise PermissionError("모의투자 설정이 아닙니다 — 주문하지 않습니다")
        self.cfg = cfg
        self.pacer = pacer

    def _call(self, fn, *a):
        try:
            return self.pacer.call(fn, self.cfg, *a)
        except KisError as e:
            if "HTTP 5" in str(e) or "JSON 아님" in str(e):   # 게이트웨이 오류·본문 깨짐 — 들어갔는지 모름
                raise OrderUnknown(str(e)) from None
            raise OrderRejected(str(e)) from None
        except (requests.RequestException, TimeoutError, ConnectionError) as e:
            raise OrderUnknown(f"{type(e).__name__}: {str(e)[:120]}") from None
        except (ValueError, PermissionError, RuntimeError) as e:   # 보내기 전 안전장치(금액·호가·중복·모의 아님)
            raise OrderRejected(f"안전장치: {e}") from None

    def place(self, o: Order) -> Ack:
        i = o.intent
        if i.mode in ("limit", "chase"):                    # chase 는 맨 앞 가격의 지정가로 보낸다
            r = self._call(kis_order.limit_order, i.side, i.code, i.qty, i.price)
        elif i.mode == "market":
            r = self._call(kis_order.market_order, i.side, i.code, i.qty, i.price)
        elif i.mode == "best":
            r = self._call(kis_order.best_order, i.side, i.code, i.qty, i.price)
        else:
            raise ValueError(f"보낼 수 없는 주문 방식 {i.mode}")
        if not r.get("odno"):
            raise OrderUnknown("응답에 주문번호가 없음")
        return Ack(str(r["odno"]), str(r.get("orgno") or ""), str(r.get("ord_tmd") or ""), r.get("msg", ""))

    def status(self, o: Order) -> Status | None:
        try:
            day = datetime.fromtimestamp(o.t_send_ms / 1000).strftime("%Y%m%d") if o.t_send_ms else _today()
            s = self.pacer.call(account.order_status, self.cfg, day, o.odno)
        except Exception as e:                    # 토큰 재발급 실패 등 무엇이든 — 실행기를 죽이지 않는다
            raise OrderUnknown(f"상태 조회 실패: {type(e).__name__}: {str(e)[:120]}") from None
        if s is None:
            return None
        if s.get("orgno") and not o.orgno:
            o.orgno = s["orgno"]
        return Status(s["qty"], s["filled"], s["avg"], s["remaining"], s["rejected"], s["raw"], s["cancelled"])

    def find(self, o: Order, known: set[str]) -> Ack | None:
        """응답 없이 끝난 주문을 그날 주문 목록에서 찾는다(같은 종목·방향·수량, 보낸 시각 −5초 뒤, 아직 모르는 번호)."""
        rows = self.pacer.call(account.today_orders, self.cfg, _today())
        sent = datetime.fromtimestamp(o.t_send_ms / 1000).strftime("%H%M%S") if o.t_send_ms else "000000"
        lo = datetime.fromtimestamp(max(o.t_send_ms / 1000 - 5, 0)).strftime("%H%M%S") if o.t_send_ms else "000000"
        hits = [r for r in rows if r["code"] == o.code and r["side"] == o.side and r["qty"] == o.qty
                and not r["cancel_row"] and r["ord_tmd"] >= lo and r["odno"].lstrip("0") not in known]
        if len(hits) == 1:
            return Ack(hits[0]["odno"], hits[0]["orgno"], hits[0]["ord_tmd"], f"조회로 찾음(보낸 시각 {sent})")
        return None

    def cancel(self, o: Order, remaining: int) -> Ack:
        r = self._call(kis_order.cancel_order, o.code, o.odno, o.orgno, remaining)
        return Ack(str(r.get("odno") or ""), "", str(r.get("ord_tmd") or ""), r.get("msg", ""))

    def holdings(self) -> dict[str, int]:
        rows, _ = self.pacer.call(account.balance, self.cfg)
        return {h["pdno"]: int(float(h.get("hldg_qty", 0) or 0)) for h in rows}


class DryRunBroker:
    """주문을 보내지 않는다. 보낸 즉시 계획 가격에 전량 체결로 친다."""
    name = "dry-run"
    dry = True
    instant = True

    def __init__(self):
        self._n = itertools.count(1)
        self._book: dict[str, Status] = {}
        self._hold: dict[str, int] = {}

    def place(self, o: Order) -> Ack:
        odno = f"DRY{next(self._n):05d}"
        self._book[odno] = Status(o.qty, o.qty, float(o.intent.price), 0)
        self._hold[o.code] = self._hold.get(o.code, 0) + (o.qty if o.side == "buy" else -o.qty)
        return Ack(odno, "DRY", datetime.now().strftime("%H%M%S"), "dry-run(주문 안 보냄)")

    def status(self, o: Order) -> Status | None:
        return self._book.get(o.odno)

    def find(self, o: Order, known: set[str]) -> Ack | None:
        return None

    def cancel(self, o: Order, remaining: int) -> Ack:
        return Ack("", "", "", "dry-run")

    def holdings(self) -> dict[str, int]:
        return {k: v for k, v in self._hold.items() if v > 0}
