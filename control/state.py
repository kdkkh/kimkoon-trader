"""실행기 상태 읽기 — 모의(state/) · 시뮬레이션(state/sim/) · dry-run. 읽기만 하고 어떤 파일이 없거나 깨져도 예외를 내지 않는다.

돌려주는 것에는 계좌 · 주문번호 · 원응답(raw)을 넣지 않는다(포지션은 종목 · 수량 · 평균가, 진행 중 주문은 종목 · 방향 · 수량 · 상태만).
살아 있음 = 잠금 파일의 pid 가 살아 있음, 또는 heartbeat 가 40초 안(그 실행기의 연결 이름일 때 — 모의는 heartbeat.json,
  dry-run 은 heartbeat_dry.json 이고, 옛 판 dry-run 이 heartbeat.json 에 쓴 것은 broker 칸으로 가려낸다).
바깥이 산 것(door_state 의 owned)에는 장부(oms_*.json)의 수량 · 평균가를 붙인다(owned.json 에는 없다).
문(ext) 켜짐 = 오늘 마지막 start 사건의 ext 가 참 + 그 뒤 stop 사건 없음 + 살아 있음.
"""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from control.common import RUNNERS, known_target, log_path, now_ms, read_events, read_json, runner_paths, scrub

HB_MAX_AGE_S = 40
TARGETS = ("paper", "sim")


def _pid_alive_default(pid: int) -> bool:
    try:
        from strategy.live import pid_alive
        return pid_alive(pid)
    except Exception:
        return False


def alive(root: Path, target: str, max_age_s: float = HB_MAX_AGE_S, pid_alive=None) -> tuple[bool, str]:
    """(살아 있나, 이유). 시각은 늘 벽시계(heartbeat 는 실행기가 벽시계로 쓴다)."""
    try:
        p = runner_paths(root, target)
    except ValueError as e:
        return False, str(e)
    check = pid_alive or _pid_alive_default
    try:
        txt = p["lock"].read_text(encoding="utf-8").strip()
        pid = int(txt or 0)
        if pid and check(pid):
            return True, "실행 중(잠금 파일의 프로세스가 살아 있음)"
    except Exception:
        pass
    hb = read_json(p["heartbeat"])
    if not isinstance(hb, dict) or not hb.get("at"):
        return False, "실행기 꺼짐"
    if hb.get("broker") and hb.get("broker") not in p["brokers"]:
        return False, "실행기 꺼짐"                              # 같은 state 폴더의 다른 실행기(모의 · dry-run)
    try:
        age = time.time() - datetime.fromisoformat(str(hb["at"])).timestamp()
    except Exception:
        return False, "heartbeat 시각을 읽지 못함"
    return age <= max_age_s, f"마지막 신호 {max(age, 0):.0f}초 전"


def heartbeat(root: Path, target: str) -> dict:
    """heartbeat 의 보여 줄 칸만(종목 · 연결 · 진행 중 주문 수 · 시각)."""
    hb = read_json(runner_paths(root, target)["heartbeat"])
    if not isinstance(hb, dict):
        return {}
    codes = hb.get("codes") if isinstance(hb.get("codes"), list) else []
    return {"at": str(hb.get("at", ""))[:19], "broker": str(hb.get("broker", ""))[:20],
            "codes": [str(c) for c in codes][:50], "active_orders": hb.get("active_orders")}


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def book(root: Path, target: str) -> dict:
    """장부 파일(oms_*.json) → 포지션 · 진행 중 주문 · 오늘 진입 · 실현(주문번호 · raw 는 버림)."""
    d = read_json(runner_paths(root, target)["oms"])
    if not isinstance(d, dict):
        d = {}
    pos = {}
    for c, v in (d.get("positions") or {}).items() if isinstance(d.get("positions"), dict) else []:
        if isinstance(v, dict):
            q = int(_num(v.get("qty")))
            if q:
                pos[str(c)] = {"qty": q, "avg": round(_num(v.get("avg")), 2)}
    orders = []
    for o in d.get("orders") or [] if isinstance(d.get("orders"), list) else []:
        if isinstance(o, dict):
            orders.append({"code": str(o.get("code", "")), "side": str(o.get("side", "")),
                           "qty": int(_num(o.get("qty"))), "state": str(o.get("state", ""))})
    return {"day": str(d.get("day", "")), "positions": pos, "orders": orders,
            "entries_today": int(_num(d.get("entries_today"))), "realized": _num(d.get("realized")),
            "pending_exit": [str(x) for x in d.get("pending_exit") or [] if isinstance(x, str)]}


