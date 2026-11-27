# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""간단 장부 — 공개판의 주문 장부. 실행기(strategy/live.py LiveRunner)가 부르는 이름을 모두 갖되, 주문 상태 기계가 없다.

갈래 둘(증권사 연결로 고른다)
  간단 시뮬레이션   연결이 주문을 보내지 않는 것(DryRunBroker · InstantBroker)이면, 신호 순간 현재가(intent.current)로
                    바로 다 체결된 것으로 적는다. 호가 · 줄 서기 · 체결 지연은 없다 → 결과는 실거래와 다를 수 있다.
  모의투자 연결     연결이 한국투자 모의(KisPaperBroker)면 **시장가**로 보낸다 → check_after_s 초 뒤 체결 조회 한 번 →
                    체결된 수량만 포지션에 넣는다. 덜 채워졌으면 recheck_s 초 뒤(보통 다음 봉) 한 번 더 확인하고,
                    그래도 덜 채워졌으면 check_needed('확인 필요') 사건을 남기고 체결된 수량까지만 포지션으로 둔다.
                    지정가(limit)를 골라도 모의 연결에서는 시장가로 보낸다(kind_substituted 사건으로 남김).
왜 이렇게 단순한가: 지정가 기다림 · 취소 확정 · 정정 · 응답 없는 주문 찾기 같은 주문 관리는 증권사 API 의 특수 상황마다
  손이 가고 그때마다 고쳐야 한다. 이 판은 그 부분을 싣지 않고, 위험 점검(oms/risk.py) · 실행기 · 문은 그대로 쓴다.
막힌 자리
  - 주문을 보냈는데 응답이 없으면(연결 끊김 · 시간 초과) 들어갔는지 알 수 없다 → 다시 보내지 않는다. 그 종목은 그날
    새 주문을 내지 않고(check_needed) 증권사 앱 · 잔고로 직접 확인하게 한다.
  - 시장가가 덜 채워진 채 남으면 나머지가 나중에 체결될 수 있다 → 이 장부는 확인한 수량만 안다. 끝날 때 잔고 대조로 차이를 기록한다.
저장: state 폴더의 oms_*.json(포지션 · 실현 손익 · 오늘 진입 수 · 확인 못 한 종목). 날이 바뀌면 진입 수 · 실현 손익만 0으로.
이 파일은 공개판에만 있다(내 판은 주문 관리 oms/manager.py 를 쓴다). 실전 주문 경로는 없다(kis/order.py 가 모의 설정만 받는다).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from kis.order import tick_size

from .broker import OrderRejected
from .model import Order

PAPER_HOST = "openapivts.koreainvestment.com"     # 모의투자 REST 도메인(공식 예제 kis_devlp.yaml 의 vps)
SELL_REJECT_MAX = 3                               # 잇단 매도 거부가 이만큼이면 그 종목을 막는다


def now_ms() -> int:
    return int(time.time() * 1000)


class InstantBroker:
    """주문을 어디에도 보내지 않는 연결 — 간단 시뮬레이션 실행기(scripts/sim_live.py · 봉 재생)용. 체결은 장부가 적는다."""
    name = "sim"
    dry = False
    instant = True

    def holdings(self) -> dict[str, int]:
        return {}


