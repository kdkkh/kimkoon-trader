"""매매 탭 — 실행기 상태(모의 · 시뮬레이션) · 켜기/끄기 · 오늘 요약 · 설정 바꾸기 · 사건 흐름 · 모의 대 시뮬레이션 비교 · 지난 날 보기.

상태 · 조작은 전부 control 함수로 한다(이 탭은 증권사 API 를 부르지 않고, 실행기에 직접 말하지 않는다).
  상태 control.state.runner_status · heartbeat · today_summary · 켜기/끄기 control.switch.check_start · start · stop
  설정 control.settings_edit.view · preview · edit(actor="desk")
기록은 logs/strategy_live_YYYYMMDD.jsonl(모의) · logs/sim/…(시뮬레이션)을 읽기만 한다.
"""
from __future__ import annotations

import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

from control import settings_edit, state, switch
from control.common import journal_today, known_target, log_path, read_events, runner_paths
from desk.core import runlog, tail

ID = "trade"
NAME = "매매"
PUBLIC = True
NEEDS = ()

TARGETS = ("paper", "sim")
DAY_RX = re.compile(r"^\d{8}$")
EVENT_LIMIT = 3000                    # 한 번에 보내는 사건 수 상한(봉 사건은 기본으로 빼므로 하루 수백 줄)
STATUS_TEXT = {
    "applied": "보냄 → 다음 봉에 적용됨",
    "saved": "실행기가 꺼져 있어 다음 시작 때 반영",
    "unconfirmed": "보냄 → 기다리는 동안 적용 기록을 못 봄(파일은 바뀜)",
    "rejected": "보냄 → 거부됨",
    "refused": "보내지 않음",
    "unchanged": "바뀐 것 없음",
    "busy": "다른 설정 변경이 진행 중",
}


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _day(req) -> str:
    d = str(req.get("day") or "").replace("-", "")
    return d if DAY_RX.match(d) else _today()


def _target(req, default="paper") -> str | None:
    t = str(req.get("target") or default)
    return t if t in TARGETS or (t.startswith("sim_") and known_target(t)) else None


# ---------- 상태 ----------
def _card(root: Path, t: str) -> dict:
    st = state.runner_status(root, t)
    hb = state.heartbeat(root, t)
    if hb.get("broker") and hb["broker"] not in runner_paths(root, t)["brokers"]:
        hb = {}                                               # 같은 state 폴더를 쓰는 다른 실행기(dry-run)의 것
    door = st.get("door") or {}
    return {"target": t, "label": st["label"], "alive": st["alive"], "why": st["why"], "stop_file": st["stop_file"],
            "heartbeat_at": hb.get("at", ""), "broker": hb.get("broker", ""), "codes": st["codes"],
            "active_orders": len(st["orders"]) if st["orders"] else int(hb.get("active_orders") or 0),
            "positions": st["positions"], "door_open": bool(door.get("open")), "door_why": door.get("why", "")}


def get_status(ctx, req):
    return {"ok": True, "now": datetime.now().strftime("%H:%M:%S"), "cards": [_card(ctx.root, t) for t in TARGETS]}


def get_days(ctx, req):
    days: dict[str, dict] = {}
    for t in TARGETS:
        p = runner_paths(ctx.root, t)
        try:
            files = list(p["logs"].glob(f"{p['prefix']}2*.jsonl"))
        except OSError:
            files = []
        for f in files:
            d = f.stem[len(p["prefix"]):]
            if DAY_RX.match(d):
                days.setdefault(d, {"day": d, "label": runlog.day_label(d), "paper": False, "sim": False})[t] = True
    out = sorted(days.values(), key=lambda x: x["day"], reverse=True)
    return {"ok": True, "today": _today(), "days": out}


# ---------- 요약 · 사건 · 비교 ----------
def _events(root: Path, t: str, day: str) -> list[dict]:
    return tail.read_all(log_path(root, t, day))


def get_summary(ctx, req):
    day = _day(req)
    fees = ctx.cfg.get("fees") if isinstance(ctx.cfg.get("fees"), dict) else {}
    out = []
    for t in TARGETS:
        ts = state.today_summary(ctx.root, t, day)            # control 쪽 요약(있음 · 줄 수 · 끝 요약)
        s = runlog.summarize(_events(ctx.root, t, day), fees) if ts["exists"] else None
        out.append({"target": t, "label": runner_paths(ctx.root, t)["label"], "exists": ts["exists"], "summary": s,
                    "orders_done": ts.get("orders_done", {})})
    return {"ok": True, "day": day, "label": runlog.day_label(day), "runners": out,
            "fees_set": runlog.fee_rates(fees)[0] is not None}


def get_events(ctx, req):
    t = _target(req)
    if t is None:
        return 400, {"ok": False, "error": "모르는 실행기"}
    day = _day(req)
    want_bars = str(req.get("bars") or "") == "1"
    r = tail.read_from(log_path(ctx.root, t, day), req.get("pos") or 0)
    lines = [runlog.show_event(e, mask=req.mask) for e in r["lines"] if want_bars or e.get("event") != "bar"]
    cut = max(len(lines) - EVENT_LIMIT, 0)
    return {"ok": True, "target": t, "day": day, "exists": r["exists"], "pos": r["pos"], "reset": r["reset"],
            "more": r["more"], "cut": cut, "lines": lines[cut:], "cats": runlog.CAT_NAMES}


def get_compare(ctx, req):
    day = _day(req)
    p, s = _events(ctx.root, "paper", day), _events(ctx.root, "sim", day)
    if not p or not s:
        return {"ok": True, "day": day, "rows": [], "message": "이 날은 모의 · 시뮬레이션 기록이 함께 있지 않음"}
    c = runlog.compare(p, s)
    return {"ok": True, "day": day, "message": "", **c}


