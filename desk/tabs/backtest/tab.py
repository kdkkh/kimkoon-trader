"""백테스트 탭 — 지난 결과 보기(재생 백테스트 · 지난 실시간 시뮬레이션 · 봉 백테스트)와 실행 단추(봉 백테스트 · 트레이딩뷰 대조 검증기).

읽는 것(뿌리 폴더 아래, 읽기만)
  재생 백테스트   data/bt_* · data/cmp_* · data/ref_*/* 가운데 round_trips.csv 가 있는 폴더(BOM 이 붙어 있다) —
                  체결 백테스트(scripts/backtest_ticks.py) · 봉 재생(scripts/bar_replay.py) 이 같은 꼴로 쓴다(판에 있는 것만)
  지난 시뮬레이션 logs/sim*/strategy_live_*.jsonl 의 stop.summary + 체결 사건
  봉 백테스트     data/backtest/*_요약.json (+ 같은 이름의 _거래.csv)
기록 파일 이름의 날짜는 '돌린 날'이다(재현 백테스트) — 화면에는 폴더 이름과 기록 안의 거래일을 함께 보인다.

실행(자식 프로세스 — 화면 프로세스 안에서 백테스트를 import 해 돌리지 않는다)
  봉 백테스트  scripts/strategy_backtest.py --csv <받아 둔 봉 CSV> (API 호출 없음, 기본)
              scripts/strategy_backtest.py --code … --tf 1d --days … (한투 시세 조회 — 확인 창 + 실행기가 돌고 있으면 막음)
  검증기      scripts/strategy_validate.py <등록된 전략 이름> [--csv …] (합성 봉, API 호출 없음)
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

from control import settings_edit, state
from control.common import runner_paths
from desk.core import runlog, tail

ID = "backtest"
NAME = "백테스트"
PUBLIC = True
NEEDS = ()

FILL_GLOBS = ("data/bt_*", "data/cmp_*", "data/ref_*/*")       # ref_* = 재현 기준 폴더
SIM_GLOB = "logs/sim*/strategy_live_*.jsonl"
BAR_GLOB = "data/backtest/*_요약.json"
BARS_DIR = "data/bars"
CODE_RX = re.compile(r"^\d{6}$")
PARAM_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,30}=[-A-Za-z0-9_.]{1,20}$")
DATE_RX = re.compile(r"(\d{8})")
MAX_ROWS = 2000


def _rel(root: Path, p: Path) -> str:
    return p.resolve().relative_to(root.resolve()).as_posix()


def _ms_text(v) -> str:
    try:
        return datetime.fromtimestamp(int(float(v)) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def read_round_trips(p: Path) -> list[dict]:
    """round_trips.csv(BOM 있음 · 없음 모두) → 행 목록(숫자로)."""
    try:
        text = Path(p).read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return []
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        out.append({"code": code.zfill(6) if code.isdigit() else code, "t_entry_ms": int(_num(r.get("t_entry_ms"))),
                    "t_exit_ms": int(_num(r.get("t_exit_ms"))), "qty": int(_num(r.get("qty"))),
                    "entry": _num(r.get("entry")), "exit": _num(r.get("exit")), "pnl": _num(r.get("pnl")),
                    "ret_pct": _num(r.get("ret_pct"))})
    return out


def _head_events(p: Path, n: int = 6) -> list[dict]:
    r = tail.read_from(p, 0, max_bytes=16 * 1024)
    return r["lines"][:n]


def _tail_stop(p: Path) -> dict | None:
    """기록 끝 16KB 에서 마지막 stop 사건(파일 전체를 읽지 않게)."""
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            f.seek(max(size - 16 * 1024, 0))
            chunk = f.read()
    except OSError:
        return None
    stop = None
    for raw in chunk.split(b"\n"):
        if b'"stop"' not in raw:
            continue
        try:
            e = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if e.get("event") == "stop":
            stop = e
    return stop


def _fill_logs(folder: Path) -> list[Path]:
    """체결 백테스트 폴더 안 기록: <폴더>/<all 또는 종목>/strategy_live_*.jsonl."""
    try:
        return sorted(folder.glob("*/strategy_live_*.jsonl"))
    except OSError:
        return []


def _trade_day_of(p: Path) -> str:
    ev = _head_events(p)
    return runlog.trade_day(ev)


def _summ_trips(rows: list[dict]) -> dict:
    n = len(rows)
    pnl = sum(r["pnl"] for r in rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    by = {}
    for r in rows:
        b = by.setdefault(r["code"], {"code": r["code"], "n": 0, "pnl": 0.0, "wins": 0})
        b["n"] += 1
        b["pnl"] += r["pnl"]
        b["wins"] += r["pnl"] > 0
    per = sorted(({**b, "pnl": round(b["pnl"], 0)} for b in by.values()), key=lambda x: x["pnl"])
    return {"trades": n, "pnl": round(pnl, 0), "wins": wins, "win_pct": round(wins / n * 100, 1) if n else None,
            "avg_ret_pct": round(sum(r["ret_pct"] for r in rows) / n, 4) if n else None, "codes": len(by), "per_code": per}


# ---------- 목록 ----------
def _list_fill(root: Path) -> list[dict]:
    out = []
    for g in FILL_GLOBS:
        for d in sorted(root.glob(g)):
            rt = d / "round_trips.csv"
            if not d.is_dir() or not rt.is_file():
                continue
            rows = read_round_trips(rt)
            logs = _fill_logs(d)
            run_day = ""
            if logs:
                m = DATE_RX.search(logs[0].stem)
                run_day = runlog.day_label(m.group(1)) if m else ""
            out.append({"kind": "fill", "id": _rel(root, d), "name": d.name, "group": d.parent.name,
                        "trade_day": _trade_day_of(logs[0]) if logs else "", "run_day": run_day,
                        "trades": len(rows), "pnl": round(sum(r["pnl"] for r in rows), 0),
                        "folders": len({p.parent.name for p in logs})})
    return out


def _list_sim(root: Path) -> list[dict]:
    out = []
    for p in sorted(root.glob(SIM_GLOB)):
        st = _tail_stop(p)
        s = st.get("summary") if st and isinstance(st.get("summary"), dict) else {}
        m = DATE_RX.search(p.stem)
        od = s.get("orders_done") if isinstance(s.get("orders_done"), dict) else {}
        out.append({"kind": "sim", "id": _rel(root, p), "name": f"{p.parent.name} · {p.stem[-8:]}", "group": p.parent.name,
                    "trade_day": runlog.day_label(m.group(1)) if m else "", "run_day": runlog.day_label(m.group(1)) if m else "",
                    "ended": st is not None, "realized": _num(s.get("realized")) if s else None,
                    "done_filled": int(_num(od.get("filled"))), "cancelled": int(_num(od.get("cancelled")))})
    return out


def _list_bar(root: Path) -> list[dict]:
    out = []
    for p in sorted(root.glob(BAR_GLOB), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        s = d.get("summary") if isinstance(d.get("summary"), dict) else {}
        m = re.search(r"_(\d{8})_(\d{6})_요약$", p.stem)
        out.append({"kind": "bar", "id": _rel(root, p), "name": p.stem[: -len("_요약")], "group": "backtest",
                    "trade_day": str(s.get("기간", "")), "run_day": runlog.day_label(m.group(1)) if m else "",
                    "code": str(d.get("code", "")), "tf": str(d.get("tf", "")), "strategy": str(d.get("strategy", "")),
                    "trades": s.get("닫힌 거래 수"), "pnl": s.get("총 손익(원)")})
    return out


def get_list(ctx, req):
    root = ctx.root
    return {"ok": True, "fill": _list_fill(root), "sim": _list_sim(root), "bar": _list_bar(root),
            "note": "재생 백테스트 기록 파일 이름의 날짜는 돌린 날 — 거래일은 기록 안의 날짜(봉 시각)를 따로 보입니다. "
                    "손익은 수수료 · 세금 전입니다."}


def _find(root: Path, kind: str, rid: str) -> Path | None:
    """목록에 있는 것만 연다(경로 조작 막기)."""
    items = {"fill": _list_fill, "sim": _list_sim, "bar": _list_bar}.get(kind)
    if items is None:
        return None
    for it in items(root):
        if it["id"] == rid:
            return root / rid
    return None


def get_detail(ctx, req):
    kind, rid = str(req.get("kind") or ""), str(req.get("id") or "")
    p = _find(ctx.root, kind, rid)
    if p is None:
        return 404, {"ok": False, "error": "목록에 없는 결과"}
    if kind == "fill":
        rows = read_round_trips(p / "round_trips.csv")
        logs = _fill_logs(p)
        stops = [s for s in (_tail_stop(x) for x in logs[:1]) if s] if len(logs) == 1 else []
        days = sorted({d for d in (_trade_day_of(x) for x in logs[:3]) if d})
        trades = [{**r, "entry_at": _ms_text(r["t_entry_ms"]), "exit_at": _ms_text(r["t_exit_ms"])} for r in rows[:MAX_ROWS]]
        return {"ok": True, "kind": kind, "id": rid, "name": p.name, "trade_days": days,
                "run_day": runlog.day_label(DATE_RX.search(logs[0].stem).group(1)) if logs and DATE_RX.search(logs[0].stem) else "",
                "folders": len(logs), "totals": _summ_trips(rows), "trades": trades, "cut": max(len(rows) - MAX_ROWS, 0),
                "stop": (stops[0].get("summary") if stops else None)}
    if kind == "sim":
        ev = tail.read_all(p)
        rows = runlog.round_trips(ev)
        s = runlog.summarize(ev, ctx.cfg.get("fees") if isinstance(ctx.cfg.get("fees"), dict) else {})
        trades = [{**r, "entry_at": _ms_text(r["t_entry_ms"]), "exit_at": _ms_text(r["t_exit_ms"])} for r in rows]
        st = runlog.last_stop(ev)
        return {"ok": True, "kind": kind, "id": rid, "name": p.name, "trade_days": [s["trade_day"]] if s["trade_day"] else [],
                "run_day": s["trade_day"], "totals": _summ_trips(rows), "trades": trades, "cut": 0,
                "stop": st.get("summary") if st else None, "summary": s}
    # bar
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    tr = p.with_name(p.name.replace("_요약.json", "_거래.csv"))
    rows = []
    if tr.is_file():
        try:
            rows = list(csv.DictReader(io.StringIO(tr.read_bytes().decode("utf-8-sig", errors="replace"))))[:MAX_ROWS]
        except OSError:
            rows = []
    return {"ok": True, "kind": kind, "id": rid, "name": p.stem, "summary": d.get("summary") or {}, "code": d.get("code"),
            "tf": d.get("tf"), "strategy": d.get("strategy"), "params": d.get("params"), "pending": d.get("pending"),
            "api_calls": d.get("api_calls"), "bar_trades": rows}


# ---------- 실행 ----------
def _bar_csvs(root: Path) -> list[str]:
    try:
        return sorted(p.name for p in (root / BARS_DIR).glob("*.csv") if p.is_file())
    except OSError:
        return []


def _running_runners(root: Path) -> list[str]:
    return [runner_paths(root, t)["label"] for t in state.running_targets(root)]


def get_options(ctx, req):
    try:
        strategies = sorted(settings_edit.registered_strategies())
    except Exception:
        strategies = []
    return {"ok": True, "csvs": _bar_csvs(ctx.root), "csv_dir": BARS_DIR, "strategies": strategies,
            "running": _running_runners(ctx.root),
            "scripts": {k: (ctx.root / "scripts" / f).is_file()
                        for k, f in (("bar", "strategy_backtest.py"), ("validate", "strategy_validate.py"))}}


def post_run(ctx, req):
    b = req.body
    kind = str(b.get("kind") or "")
    try:
        strategies = settings_edit.registered_strategies()
    except Exception:
        strategies = set()
    strat = str(b.get("strategy") or "")
    if strat not in strategies:
        return 400, {"ok": False, "message": "전략은 등록된 이름만(파일 경로는 받지 않음)"}
    py = [ctx.python, "-X", "utf8"]
    if kind in ("bar_csv", "bar_fetch"):
        script = ctx.root / "scripts" / "strategy_backtest.py"
        if not script.is_file():
            return 400, {"ok": False, "message": "스크립트 없음: scripts/strategy_backtest.py"}
        amount = _num(b.get("amount"), 1_000_000)
        if not 0 < amount <= 1_000_000_000:
            return 400, {"ok": False, "message": "금액은 0보다 큰 숫자"}
        params = [s.strip() for s in re.split(r"[,\s]+", str(b.get("params") or "")) if s.strip()]
        if any(not PARAM_RX.match(s) for s in params):
            return 400, {"ok": False, "message": "전략 값은 이름=값 꼴(쉼표로 여럿)"}
        args = py + [str(script)]
        if kind == "bar_csv":
            name = str(b.get("csv") or "")
            if name not in _bar_csvs(ctx.root):
                return 400, {"ok": False, "message": f"받아 둔 봉 CSV 가 아님({BARS_DIR} 안의 파일만)"}
            args += ["--csv", f"{BARS_DIR}/{name}"]
        else:
            if b.get("confirm") is not True:
                return 400, {"ok": False, "message": "확인 창을 거쳐야 합니다(한투 조회)"}
            run = _running_runners(ctx.root)
            if run:
                return 409, {"ok": False, "message": f"실행기가 돌고 있어 막음({', '.join(run)}) — 조회는 실행기와 같은 키 · 토큰을 씀"}
            code, days = str(b.get("code") or ""), int(_num(b.get("days"), 365))
            if not CODE_RX.match(code):
                return 400, {"ok": False, "message": "종목 코드를 넣어 주세요(6자리 숫자)"}
            if not 1 <= days <= 3650:
                return 400, {"ok": False, "message": "기간(일)은 1~3650"}
            args += ["--code", code, "--tf", "1d", "--days", str(days)]
        args += ["--strategy", strat, "--amount", f"{amount:.0f}"]
        for s in params:
            args += ["--param", s]
        label = "봉 백테스트(" + ("받아 둔 봉 · API 없음" if kind == "bar_csv" else "한투 조회") + ")"

        def done(job):
            m = re.search(r"저장: (data/backtest/\S+?)_거래목록\.csv", ctx.jobs.text("bar_backtest"))
            return {"saved": f"{m.group(1)}_요약.json"} if m else {}
        r = ctx.jobs.start("bar_backtest", args, cwd=ctx.root, label=label, on_done=done)
        return (200 if r["ok"] else 409 if r["status"] == "busy" else 500), r
    if kind == "validate":
        script = ctx.root / "scripts" / "strategy_validate.py"
        if not script.is_file():
            return 400, {"ok": False, "message": "스크립트 없음: scripts/strategy_validate.py"}
        args = py + [str(script), strat]
        name = str(b.get("csv") or "")
        if name:
            if name not in _bar_csvs(ctx.root):
                return 400, {"ok": False, "message": f"받아 둔 봉 CSV 가 아님({BARS_DIR} 안의 파일만)"}
            args += ["--csv", f"{BARS_DIR}/{name}"]

        def vdone(job):
            m = re.findall(r"⑤ 판정: (.+)", ctx.jobs.text("validate"))
            return {"verdict": m[-1].strip()[:80]} if m else {}
        r = ctx.jobs.start("validate", args, cwd=ctx.root, label="트레이딩뷰 대조 검증기(API 없음)", on_done=vdone)
        return (200 if r["ok"] else 409 if r["status"] == "busy" else 500), r
    return 400, {"ok": False, "message": "모르는 실행"}


def get_jobs(ctx, req):
    return {"ok": True, "jobs": ctx.jobs.all()}


ROUTES = {
    "GET list": get_list,
    "GET detail": get_detail,
    "GET options": get_options,
    "POST run": post_run,
    "GET jobs": get_jobs,
}
