"""실행기 끄기 · 켜기 — 화면 · 봇이 같은 함수를 쓴다.

끄기: 정지 파일(모의 state/stop · 시뮬레이션 state/sim/stop)을 만든다. 실행기가 새 주문을 멈추고 진행 중 주문을 정리한 뒤 끝낸다
      (프로세스를 강제로 끝내지 않는다).
켜기: 이미 돌고 있으면 거부 → 정지 파일이 있으면 지움 → 휴장일 표(holidays.yaml) + 장 시각(평일 08:30 ~ until) 확인
      → 예약 작업과 같은 방식으로 scripts/run_daily.ps1 을 분리 프로세스로 띄운다(시뮬레이션은 -Sim, 문은 -Ext).
      띄우는 함수(launcher)는 인자로 바꿔 끼울 수 있다 — 시험은 실제로 띄우지 않는다.
문 깃발(-Ext): ext 를 주지 않으면 ① 바깥이 산 종목 표시(owned.json)가 남아 있으면 켬(문 없이 뜨면 전략 청산이 그것을 판다)
      ② 아니면 가장 최근 start 사건의 ext 를 따른다 ③ 기록이 없으면 끔.
"""
from __future__ import annotations

import subprocess
from datetime import date, datetime
from pathlib import Path

from control import state
from control.common import ROOT, journal, now_ms, read_json, runner_paths, scrub

HOLIDAYS = Path(__file__).with_name("holidays.yaml")
OPEN_FROM = "08:30"                 # 예약 시작(08:50) 앞 여유 — 실행기는 첫 봉 마감까지 스스로 기다린다
START_TARGETS = ("paper", "sim")


def load_holidays(path: Path = HOLIDAYS) -> dict:
    """{'source', 'checked', 'years': {해: set(날짜)}}. 파일이 없거나 깨졌으면 빈 표(모두 '모름')."""
    out = {"source": "", "checked": "", "years": {}}
    try:
        import yaml
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
    except Exception:
        return out
    if not isinstance(d, dict):
        return out
    out["source"], out["checked"] = str(d.get("source") or ""), str(d.get("checked") or "")
    years = d.get("years") if isinstance(d.get("years"), dict) else {}
    for y, days in years.items():
        if isinstance(days, list):
            out["years"][str(y)] = {str(x) for x in days}
    return out


def holiday_status(day: date, path: Path = HOLIDAYS) -> tuple[str, str]:
    """('open' | 'closed' | 'unknown', 이유)."""
    if day.weekday() >= 5:
        return "closed", "주말"
    t = load_holidays(path)
    days = t["years"].get(str(day.year))
    if days is None:
        return "unknown", f"휴장일 표에 {day.year}년이 없음(모름 — 막지 않음)"
    if day.isoformat() in days:
        return "closed", "휴장일(휴장일 표)"
    return "open", f"휴장일 표 기준 개장일(확인 {t['checked'] or '날짜 없음'})"


def market_check(now: datetime | None = None, until: str = "15:20", holidays: Path = HOLIDAYS) -> tuple[bool, str, list[str]]:
    """(켜도 되나, 이유, 덧붙일 말). 표에 없는 해는 막지 않고 덧붙일 말에 '모름'을 적는다."""
    now = now or datetime.now()
    st, why = holiday_status(now.date(), holidays)
    notes = [why] if st == "unknown" else []
    if st == "closed":
        return False, f"장 시간이 아님({why})", notes
    hm = now.strftime("%H:%M")
    if not (OPEN_FROM <= hm < until):
        return False, f"장 시간이 아님(평일 {OPEN_FROM}~{until})", notes
    return True, "장 시간", notes


def stop(root: Path = ROOT, target: str = "paper", actor: str = "desk", pid_alive=None) -> dict:
    """정지 파일을 만든다. 반환 {ok, was_running, message}."""
    p = runner_paths(root, target)
    was, _ = state.alive(root, target, pid_alive=pid_alive)
    try:
        p["state"].mkdir(parents=True, exist_ok=True)
        p["stop"].write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        ok = True
        msg = (f"{p['label']}: 정지 파일을 만들었습니다 — "
               + ("실행기가 진행 중 주문을 정리하고 끝냅니다" if was else "실행기는 이미 꺼져 있습니다(이 파일이 남아 있는 동안 다음 시작도 막힙니다 · 켜기를 하면 지워집니다)"))
    except OSError as e:
        ok, msg = False, f"{p['label']}: 정지 파일을 만들지 못함 {scrub(type(e).__name__)}"
    journal(root, "stop", actor=actor, target=target, ok=ok, was_running=was, message=msg)
    return {"ok": ok, "was_running": was, "message": msg}


def default_ext(root: Path, target: str) -> bool:
    p = runner_paths(root, target)
    owned = read_json(p["ext"] / "owned.json")
    if isinstance(owned, dict) and owned:
        return True
    st = state.recent_start(root, target)
    return bool(st and st.get("ext"))