class SimpleBook:
    def __init__(self, broker, log, state_path: Path, say=print, clock=now_ms, today=None, sleep=time.sleep,
                 check_after_s: float = 2.0, recheck_s: float = 60.0):
        self.broker = broker
        self.log = log
        self.state_path = Path(state_path)
        self.say = say
        self.clock = clock
        self.today = today or (lambda: datetime.now().strftime("%Y%m%d"))
        self.sleep = sleep
        self.check_after_s = float(check_after_s)
        self.recheck_ms = int(float(recheck_s) * 1000)
        self.paper = not (getattr(broker, "dry", False) or getattr(broker, "instant", False))
        if self.paper:
            cfg = getattr(broker, "cfg", None)
            url = str(getattr(cfg, "rest_url", "") or "")
            if PAPER_HOST not in url:
                # 모드 이름만 보면 설정 파일의 vps 칸에 실전 주소를 잘못 적은 경우를 못 막는다 → 보내는 주소도 본다.
                raise PermissionError(f"모의투자 서버 주소({PAPER_HOST})가 아닙니다 — 설정 파일의 vps 값을 확인하세요")
        self.positions: dict[str, dict] = {}
        self.orders: dict[str, Order] = {}          # 체결을 아직 다 확인하지 못한 주문(모의 연결만)
        self.done_today: list[dict] = []
        self.day = self.today()
        self.entries_today = 0
        self.realized = 0.0
        self.current: dict[str, int] = {}            # 종목별 최근 현재가(실행기가 넣어 줌 — 평가 손익용)
        self.pending_exit: set[str] = set()          # 청산 신호를 받았는데 다 못 판 종목(실행기가 다음 봉에 다시 낸다)
        self.blocked: dict[str, str] = {}            # 주문이 들어갔는지 · 체결됐는지 확인 못 해 새 주문을 내지 않는 종목 → 이유
        self.blocked_day: dict[str, str] = {}        # 그 종목을 막은 날 — 날이 바뀐 뒤 잔고와 장부가 같으면 푼다(reconcile)
        self._sell_rejects: dict[str, int] = {}      # 종목별 잇단 매도 거부 수(봉마다 되풀이 보내지 않게)
        self.price_fn = None                         # 실행기가 넣어 주는 자리(이 장부는 쓰지 않음)
        self.switch_fn = None
        self._due: dict[str, int] = {}               # 주문 id → 다시 확인할 시각(ms)

    # ---------- 저장 · 복원 ----------
    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        d = {"version": 1, "book": "simple", "saved_at": datetime.now().isoformat(timespec="seconds"),
             "broker": self.broker.name, "day": self.day, "entries_today": self.entries_today,
             "realized": round(self.realized, 2), "positions": self.positions,
             "orders": [o.to_dict() for o in self.orders.values()], "pending_exit": sorted(self.pending_exit),
             "blocked": self.blocked, "blocked_day": self.blocked_day}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        for i in range(5):                           # 다른 프로그램이 잠깐 열고 있으면 PermissionError — 짧게 다시
            try:
                os.replace(tmp, self.state_path)
                return
            except PermissionError:
                time.sleep(0.2 * (i + 1))
        self.log.write("save_error", note="state 파일을 바꾸지 못함(다음 저장 때 다시)")

    def restore(self) -> None:
        if not self.state_path.exists():
            self.log.write("state_restored", found=False, book="simple")
            return
        d = json.loads(self.state_path.read_text(encoding="utf-8"))
        if d.get("broker") not in (None, self.broker.name):
            raise RuntimeError(f"state 파일이 다른 연결({d.get('broker')})의 것입니다: {self.state_path.name}")
        self.positions = {k: dict(v) for k, v in (d.get("positions") or {}).items()}
        self.pending_exit = set(d.get("pending_exit") or [])
        self.orders = {o["id"]: Order.from_dict(o) for o in d.get("orders") or []}
        for oid in self.orders:                      # 다시 띄웠으면 남은 주문은 바로 한 번 확인한다
            self._due[oid] = 0
        if d.get("day") == self.today():
            self.entries_today, self.realized = int(d.get("entries_today", 0)), float(d.get("realized", 0.0))
        # 막힌 종목은 날이 바뀌어도 이어 받는다 — 늦게 체결됐을 수 있는 주식이 계좌에 있는데 다시 사지 않게.
        # 날이 바뀐 것은 reconcile 이 잔고와 장부가 같은지 보고 푼다.
        self.blocked = dict(d.get("blocked") or {})
        self.blocked_day = {c: str((d.get("blocked_day") or {}).get(c) or d.get("day") or self.today()) for c in self.blocked}
        self.day = self.today()
        self.log.write("state_restored", found=True, book="simple", positions=self.positions,
                       entries_today=self.entries_today, realized=self.realized, unconfirmed=list(self.orders))
        self.save()

    # ---------- 조회 ----------
    def held(self, code: str) -> int:
        return int(self.positions.get(code, {}).get("qty", 0))

    def active_for(self, code: str):
        o = next((o for o in self.orders.values() if o.code == code), None)
        if o is not None:
            return o
        return self.blocked.get(code)              # 응답 없는 주문이 있던 종목도 '진행 중'으로 본다(다시 보내지 않게)

    def has_active(self) -> bool:
        return bool(self.orders)

    def unrealized(self, prices: dict | None = None) -> float:
        prices = prices or self.current
        return sum((prices[c] - p["avg"]) * p["qty"] for c, p in self.positions.items() if prices.get(c))

    def _opp(self, code: str, side: str):
        return None

    # ---------- 주문 ----------
    def submit(self, intent):
        if self.active_for(intent.code):
            self.log.write("order_skip", code=intent.code, reason="이 종목에 확인 중인 주문이 있음(종목당 하나)")
            return None
        if not self.paper:
            return self._fill_now(intent)
        return self._send_market(intent)

    def _fill_now(self, intent):
        """간단 시뮬레이션: 신호 순간 현재가로 전량 체결."""
        o = Order(intent=intent, t_created_ms=self.clock())
        o.t_send_ms = o.t_created_ms
        if intent.reason == "long_entry":
            self.entries_today += 1
        self.log.write("order_plan", order_id=o.id, **intent.to_dict(), t_send_ms=o.t_send_ms,
                       fill_rule="신호 순간 현재가 즉시 체결(간단 시뮬레이션)", dry_run=True)
        qty = int(intent.qty) if intent.side == "buy" else min(int(intent.qty), self.held(intent.code))
        if qty > 0:
            self._apply_fill(o, qty, float(intent.current))
        self._finish(o, "filled" if qty > 0 else "rejected", "" if qty > 0 else "팔 수량 없음")
        return o

    def _send_market(self, intent):
        """모의 연결: 시장가로 보내고 잠깐 뒤 체결 조회 한 번."""
        if intent.mode != "market":
            self.log.write("kind_substituted", code=intent.code, want=intent.mode, use="market",
                           note="모의투자 연결은 시장가로만 보낸다(간단 장부)")
            qty = intent.qty
            price = intent.price
            if intent.side == "buy":                    # 시장가 수량 규칙(현재가 + 1호가)으로 다시 — 금액을 넘지 않게
                price = intent.price + tick_size(intent.price)
                budget = intent.amount if intent.amount > 0 else intent.qty * intent.price
                qty = budget // price                   # 금액에서 바로 센다 — 내림한 수량에서 다시 내림하면 늘 한 주가 빠진다
            intent = replace(intent, mode="market", price=int(price), qty=int(qty), wait_s=0.0, on_timeout="none")
            if intent.qty <= 0:
                self.log.write("order_skip", code=intent.code, reason="시장가 수량 규칙으로 1주도 못 삼")
                return None
        o = Order(intent=intent, t_created_ms=self.clock())
        o.state, o.t_send_ms = "sending", self.clock()
        plan = {"order_id": o.id, **intent.to_dict()}
        try:
            ack = self.broker.place(o)
        except OrderRejected as e:
            self._finish(o, "rejected", str(e)[:200])
            self.say(f"  [주문 거부] {intent.code} {intent.side} {intent.qty}주: {str(e)[:100]}")
            if intent.side == "sell":
                self._sell_rejected(intent.code)
            return o
        except Exception as e:                       # 응답 없음 — 들어갔을 수도 있다. 다시 보내지 않는다
            if intent.reason == "long_entry":
                self.entries_today += 1
            note = f"{type(e).__name__}: {str(e)[:160]}"
            self.log.write("order_unknown", **plan, t_send_ms=o.t_send_ms, error=note)
            self._check_needed(o, "응답 없음 — 주문이 들어갔는지 모름. 다시 보내지 않으니 증권사 앱 · 잔고로 확인",
                               block="응답 없는 주문")
            return o
        o.odno, o.orgno, o.t_ack_ms, o.state = ack.odno, ack.orgno, self.clock(), "accepted"
        if intent.reason == "long_entry":
            self.entries_today += 1
        self.log.write("order_sent", **plan, odno=o.odno, orgno=o.orgno, ord_tmd=ack.ord_tmd, msg=ack.msg,
                       t_send_ms=o.t_send_ms, t_resp_ms=o.t_ack_ms,
                       ms_signal_to_send=(o.t_send_ms - intent.t_signal_ms) if intent.t_signal_ms else None,
                       dry_run=False)
        self.orders[o.id] = o
        self.save()
        if self.check_after_s > 0:
            self.sleep(self.check_after_s)
        self._check(o, last=False)
        return o

    def _check(self, o: Order, last: bool) -> None:
        """체결 조회 한 번 → 새로 체결된 수량만 포지션에. 다 채워지면 끝, 아니면 다음 확인을 잡거나(last=False) 확인 필요로 닫음."""
        try:
            st = self.broker.status(o)
        except Exception as e:
            self.log.write("status_error", order_id=o.id, code=o.code, error=f"{type(e).__name__}: {str(e)[:160]}")
            st = None
        if st is not None and st.filled > o.filled:
            self._apply_fill(o, int(st.filled), float(st.avg))
        if o.filled >= o.qty:
            self._finish(o, "filled")
            return
        if st is not None and st.rejected >= o.qty and o.filled == 0:
            self._finish(o, "rejected", "증권사 · 거래소 거부")
            return
        if not last:
            self._due[o.id] = self.clock() + self.recheck_ms
            if o.filled:
                o.state = "partial"
            self.log.write("fill_pending", order_id=o.id, code=o.code, side=o.side, filled=o.filled, qty=o.qty,
                           note="덜 채워짐 — 다음 확인 때 한 번 더 본다")
            self.save()
            return
        # 매수만 막는다 — 덜 팔린 매도는 실행기가 다음 봉에 남은 수량을 다시 내고(pending_exit),
        # 앞 매도가 늦게 체결돼 거부되면 _sell_rejected 가 잔고와 맞춘다. 매수는 늦게 체결되면 같은 종목을 두 번 사게 된다.
        self._check_needed(o, f"두 번 확인했는데 {o.filled}/{o.qty}주만 체결 — 남은 수량은 증권사 앱 · 잔고로 확인",
                           block="체결을 확인하지 못한 매수" if o.side == "buy" else None)

    def _check_needed(self, o: Order, why: str, block: str | None = None) -> None:
        """확인 필요로 닫는다. block 을 주면 그 종목을 막는다 — 늦게 체결될 수 있는데 장부는 모르므로 다시 사지 않게.
        (2026-11-26 검토: 막지 않으면 같은 종목을 또 사고, 늦게 체결된 주식은 장부 밖에 남아 실행기가 팔지 않는다.)"""
        self.log.write("check_needed", order_id=o.id, code=o.code, side=o.side, qty=o.qty, filled=o.filled, note=why)
        self.say(f"  [확인 필요] {o.code} {why}")
        if block:
            self._block(o.code, block)
        self._finish(o, "partial" if o.filled else "unconfirmed", why)

    def _block(self, code: str, why: str) -> None:
        self.blocked[code] = why
        self.blocked_day[code] = self.today()
        self.pending_exit.discard(code)              # 막힌 종목은 봉마다 다시 내지 않는다

    def _sell_rejected(self, code: str) -> None:
        """매도 거부 뒤: 잔고와 맞춰 본다(앱에서 이미 팔았거나, 앞 매도가 늦게 체결됐으면 장부가 더 많이 알고 있다).
        그래도 들고 있고 거부가 잇달아 SELL_REJECT_MAX 번이면 그 종목을 막는다 — 봉마다 같은 매도를 되풀이 보내지 않게."""
        n = self._sell_rejects[code] = self._sell_rejects.get(code, 0) + 1
        self.reconcile()
        if self.held(code) <= 0:
            self._sell_rejects.pop(code, None)
            return
        if n >= SELL_REJECT_MAX:
            self.log.write("check_needed", code=code, side="sell", qty=self.held(code), filled=0,
                           note=f"매도가 {n}번 잇달아 거부됨 — 더 보내지 않으니 증권사 앱 · 잔고로 확인")
            self.say(f"  [확인 필요] {code} 매도가 {n}번 잇달아 거부됨 — 더 보내지 않습니다")
            self._block(code, "매도가 잇달아 거부됨")
            self.save()

    def poll(self) -> None:
        """실행기가 봉 사이에 부른다. 다시 확인할 시각이 된 주문만 한 번 더 조회한다."""
        now = self.clock()
        for o in list(self.orders.values()):
            if now >= self._due.get(o.id, 0):
                self._check(o, last=True)

    def _apply_fill(self, o: Order, filled: int, avg: float) -> None:
        dq = filled - o.filled
        cost = avg * filled - o.avg * o.filled
        px = cost / dq if dq else avg
        pos = self.positions.get(o.code)
        if o.side == "buy":
            q0, a0 = (pos["qty"], pos["avg"]) if pos else (0, 0.0)
            q = q0 + dq
            self.positions[o.code] = {"qty": q, "avg": (q0 * a0 + dq * px) / q,
                                      "since": pos["since"] if pos else datetime.fromtimestamp(self.clock() / 1000).isoformat(timespec="seconds")}
        else:
            q0, a0 = (pos["qty"], pos["avg"]) if pos else (0, px)
            self.realized += (px - a0) * dq
            if q0 - dq > 0:
                self.positions[o.code]["qty"] = q0 - dq
            else:
                self.positions.pop(o.code, None)
                self.pending_exit.discard(o.code)
            self._sell_rejects.pop(o.code, None)
        o.filled, o.avg = filled, avg
        self.log.write("fill", order_id=o.id, code=o.code, side=o.side, qty=dq, price=round(px, 2), cum_filled=filled,
                       avg=avg, t_seen_ms=self.clock(), realized_today=round(self.realized, 2),
                       dry_run=not self.paper)
        self.say(f"  [체결] {o.code} {'매수' if o.side == 'buy' else '매도'} {dq}주 × {px:,.0f}원 (누적 {filled}/{o.qty})")

    def _finish(self, o: Order, state: str, note: str = "") -> None:
        o.state, o.t_done_ms, o.note = state, self.clock(), note
        rec = {"order_id": o.id, "code": o.code, "side": o.side, "mode": o.intent.mode, "reason": o.intent.reason,
               "state": state, "qty": o.qty, "filled": o.filled, "avg": o.avg, "note": note}
        self.log.write("order_done", **rec)
        self.done_today.append(rec)
        self.orders.pop(o.id, None)
        self._due.pop(o.id, None)
        if o.side == "sell":
            if self.held(o.code) > 0 and o.code not in self.blocked:
                self.pending_exit.add(o.code)        # 다 못 팔았으면 실행기가 다음 봉에 다시 낸다
            else:
                self.pending_exit.discard(o.code)
        self.save()

    # ---------- 정리 ----------
    def cancel_all(self, why: str) -> None:
        """끝낼 때: 확인 중인 주문을 지금 한 번 더 조회하고 닫는다(시장가라 취소는 보내지 않는다)."""
        for o in list(self.orders.values()):
            self._check(o, last=True)

    def wait_idle(self, timeout_s: float = 60.0, every_s: float = 2.0, sleep=time.sleep) -> bool:
        return not self.has_active()

    def switch_open_orders(self, why: str) -> int:
        return 0                                     # 걸어 둔 주문이 없다(시장가 · 즉시 체결뿐)

    def reconcile(self) -> dict:
        """모의 연결만: 증권사 잔고와 대조. 잔고가 적으면 장부를 줄이고, 많으면 손대지 않는다(실행기가 산 것만 관리)."""
        if not self.paper:
            return {}
        try:
            have = self.broker.holdings()
        except Exception as e:
            self.log.write("reconcile_error", error=f"{type(e).__name__}: {str(e)[:160]}")
            return {"ok": False, "error": str(e)[:160]}
        if not have and self.positions:              # 빈 응답(일시 오류)으로 지우지 않는다
            self.log.write("reconcile_skip", note="잔고 응답이 비었는데 장부에 포지션이 있음 — 줄이지 않음",
                           positions=self.positions)
            return {"ok": False, "skipped": True, "diffs": []}
        diffs = []
        for code, p in list(self.positions.items()):
            if self.active_for(code) and code not in self.blocked:
                continue
            h = int(have.get(code, 0))
            if h < p["qty"]:
                diffs.append({"code": code, "ours": p["qty"], "broker": h, "fix": "잔고에 맞춰 줄임"})
                if h <= 0:
                    self.positions.pop(code)
                    self.pending_exit.discard(code)
                else:
                    p["qty"] = h
            elif h > p["qty"]:
                diffs.append({"code": code, "ours": p["qty"], "broker": h, "fix": "없음(실행기가 산 것만 관리)"})
        today = self.today()
        for code in list(self.blocked):              # 앞선 날 막힌 종목: 잔고와 장부가 같으면 풀고, 다르면 계속 막는다
            if self.blocked_day.get(code, today) >= today:
                continue                             # 오늘 막은 것은 오늘은 풀지 않는다(아직 체결될 수 있다)
            h = int(have.get(code, 0))
            if h == self.held(code):
                self.log.write("unblocked", code=code, was=self.blocked.pop(code), holding=h)
                self.blocked_day.pop(code, None)
            else:
                self.log.write("check_needed", code=code, side="", qty=self.held(code), filled=0,
                               note=f"앞선 날 막힌 종목 — 잔고 {h}주 · 장부 {self.held(code)}주로 달라 계속 막음"
                                    "(증권사 앱에서 맞춘 뒤 다시 켜면 풀림)")
        rep = {"ok": not any(d["fix"] != "없음(실행기가 산 것만 관리)" for d in diffs), "diffs": diffs,
               "positions": self.positions}
        self.log.write("reconcile", **rep)
        self.save()
        return rep

    def summary(self) -> dict:
        by: dict[str, int] = {}
        for r in self.done_today:
            by[r["state"]] = by.get(r["state"], 0) + 1
        return {"orders_done": by, "active": len(self.orders), "entries_today": self.entries_today,
                "realized": round(self.realized, 2), "unrealized": round(self.unrealized(), 2),
                "positions": self.positions}

