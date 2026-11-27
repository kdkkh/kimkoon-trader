"""실행기 기록(strategy_live_*.jsonl) 읽기 — 사건 분류 · 한 줄 글 · 하루 요약 · 체결로 만든 포지션 · 한 번 사고팔기 · 신호 짝짓기.

모의 · 시뮬레이션 · 체결 백테스트 기록이 모두 같은 사건 이름을 쓴다(실행기 코드가 하나라서). 읽기만 하고 예외를 내지 않는다.
돌려주는 것에는 주문번호 · 조직번호 · 원응답(raw)을 넣지 않는다.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

# 사건 → 화면 거르기 칸
CATS = {
    "signal": "signal", "ext_signal": "signal", "entry_review": "signal",
    "order_intent": "order", "order_sent": "order", "order_unknown": "order", "order_found": "order",
    "order_skip": "order", "exit_retry": "order",
    "fill": "fill",
    "cancel_sent": "cancel", "review_cancelled": "cancel",
    "risk_block": "risk",
    "ext_rejected": "door", "entry_filtered": "door", "ext_rule_exit": "door", "ext_owned_left": "door",
    "review_expired": "door", "review_denied": "door", "review_unavailable": "door",
    "settings_changed": "settings", "settings_rejected": "settings", "settings_partial": "settings",
    "switch_requested": "settings", "codes_changed": "settings",
    "error": "error", "notify_error": "error", "find_error": "error", "ws_error": "error", "ws_parse_error": "error",
    "start_refused": "error", "notice_error": "error", "check_needed": "error",
    "bar": "bar",
}
CAT_NAMES = {"signal": "신호", "order": "주문", "fill": "체결", "cancel": "취소", "risk": "위험 점검 막힘", "door": "문",
             "settings": "설정", "error": "오류", "run": "실행"}
SIDE = {"buy": "매수", "sell": "매도"}
STATE = {"filled": "체결 끝", "cancelled": "취소됨", "rejected": "거부", "partial": "일부 체결"}


def cat_of(e: dict) -> str:
    ev = str(e.get("event", ""))
    if ev == "order_done":
        st = str(e.get("state", ""))
        return "fill" if st == "filled" else "cancel" if st == "cancelled" else "order"
    return CATS.get(ev, "run")


def _n(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def won(v) -> str:
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _hm(bar_time) -> str:
    s = str(bar_time or "")
    return s[11:16] if len(s) >= 16 else s


MASKED = "•••"          # ?mask=1(녹화) 일 때 원 단위 가격 · 체결가 · 평균가 · 종가 자리에 넣는 글


def event_text(e: dict, mask: bool = False) -> str:
    """사건 한 줄 글. mask 면 가격 · 체결가 · 평균가 · 종가를 가린다(손익 합계는 남김)."""
    won_px = (lambda v: MASKED) if mask else won
    ev = str(e.get("event", ""))
    c = str(e.get("code", "") or "")
    side = SIDE.get(str(e.get("side", "")), str(e.get("side", "") or ""))
    if ev == "signal":
        return f"{c} {e.get('kind', '')} (봉 {_hm(e.get('bar_time'))})"
    if ev == "order_intent":
        px = f" {won_px(e.get('price'))}" if e.get("price") else ""
        return f"{c} {side} {e.get('qty')}주 {e.get('method') or e.get('mode', '')}{px} — {e.get('reason', '')}"
    if ev == "order_sent":
        return f"{c} {side} {e.get('qty')}주 보냄(신호 뒤 {e.get('ms_signal_to_send', '?')}ms)"
    if ev == "order_unknown":
        return f"{c} {side} {e.get('qty')}주 — 응답 없음(조회로 확인)"
    if ev == "order_found":
        return f"{c} 주문 확인 — {e.get('note', '')}"
    if ev == "fill":
        return f"{c} {side} {e.get('qty')}주 {won_px(e.get('price'))} 체결(누적 {e.get('cum_filled')})"
    if ev == "order_done":
        st = STATE.get(str(e.get("state", "")), str(e.get("state", "")))
        avg = f" 평균 {won_px(e.get('avg'))}" if _n(e.get("avg")) else ""
        return f"{c} {side} {st} {e.get('filled')}/{e.get('qty')}주{avg}"
    if ev == "cancel_sent":
        return f"{c} 취소 보냄 — {e.get('why', '')} · 남은 {e.get('remaining')}주"
    if ev == "order_skip":
        return f"{c} 건너뜀 — {e.get('reason', '')}"
    if ev == "risk_block":
        return f"{c} 진입 막힘({e.get('kind', '')}) — {e.get('reason', '')}"
    if ev == "entry_filtered":
        return f"{c} 진입 거름 — {e.get('gate', '')}"
    if ev == "ext_signal":
        return f"{c} 바깥 신호 {e.get('action', '')} ({e.get('source', '')})"
    if ev == "ext_rule_exit":
        px, avg = e.get("px"), e.get("avg")
        return (f"{c} 바깥 규칙 청산 — {e.get('reason', '')}"
                + (f" · 판정 값 {won_px(px)} / 평균 {won_px(avg)}" if px and avg else ""))
    if ev == "ext_rejected":
        return f"바깥 신호 버림 — {e.get('reason', '')}"
    if ev in ("settings_changed", "settings_partial"):
        ch = e.get("changes") if ev == "settings_changed" else e.get("keys") or e.get("changes")
        if isinstance(ch, dict):
            return "설정 바뀜 " + ", ".join(f"{k}: {v[0]} → {v[1]}" if isinstance(v, list) and len(v) == 2 else f"{k}" for k, v in ch.items())
        return f"설정 {'일부만(다시 시작해야 반영)' if ev == 'settings_partial' else '바뀜'} {ch if ch else ''}"
    if ev == "settings_rejected":
        return f"설정 거부 — {e.get('error', '')}"
    if ev == "start":
        return (f"시작 {e.get('mode', '')} · {e.get('broker', '')} · 종목 {len(e.get('codes') or [])} · "
                f"{e.get('order_mode', '')} · 1회 {won(e.get('amount'))}원" + (" · 문 켜짐" if e.get("ext") else ""))
    if ev == "stop":
        s = e.get("summary") if isinstance(e.get("summary"), dict) else {}
        return f"끝({e.get('reason', '')}) · 실현 {won(s.get('realized'))} · 평가 {won(s.get('unrealized'))}"
    if ev == "reconcile":
        return f"잔고 대조 {'맞음' if e.get('ok') else '차이 ' + str(len(e.get('diffs') or []))}"
    if ev == "state_restored":
        return f"장부 복원({'있음' if e.get('found') else '없음'})"
    if ev == "start_refused":
        return f"시작 거부 — {e.get('reason', '')} {e.get('detail', '') or ''}"
    if ev == "bar":
        return f"{c} 봉 {_hm(e.get('bar_time'))} 종가 {won_px(e.get('close'))}"
    if "error" in e:
        return f"{ev} — {e.get('error')}"
    keys = [k for k in e if k not in ("ts_ms", "at", "event")][:4]
    return f"{ev} " + " · ".join(f"{k}={e.get(k)}" for k in keys)


def show_event(e: dict, mask: bool = False) -> dict:
    """화면에 보낼 한 줄(필요한 칸만). mask 면 글 속 가격을 가린다."""
    at = str(e.get("at", ""))
    return {"ts_ms": int(_n(e.get("ts_ms"))), "at": at[11:23] if len(at) >= 19 else at, "event": str(e.get("event", "")),
            "cat": cat_of(e), "code": str(e.get("code", "") or ""), "text": event_text(e, mask)}


def trade_day(events: list[dict]) -> str:
    """기록 안의 거래일(YYYY-MM-DD): 첫 봉의 bar_time → start 의 day → 첫 at."""
    for e in events:
        if e.get("event") == "bar" and e.get("bar_time"):
            return str(e["bar_time"])[:10]
    for e in events:
        if e.get("event") == "start" and e.get("day"):
            d = str(e["day"])
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
    for e in events:
        if e.get("at"):
            return str(e["at"])[:10]
    return ""


def last_stop(events: list[dict]) -> dict | None:
    st = None
    for e in events:
        if e.get("event") == "stop":
            st = e
    return st


def book_from_fills(events: list[dict]) -> dict:
    """체결 사건으로 다시 계산한 장부(평균 단가 방식): {positions{종목: qty · avg}, realized, buy_amt, sell_amt}.
    하루 처음 장부 복원(state_restored)의 포지션을 시작점으로 둔다."""
    pos: dict[str, dict] = {}
    for e in events:
        if e.get("event") == "state_restored" and isinstance(e.get("positions"), dict):
            for c, v in e["positions"].items():
                if isinstance(v, dict) and int(_n(v.get("qty"))):
                    pos[str(c)] = {"qty": int(_n(v.get("qty"))), "avg": _n(v.get("avg"))}
            break
    realized = buy_amt = sell_amt = 0.0
    for e in events:
        if e.get("event") != "fill":
            continue
        c, q, px = str(e.get("code", "")), int(_n(e.get("qty"))), _n(e.get("price"))
        if not c or q <= 0:
            continue
        p = pos.setdefault(c, {"qty": 0, "avg": 0.0})
        if e.get("side") == "buy":
            buy_amt += q * px
            p["avg"] = (p["avg"] * p["qty"] + px * q) / (p["qty"] + q)
            p["qty"] += q
        elif e.get("side") == "sell":
            sell_amt += q * px
            realized += (px - p["avg"]) * q
            p["qty"] -= q
            if p["qty"] <= 0:
                p["qty"], p["avg"] = 0, 0.0
    return {"positions": {c: {"qty": v["qty"], "avg": round(v["avg"], 2)} for c, v in pos.items() if v["qty"] > 0},
            "realized": round(realized, 2), "buy_amt": buy_amt, "sell_amt": sell_amt}


def last_closes(events: list[dict]) -> dict[str, float]:
    out = {}
    for e in events:
        if e.get("event") == "bar" and e.get("code") and e.get("close") is not None:
            out[str(e["code"])] = _n(e.get("close"))
    return out


def fee_rates(fees: dict | None) -> tuple[float | None, float | None, str]:
    """(수수료 %, 매도세 %, 출처 글). 둘 중 하나라도 비면 (None, None, '')."""
    f = fees if isinstance(fees, dict) else {}
    try:
        c = float(f.get("commission_pct")) if f.get("commission_pct") not in (None, "") else None
        t = float(f.get("sell_tax_pct")) if f.get("sell_tax_pct") not in (None, "") else None
    except (TypeError, ValueError):
        return None, None, "율 칸이 숫자가 아님"
    if c is None or t is None or c < 0 or t < 0:
        return None, None, ""
    return c, t, str(f.get("source") or "")


def summarize(events: list[dict], fees: dict | None = None) -> dict:
    """하루 요약. 손익은 두 줄: 수수료 · 세금 전(실행기 값) / 수수료 · 세금 넣은 값(율이 있으면)."""
    cnt = Counter(str(e.get("event")) for e in events)
    done = Counter(str(e.get("state")) for e in events if e.get("event") == "order_done")
    fb = book_from_fills(events)
    stop = last_stop(events)
    s = stop.get("summary") if stop and isinstance(stop.get("summary"), dict) else None
    closes = last_closes(events)
    if s is not None:
        realized, unreal, src = _n(s.get("realized")), _n(s.get("unrealized")), "끝 요약(stop)"
    else:
        realized = fb["realized"]
        unreal = sum((closes.get(c, v["avg"]) - v["avg"]) * v["qty"] for c, v in fb["positions"].items())
        src = "체결 사건으로 계산 · 평가는 마지막 봉 종가"
    holdings = [{"code": c, "qty": v["qty"], "avg": v["avg"], "last": closes.get(c)} for c, v in sorted(fb["positions"].items())]
    c_pct, t_pct, fsrc = fee_rates(fees)
    gross = realized + unreal
    if c_pct is None:
        net = {"ok": False, "text": "율을 설정하면 계산", "source": fsrc}
    else:
        cost_fee = (fb["buy_amt"] + fb["sell_amt"]) * c_pct / 100
        cost_tax = fb["sell_amt"] * t_pct / 100
        net = {"ok": True, "value": round(gross - cost_fee - cost_tax, 0), "fee": round(cost_fee, 0), "tax": round(cost_tax, 0),
               "commission_pct": c_pct, "sell_tax_pct": t_pct, "source": fsrc}
    return {
        "lines": len(events), "trade_day": trade_day(events),
        "signals": cnt.get("signal", 0), "ext_signals": cnt.get("ext_signal", 0),
        "intents": cnt.get("order_intent", 0), "sent": cnt.get("order_sent", 0), "unknown": cnt.get("order_unknown", 0),
        "fills": cnt.get("fill", 0), "done_filled": done.get("filled", 0), "cancelled": done.get("cancelled", 0),
        "risk_blocks": cnt.get("risk_block", 0), "filtered": cnt.get("entry_filtered", 0), "errors":
            sum(v for k, v in cnt.items() if CATS.get(k) == "error"),
        "realized": round(realized, 0), "unrealized": round(unreal, 0), "gross": round(gross, 0), "pnl_source": src,
        "realized_fills": round(fb["realized"], 0), "buy_amt": round(fb["buy_amt"], 0), "sell_amt": round(fb["sell_amt"], 0),
        "net": net, "holdings": holdings, "ended": stop is not None,
        "stop_reason": str(stop.get("reason", ""))[:40] if stop else "",
    }


def round_trips(events: list[dict]) -> list[dict]:
    """체결 사건 → 한 번 사고팔기(포지션 0 → 보유 → 0). round_trips.csv 와 같은 칸."""
    out, cur = [], {}
    for e in events:
        if e.get("event") != "fill":
            continue
        c, q, px = str(e.get("code", "")), int(_n(e.get("qty"))), _n(e.get("price"))
        t = int(_n(e.get("t_seen_ms") or e.get("ts_ms")))
        if not c or q <= 0:
            continue
        r = cur.get(c)
        if e.get("side") == "buy":
            if r is None:
                r = cur[c] = {"code": c, "t_entry_ms": t, "qty": 0, "cost": 0.0, "held": 0, "sold": 0, "proceeds": 0.0}
            r["qty"] += q
            r["held"] += q
            r["cost"] += q * px
        elif e.get("side") == "sell" and r is not None:
            r["held"] -= q
            r["sold"] += q
            r["proceeds"] += q * px
            if r["held"] <= 0:
                entry = r["cost"] / r["qty"] if r["qty"] else 0.0
                exitp = r["proceeds"] / r["sold"] if r["sold"] else 0.0
                pnl = r["proceeds"] - entry * r["sold"]
                out.append({"code": c, "t_entry_ms": r["t_entry_ms"], "t_exit_ms": t, "qty": r["sold"],
                            "entry": round(entry, 2), "exit": round(exitp, 2), "pnl": round(pnl, 2),
                            "ret_pct": round((exitp / entry - 1) * 100, 4) if entry else 0.0})
                del cur[c]
    return out


def signal_chain(events: list[dict]) -> dict:
    """신호 (종목, bar_time, 종류) → {t_signal_ms, ordered, side, qty, state, filled, avg}.
    신호 → order_intent(t_signal_ms) → order_sent/order_unknown(t_signal_ms → order_id) → order_done(order_id)."""
    sig, by_t = {}, {}
    for e in events:
        if e.get("event") == "signal":
            k = (str(e.get("code", "")), str(e.get("bar_time", "")), str(e.get("kind", "")))
            sig[k] = {"t_signal_ms": e.get("t_signal_ms"), "ordered": False, "side": "", "qty": 0, "state": "",
                      "filled": 0, "avg": None, "order_id": None}
            by_t[e.get("t_signal_ms")] = k
    oid_of = {}
    for e in events:
        ev = e.get("event")
        k = by_t.get(e.get("t_signal_ms"))
        if ev == "order_intent" and k and not sig[k]["ordered"] and not e.get("parent"):
            sig[k].update(ordered=True, side=str(e.get("side", "")), qty=int(_n(e.get("qty"))), price=e.get("price"))
        elif ev in ("order_sent", "order_unknown") and k and e.get("order_id") and sig[k]["order_id"] is None:
            sig[k]["order_id"] = e["order_id"]
            oid_of[e["order_id"]] = k
        elif ev == "order_done" and e.get("order_id") in oid_of:
            kk = oid_of[e["order_id"]]
            sig[kk].update(state=str(e.get("state", "")), filled=int(_n(e.get("filled"))),
                           avg=_n(e.get("avg")) if _n(e.get("avg")) else None)
    for v in sig.values():
        v.pop("order_id", None)
    return sig


def compare(paper: list[dict], sim: list[dict]) -> dict:
    """모의 대 시뮬레이션: 같은 신호(종목, bar_time, 종류)로 짝지어 주문 · 체결 여부 · 체결가 차이."""
    a, b = signal_chain(paper), signal_chain(sim)
    rows = []
    for k in sorted(set(a) | set(b), key=lambda x: (x[1], x[0], x[2])):
        pa, sb = a.get(k), b.get(k)
        row = {"code": k[0], "bar": _hm(k[1]), "bar_time": k[1], "kind": k[2], "paper": pa, "sim": sb, "diff": None,
               "diff_won": None, "note": ""}
        if pa and sb and pa["avg"] and sb["avg"]:
            row["diff"] = round(sb["avg"] - pa["avg"], 2)
            q = min(pa["filled"], sb["filled"])
            sign = 1 if pa["side"] == "buy" else -1                     # 매수는 비싸게 = 나쁨, 매도는 싸게 = 나쁨
            row["diff_won"] = round(-sign * row["diff"] * q, 0) + 0.0              # + 0.0 = '-0' 없애기
        if not pa:
            row["note"] = "시뮬레이션에만 신호"
        elif not sb:
            row["note"] = "모의에만 신호"
        elif pa["ordered"] and sb["ordered"] and bool(pa["filled"]) != bool(sb["filled"]):
            row["note"] = "한쪽만 체결"
        rows.append(row)
    ordered = [r for r in rows if r["paper"] and r["sim"] and r["paper"]["ordered"] and r["sim"]["ordered"]]
    return {"rows": rows, "signals_paper": len(a), "signals_sim": len(b), "paired": sum(1 for r in rows if r["paper"] and r["sim"]),
            "both_ordered": len(ordered),
            "both_filled": sum(1 for r in ordered if r["paper"]["filled"] and r["sim"]["filled"]),
            "one_filled": sum(1 for r in ordered if bool(r["paper"]["filled"]) != bool(r["sim"]["filled"])),
            "price_diffs": [r["diff"] for r in rows if r["diff"] is not None]}


def day_label(ymd: str) -> str:
    try:
        return datetime.strptime(ymd, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return ymd