# ---------- 설정 ----------
def get_settings(ctx, req):
    v = settings_edit.view(ctx.root)
    try:
        editable = [k for k in settings_edit.reloadable()]
    except Exception:
        editable = []
    try:
        strategies = sorted(settings_edit.registered_strategies())
    except Exception:
        strategies = []
    hist = [r for r in journal_today(ctx.root) if r.get("action") == "settings_edit"][-10:]
    recent = [{"at": str(r.get("at", ""))[11:19], "actor": r.get("actor"), "status": r.get("status"),
               "text": STATUS_TEXT.get(str(r.get("status")), str(r.get("status"))), "diff": r.get("diff") or {},
               "message": r.get("message", "")} for r in reversed(hist)]
    runner_ev = []
    for t in TARGETS:
        for e in read_events(log_path(ctx.root, t), {"settings_changed", "settings_rejected", "settings_partial"})[-5:]:
            runner_ev.append({"label": runner_paths(ctx.root, t)["label"], **runlog.show_event(e)})
    running = state.running_targets(ctx.root)
    return {"ok": v["ok"], "error": v.get("error", ""), "file": v.get("file"), "settings": v.get("settings", {}),
            "editable": [k for k in editable if k in (v.get("settings") or {}) or k == "apply_to_open"],
            "locked": [k for k in (v.get("settings") or {}) if k not in editable],
            "strategies": strategies, "modes": _modes(), "recent": recent, "runner_events": runner_ev,
            "running": [runner_paths(ctx.root, t)["label"] for t in running]}


def _modes() -> list[str]:
    """이 판에 있는 주문 방식(판마다 다르다 — execution.modes.MODES)."""
    try:
        from execution.modes import MODES
        return list(MODES)
    except Exception:
        return []


def _parse_changes(body) -> tuple[dict, str | None]:
    raw = body.get("changes") if isinstance(body, dict) else None
    if not isinstance(raw, dict) or not raw:
        return {}, "바꿀 칸이 없음"
    out = {}
    for k, v in raw.items():
        k = str(k)[:40]
        try:
            out[k] = settings_edit.parse_value(k, v) if isinstance(v, str) else v
        except ValueError as e:
            return {}, str(e)
    return out, None


def post_preview(ctx, req):
    ch, err = _parse_changes(req.body)
    if err:
        return {"ok": False, "message": err, "diff": {}}
    r = settings_edit.preview(ch, actor="desk", root=ctx.root)
    notes = []
    if ch.get("order_mode") in ("best", "escalate"):
        notes.append("한투 모의에서 최유리(best) 접수는 아직 확인하지 못했습니다")
    return {**r, "notes": notes}


def post_apply(ctx, req):
    ch, err = _parse_changes(req.body)
    if err:
        return {"ok": False, "status": "refused", "message": err}
    jid = f"set-{secrets.token_hex(4)}"
    jobs = ctx.store.setdefault("settings_jobs", {})
    jobs[jid] = {"state": "running", "result": None, "asked": list(ch)}

    def run():
        try:
            res = settings_edit.edit(ch, actor="desk", root=ctx.root)
        except Exception as e:                                 # edit 는 예외를 내지 않지만, 혹시라도 화면은 멈추지 않게
            res = {"ok": False, "status": "refused", "message": f"오류 {type(e).__name__}", "diff": {}}
        res = {**res, "text": STATUS_TEXT.get(res.get("status"), res.get("status"))}
        jobs[jid].update(state="done", result=res)

    threading.Thread(target=run, name=f"desk-{jid}", daemon=True).start()
    return {"ok": True, "job": jid, "message": "보냄 — 실행기 적용 기록을 기다리는 중"}


def get_apply_job(ctx, req):
    j = (ctx.store.get("settings_jobs") or {}).get(str(req.get("id") or ""))
    if j is None:
        return 404, {"ok": False, "error": "없는 작업"}
    return {"ok": True, "state": j["state"], "result": j["result"]}


# ---------- 켜기 · 끄기 ----------
def _until(ctx) -> str:
    v = settings_edit.view(ctx.root)
    u = str((v.get("settings") or {}).get("until") or "15:20")
    return u if re.match(r"^\d{2}:\d{2}$", u) else "15:20"


def get_start_check(ctx, req):
    t = _target(req)
    if t not in TARGETS:
        return 400, {"ok": False, "message": "켤 수 있는 실행기는 모의 · 시뮬"}
    r = switch.check_start(ctx.root, t, now=ctx.clock(), until=_until(ctx))
    return {**r, "confirm": f"{runner_paths(ctx.root, t)['label']} 실행기를 켭니다(run_daily.ps1). " + r["message"]}


def post_start(ctx, req):
    t = _target(req)
    if t not in TARGETS:
        return 400, {"ok": False, "message": "켤 수 있는 실행기는 모의 · 시뮬"}
    if req.body.get("confirm") is not True:
        return 400, {"ok": False, "message": "확인 창을 거쳐야 켭니다"}
    return switch.start(ctx.root, t, actor="desk", now=ctx.clock(), until=_until(ctx), launcher=ctx.launcher)


def post_stop(ctx, req):
    t = _target(req)
    if t not in TARGETS:
        return 400, {"ok": False, "message": "끌 수 있는 실행기는 모의 · 시뮬"}
    return switch.stop(ctx.root, t, actor="desk")


ROUTES = {
    "GET status": get_status,
    "GET days": get_days,
    "GET summary": get_summary,
    "GET events": get_events,
    "GET compare": get_compare,
    "GET settings": get_settings,
    "POST settings/preview": post_preview,
    "POST settings/apply": post_apply,
    "GET settings/job": get_apply_job,
    "GET start_check": get_start_check,
    "POST start": post_start,
    "POST stop": post_stop,
}