def _clean_summary(s) -> dict:
    if not isinstance(s, dict):
        return {}
    pos = {}
    for c, v in (s.get("positions") or {}).items() if isinstance(s.get("positions"), dict) else []:
        if isinstance(v, dict):
            pos[str(c)] = {"qty": int(_num(v.get("qty"))), "avg": round(_num(v.get("avg")), 2)}
    od = s.get("orders_done") if isinstance(s.get("orders_done"), dict) else {}
    return {"orders_done": {str(k): int(_num(v)) for k, v in od.items()}, "active": int(_num(s.get("active"))),
            "entries_today": int(_num(s.get("entries_today"))), "realized": _num(s.get("realized")),
            "unrealized": _num(s.get("unrealized")), "positions": pos}


def today_summary(root: Path, target: str, day: str | None = None) -> dict:
    """오늘 기록 요약: 마지막 stop 사건의 summary 가 있으면 그것, 없으면 사건 수."""
    p = log_path(root, target, day)
    ev = read_events(p)
    out = {"target": target, "day": day or datetime.now().strftime("%Y%m%d"), "exists": p.exists(),
           "lines": len(ev), "stop_summary": None, "stop_reason": None}
    c, done = Counter(), Counter()
    for r in ev:
        c[str(r.get("event"))] += 1
        if r.get("event") == "order_done":
            done[str(r.get("state"))] += 1
        if r.get("event") == "stop":
            out["stop_summary"] = _clean_summary(r.get("summary"))
            out["stop_reason"] = str(r.get("reason", ""))[:40]
    out["events"] = dict(c)
    out["orders_done"] = dict(done)
    return out


def last_event(root: Path, target: str, names, since_ms: int | None = None, day: str | None = None) -> dict | None:
    names = {names} if isinstance(names, str) else set(names)
    best = None
    for r in read_events(log_path(root, target, day), names):
        if since_ms is not None and int(_num(r.get("ts_ms"))) < since_ms:
            continue
        best = r
    return best


def recent_start(root: Path, target: str, days_back: int = 10) -> dict | None:
    """가장 최근 기록 파일(오늘부터 거슬러) 의 마지막 start 사건."""
    p = runner_paths(root, target)
    try:
        files = sorted(p["logs"].glob(f"{p['prefix']}2*.jsonl"), reverse=True)[:days_back]
    except Exception:
        return None
    for f in files:
        if target == "paper" and f.name.startswith("strategy_live_dry_"):
            continue
        ev = read_events(f, {"start"})
        if ev:
            return ev[-1]
    return None


def door_state(root: Path, target: str, now: int | None = None, pid_alive=None) -> dict:
    """문 상태: 켜짐 여부 · 지금 걸린 거르기 · 검토 표시 · 바깥이 산 것(수량은 장부에서) · inbox 에 남은 신호 수."""
    now = now if now is not None else now_ms()
    p = runner_paths(root, target)
    ev = read_events(log_path(root, target), {"start", "stop"})
    starts = [e for e in ev if e.get("event") == "start"]
    last = starts[-1] if starts else None
    ok, why_alive = alive(root, target, pid_alive=pid_alive)
    if last is None:
        is_open, why = False, "오늘 실행기 시작 기록 없음"
    elif not last.get("ext"):
        is_open, why = False, "실행기가 문 없이(--ext 없이) 시작함"
    elif any(e.get("event") == "stop" and _num(e.get("ts_ms")) >= _num(last.get("ts_ms")) for e in ev):
        is_open, why = False, "실행기가 끝남"
    elif not ok:
        is_open, why = False, f"실행기 응답 없음({why_alive})"
    else:
        is_open, why = True, "켜짐"
    ext = p["ext"]
    gates = []
    try:
        gfiles = sorted(ext.joinpath("gates").glob("*.json"))
    except Exception:
        gfiles = []
    for g in gfiles:
        d = read_json(g)
        if isinstance(d, dict) and d.get("block_entries") and _num(d.get("expires_ms")) > now:
            gates.append({"name": g.stem, "reason": scrub(str(d.get("reason", ""))[:80]),
                          "expires_ms": int(_num(d.get("expires_ms"))),
                          "codes": [str(x) for x in d.get("codes") or []] if isinstance(d.get("codes"), list) else None})
    rv = read_json(ext / "review.json", default=None) if (ext / "review.json").exists() else None
    review = None
    if rv is not None:
        rv = rv if isinstance(rv, dict) else {}
        review = {"alive": _num(rv.get("expires_ms")) > now, "on_timeout": "pass" if rv.get("on_timeout") == "pass" else "skip",
                  "codes": rv.get("codes") if isinstance(rv.get("codes"), list) else None}
    owned_raw = read_json(ext / "owned.json")
    pos = book(root, target)["positions"]
    owned = {}
    if isinstance(owned_raw, dict):
        for c, o in owned_raw.items():
            o = o if isinstance(o, dict) else {}
            owned[str(c)] = {"source": str(o.get("source", ""))[:40], "exit_by": str(o.get("exit_by", "")),
                             "max_hold_min": o.get("max_hold_min"), "stop_pct": o.get("stop_pct"),
                             "t_ms": int(_num(o.get("t_ms"))), "qty": (pos.get(str(c)) or {}).get("qty", 0),
                             "avg": (pos.get(str(c)) or {}).get("avg")}

    def _count(sub):
        try:
            return len(list(ext.joinpath(sub).glob("*.json")))
        except Exception:
            return 0
    return {"target": target, "open": is_open, "why": why, "exists": ext.exists(), "gates": gates, "review": review,
            "review_requests": _count("review_requests"), "inbox_waiting": _count("inbox"), "owned": owned,
            "start_ext": bool(last.get("ext")) if last else None}