def check_start(root: Path = ROOT, target: str = "paper", now: datetime | None = None, until: str = "15:20",
                holidays: Path = HOLIDAYS, pid_alive=None, ext: bool | None = None) -> dict:
    """켜기 미리 보기(아무것도 바꾸지 않음). 반환 {ok, status(running|closed|ready|bad), message, notes, stop_file, ext}."""
    if target not in START_TARGETS:
        return {"ok": False, "status": "bad", "message": "켤 수 있는 실행기는 모의 · 시뮬", "notes": [],
                "stop_file": False, "ext": False}
    p = runner_paths(root, target)
    running, why = state.alive(root, target, pid_alive=pid_alive)
    ext = default_ext(root, target) if ext is None else bool(ext)
    base = {"stop_file": p["stop"].exists(), "ext": ext}
    if running:
        return {"ok": False, "status": "running", "message": f"{p['label']}: 이미 돌고 있습니다({why})", "notes": [], **base}
    ok, why, notes = market_check(now, until, holidays)
    if not ok:
        return {"ok": False, "status": "closed", "message": f"{p['label']}: {why}", "notes": notes, **base}
    return {"ok": True, "status": "ready", "message": f"{p['label']}: 띄울 수 있음", "notes": notes, **base}


def run_daily_args(root: Path, target: str, ext: bool) -> list[str]:
    ps1 = Path(root) / "scripts" / "run_daily.ps1"
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(ps1)]
    if target == "sim":
        args.append("-Sim")
    if ext:
        args.append("-Ext")
    return args


def default_launcher(args: list[str], cwd: Path) -> None:
    # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP — 창 없이, 띄운 쪽과 따로 산다.
    # DETACHED_PROCESS(콘솔 없음)로 띄우면 powershell.exe 가 스크립트를 돌리지 않고 종료 코드 0 으로 끝난다(2026-11-26 장중 재현).
    flags = 0x08000000 | 0x00000200
    null = subprocess.DEVNULL
    subprocess.Popen(args, cwd=str(cwd), creationflags=flags, close_fds=True, stdin=null, stdout=null, stderr=null)


def start(root: Path = ROOT, target: str = "paper", actor: str = "desk", now: datetime | None = None,
          until: str = "15:20", ext: bool | None = None, launcher=None, holidays: Path = HOLIDAYS, pid_alive=None) -> dict:
    """켜기. 반환 {ok, status(running|closed|launched|bad|error), message, notes, launched, ext, since_ms}.
    장 시간이 아니어도 정지 파일은 지운다(다음 예약 때 시작하게 — 예전 봇과 같은 동작)."""
    chk = check_start(root, target, now, until, holidays, pid_alive, ext)
    res = {**chk, "launched": False, "since_ms": now_ms()}
    if chk["status"] in ("running", "bad"):
        journal(root, "start", actor=actor, target=target, status=chk["status"], message=chk["message"])
        return res
    p = runner_paths(root, target)
    had = p["stop"].exists()
    if had:
        try:
            p["stop"].unlink()
        except OSError:
            pass
    if chk["status"] == "closed":
        res["message"] = chk["message"] + (" — 띄우지 않았습니다 · 정지 파일은 지웠습니다(다음 예약 때 시작)" if had
                                           else " — 띄우지 않았습니다")
        journal(root, "start", actor=actor, target=target, status="closed", stop_cleared=had, message=res["message"])
        return res
    args = run_daily_args(root, target, chk["ext"])
    try:
        (launcher or default_launcher)(args, Path(root))
        res.update(ok=True, status="launched", launched=True,
                   message=f"{p['label']}: 실행기를 띄웠습니다(run_daily.ps1{' -Sim' if target == 'sim' else ''}"
                           f"{' -Ext' if chk['ext'] else ''}) — 첫 봉 마감 뒤부터 움직입니다"
                           + (" · 정지 파일을 지웠습니다" if had else ""))
    except Exception as e:
        res.update(ok=False, status="error", message=f"{p['label']}: 띄우지 못함 {scrub(type(e).__name__)}")
    journal(root, "start", actor=actor, target=target, status=res["status"], ext=chk["ext"], stop_cleared=had,
            notes=chk["notes"], message=res["message"])
    return res


def start_result(root: Path, target: str, since_ms: int) -> dict | None:
    """띄운 뒤 실행기 기록에 start / start_refused 가 남았는지. None = 아직 없음."""
    ev = state.last_event(root, target, {"start", "start_refused"}, since_ms=since_ms)
    if ev is None:
        return None
    if ev.get("event") == "start":
        return {"ok": True, "message": f"{runner_paths(root, target)['label']}: 실행기가 시작했습니다"
                                       f"(문 {'켜짐' if ev.get('ext') else '꺼짐'})"}
    return {"ok": False, "message": f"{runner_paths(root, target)['label']}: 실행기가 시작을 거부함 — "
                                    f"{scrub(str(ev.get('detail') or ev.get('reason') or ''))[:120]}"}