def runner_codes(root: Path, target: str) -> list[str]:
    """실행기가 지금 보는 종목: heartbeat → 오늘 start 사건 순(없으면 빈 목록)."""
    hb = heartbeat(root, target)
    if hb.get("codes"):
        return hb["codes"]
    st = last_event(root, target, "start")
    if st and isinstance(st.get("codes"), list):
        return [str(c) for c in st["codes"]]
    return []


def runner_status(root: Path, target: str, pid_alive=None) -> dict:
    p = runner_paths(root, target)
    ok, why = alive(root, target, pid_alive=pid_alive)
    b = book(root, target)
    return {"target": target, "label": p["label"], "alive": ok, "why": why, "stop_file": p["stop"].exists(),
            "positions": b["positions"], "orders": b["orders"], "entries_today": b["entries_today"],
            "realized": b["realized"], "book_day": b["day"], "codes": runner_codes(root, target),
            "door": door_state(root, target, pid_alive=pid_alive)}


def sim_targets(root: Path) -> list[str]:
    """state 아래 시뮬레이션 폴더(sim · sim_<태그>) 중 있는 것."""
    out = ["sim"]
    try:
        for d in sorted((Path(root) / "state").glob("sim_*")):
            if d.is_dir() and d.name != "sim_once" and known_target(d.name):     # sim_once = 한 번 시험(--once)
                out.append(d.name)
    except Exception:
        pass
    return out


def held_codes(root: Path, pid_alive=None) -> dict[str, list[str]]:
    """보유 중이거나 진행 중 주문이 있는 종목 → 그 실행기들. config.yaml 은 모의 · 시뮬레이션이 함께 읽으므로 둘 다 본다.
    dry-run 장부는 그 실행기가 돌고 있을 때만 센다(계획만 하는 가짜 포지션이라)."""
    out: dict[str, list[str]] = {}
    targets = ["paper"] + sim_targets(root)
    if alive(root, "dry", pid_alive=pid_alive)[0]:
        targets.append("dry")
    for t in targets:
        b = book(root, t)
        for c in list(b["positions"]) + [o["code"] for o in b["orders"] if o["code"]]:
            out.setdefault(c, [])
            if t not in out[c]:
                out[c].append(t)
    return out


def running_targets(root: Path, pid_alive=None, include_dry: bool = True) -> list[str]:
    """돌고 있는 실행기. include_dry=False 면 모의 · 시뮬레이션만(AI 판단의 기본 대상 — dry-run 은 계획만 하는
    시험 실행기이고 문 폴더가 모의와 같아서 넣지 않는다)."""
    ts = ["paper"] + sim_targets(root) + (["dry"] if include_dry else [])
    return [t for t in ts if alive(root, t, pid_alive=pid_alive)[0]]


def overview(root: Path, pid_alive=None) -> dict:
    return {t: runner_status(root, t, pid_alive=pid_alive) for t in TARGETS}


__all__ = ["alive", "heartbeat", "book", "today_summary", "last_event", "recent_start", "door_state",
           "runner_codes", "runner_status", "held_codes", "running_targets", "overview", "sim_targets", "RUNNERS"]
