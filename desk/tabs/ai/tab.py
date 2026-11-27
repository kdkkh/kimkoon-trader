"""AI 판단 탭 — 판단 정의 목록 · 판단 프로세스 상태 · 자료 스위치 · 지금 걸린 거르기 · 판단 기록 · '한 번 묻기'.

읽는 것(뿌리 폴더 아래, 읽기만)
  judgments/*.yaml (+ *.example.yaml 견본)   judge.defs 로 검사 — 판단 프로세스가 안 돌아도 목록이 보인다
  judge.yaml                                전역 설정(없으면 보수적 기본값). 스위치는 화면에서 바꾸지 않는다
  state/judge.lock · state/judge_status.json  판단 프로세스가 도는지 · 마지막 상태
  logs/judge_YYYYMMDD.jsonl                 판단 기록(새 줄만 받아 붙임)
  control.state.door_state                  지금 걸린 거르기 · 검토 표시 · 바깥이 산 것(모의 · 시뮬레이션 문 각각)
쓰는 것(단추를 눌렀을 때만)
  '한 번 묻기' — 판단 프로세스가 돌면 state/judge_ask/<이름>.json 을 쓰고(다음 바퀴에 처리),
                 안 돌면 scripts/judge_run.py --once 를 자식 프로세스로 띄운다. 가짜 응답은 --fake --dry(기록만).
화면의 답에는 늘 'AI 판단' 표시를 붙인다. 판단 결과는 매수 · 매도 권유가 아니다.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import desk
from desk.core import mine, tail

CODE_ROOT = Path(desk.__file__).resolve().parents[1]           # 이 프로그램 폴더(scripts/judge_run.py 가 있는 곳)

ID = "ai"
NAME = "AI 판단"
PUBLIC = True
NEEDS = ()

MINE = mine.load(__file__)
FRAME_SRC = mine.frames(MINE)

TARGETS = (("paper", "모의"), ("sim", "시뮬레이션"))
DAY_RX = re.compile(r"^\d{8}$")
CODE_RX = re.compile(r"^[0-9A-Z]{6}$")
SWITCH_NAMES = {"disclosures": "공시(OpenDART)", "bars_raw": "시세 원값(원 단위 봉)", "news_titles": "뉴스 제목(한투)",
                "browser": "브라우저로 읽은 값", "signals": "전략 신호", "positions": "실행기 상태", "text": "넘겨받은 글"}
MARK = "AI 판단"


# ---------- 읽기 도우미 ----------
def _defs(root: Path) -> tuple[list[dict], list[dict]]:
    """(정의 목록, 거부 목록). 사용자 정의(judgments/*.yaml)가 먼저, 같은 id 가 없는 견본(*.example.yaml)은 '견본' 표시로."""
    from judge import defs as jdefs
    folder = root / "judgments"
    user, errs = jdefs.load_all(folder, examples=False)
    out = [dict(d, _example=False) for d in user]
    errors = [{"file": f, "error": e} for f, e in errs]
    ids = {d["id"] for d in user}
    for p in jdefs.files_in(folder, examples=True):
        if not p.name.endswith(".example.yaml"):
            continue
        try:
            d = jdefs.load_file(p)
        except jdefs.DefError as e:
            errors.append({"file": p.name, "error": str(e)})
            continue
        except OSError as e:
            errors.append({"file": p.name, "error": f"읽지 못함({type(e).__name__})"})
            continue
        if d["id"] in ids:
            continue
        ids.add(d["id"])
        out.append(dict(d, _example=True))
    return out, errors


def _global(root: Path) -> tuple[dict, str]:
    from judge import limits
    try:
        return limits.load_global(root), ""
    except limits.GlobalError as e:
        g = limits.load_global(root, path=root / "_없는_judge.yaml")
        return g, str(e)


def _when_text(w: dict) -> str:
    k = w.get("_kind")
    if k == "at":
        return f"{w.get('at')} 한 번"
    if k == "every_min":
        return f"{w.get('every_min')}분마다({w.get('from')}~{w.get('until')})"
    if k == "new_news":
        return "새 뉴스 제목이 붙었을 때"
    if k == "signal":
        return "전략 신호가 났을 때(" + "·".join(w.get("kinds") or []) + ")"
    if k == "review":
        return f"진입 검토 요청(답 없으면 {'진입 안 함' if w.get('on_timeout') == 'skip' else '진입'})"
    return str(k)


USE_NAMES = {"signal": "신호", "gate": "거르기(새 진입 막음)", "settings": "설정 내리기", "notify": "알림만", "review": "검토 답"}
INPUT_NAMES = {"bars": "1분봉", "signals": "전략 신호", "news_titles": "뉴스 제목", "disclosures": "공시 목록",
               "browser": "브라우저 값", "positions": "실행기 상태", "text": "넘겨받은 글"}


def _lock_pid(root: Path) -> tuple[bool, int]:
    p = root / "state" / "judge.lock"
    try:
        pid = int(p.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return False, 0
    try:
        from strategy.live import pid_alive
        return bool(pid and pid_alive(pid)), pid
    except Exception:
        return False, pid


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _ms_text(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _key_present(name: str) -> bool:
    """키 파일이 있는지만 본다(열지 않는다). 위치 = KIS_CONFIG_DIR, 없으면 홈 폴더 KIS/config."""
    d = os.environ.get("KIS_CONFIG_DIR")
    folder = Path(d) if d else Path.home() / "KIS" / "config"
    try:
        return (folder / name).is_file()
    except OSError:
        return False


def _answer_text(a) -> str:
    if not isinstance(a, dict):
        return ""
    if "answer" in a:
        return str(a["answer"])
    if "score" in a:
        return f"{a['score']}점"
    return ""


# ---------- 목록 · 상태 ----------
def get_overview(ctx, req):
    root = ctx.root
    from judge import limits, record
    g, gerr = _global(root)
    defs, def_errors = _defs(root)
    today = record.read_day(root, _today())
    use = limits.usage_of(today)
    last, cost_by = {}, {}
    for r in today:
        if r.get("event") == "judge":
            last[r.get("judge")] = r
            if isinstance(r.get("cost_est"), (int, float)):
                cost_by[r.get("judge")] = cost_by.get(r.get("judge"), 0.0) + float(r["cost_est"])
    rows = []
    for d in defs:
        lr = last.get(d["id"])
        sw_off = [sw for (_k, sw, opt) in _switches_needed(d) if not g["send"].get(sw)]
        rows.append({
            "id": d["id"], "file": d["_file"], "example": d["_example"], "enabled": d["enabled"], "note": d.get("note", ""),
            "per_code": d["per_code"], "when": [_when_text(w) for w in d["when"]],
            "inputs": [INPUT_NAMES.get(x["kind"], x["kind"]) + (" · 원값" if x.get("form") == "raw" else "")
                       + (" (선택)" if x.get("optional") else "") for x in d["inputs"]],
            "uses": [USE_NAMES.get(u["kind"], u["kind"]) for u in d["use"]],
            "prov": d["model"]["provider"], "model": d["model"]["name"],
            "calls_today": use["per_judge"].get(d["id"], 0), "daily_calls": d["limits"]["daily_calls"],
            "cost_today": round(cost_by.get(d["id"], 0.0), 6),
            "switch_off": [SWITCH_NAMES.get(s, s) for s in sw_off],
            "last": ({"at": _ms_text(lr.get("ts_ms")), "answer": _answer_text(lr.get("answer")), "valid": lr.get("valid"),
                      "invalid_reason": lr.get("invalid_reason") or "", "fake": bool(lr.get("fake")),
                      "outputs": [f"{o.get('kind')}:{o.get('status')}" for o in lr.get("outputs") or []]} if lr else None),
        })
    running, pid = _lock_pid(root)
    st = _read_json(root / "state" / "judge_status.json")
    status = None
    if isinstance(st, dict):
        status = {"updated": _ms_text(st.get("updated_ms")), "started": _ms_text(st.get("started_ms")),
                  "age_s": round(time.time() - int(st.get("updated_ms") or 0) / 1000, 0) if st.get("updated_ms") else None,
                  "dry": bool(st.get("dry")), "fake": bool(st.get("fake")), "open_targets": st.get("open_targets") or []}
    switches = [{"key": k, "label": SWITCH_NAMES.get(k, k), "on": bool(v)} for k, v in g["send"].items()]
    switches.append({"key": "record_titles", "label": "판단 기록에 뉴스 제목 원문을 남김", "on": bool(g["record_titles"])})
    stopped = use["limit_hit"] or bool(limits.over(use, g))
    out = {
        "ok": True, "mark": MARK,
        "process": {"running": running, "status": status,
                    "how": "켜기: python -X utf8 scripts/judge_run.py · 멈추기: state/judge.stop 파일"},
        "global": {"file": g.get("_file") or "", "file_note": "judge.yaml 없음 — 보수적 기본값(judge.example.yaml 참고)" if not g.get("_file") else "",
                   "error": gerr, "ai_enabled": bool(g["ai_enabled"]), "ack": bool(g["ack"]),
                   "daily_calls": g["limits"]["daily_calls"], "daily_usd": g["limits"]["daily_usd"],
                   "calls_today": use["calls"], "usd_today": use["usd"], "stopped": stopped,
                   "stopped_reason": use["limit_reason"] or (limits.over(use, g) or ""),
                   "prices_checked": g["prices"].get("checked") or "", "opendart_cap": g["opendart"]["daily_cap"]},
        "switches": switches,
        "switch_note": "스위치는 화면에서 바꾸지 않습니다 — judge.yaml 의 send · record_titles 칸을 직접 고치세요.",
        "keys": {"anthropic": _key_present("anthropic.yaml")},
        "defs": rows, "def_errors": def_errors,
        "doors": _doors(ctx),
        "mine": mine.infos(ctx, MINE),
        "note": "판단 결과는 'AI 판단'이며 매수 · 매도 권유가 아닙니다. 모의투자로만 검증한 기능입니다.",
    }
    return out


def _switches_needed(d: dict):
    try:
        from judge import defs as jdefs
        return jdefs.switches_needed(d)
    except Exception:
        return []


def _doors(ctx) -> list[dict]:
    from control import state
    now = int(time.time() * 1000)
    out = []
    for t, label in TARGETS:
        try:
            ds = state.door_state(ctx.root, t)
        except Exception as e:
            out.append({"target": t, "label": label, "error": f"{type(e).__name__}"})
            continue
        gates = [{"gate": g.get("name"), "reason": g.get("reason") or "", "codes": g.get("codes") or [],
                  "left_s": max(0, round((int(g.get("expires_ms") or 0) - now) / 1000)),
                  "judge": _judge_of(g.get("name"))} for g in ds.get("gates") or []]
        rv = ds.get("review") or {}
        owned = [{"code": c, "source": v.get("source"), "exit_by": v.get("exit_by"), "max_hold_min": v.get("max_hold_min"),
                  "stop_pct": v.get("stop_pct"), "qty": v.get("qty"), "since": _ms_text(v.get("t_ms"))}
                 for c, v in (ds.get("owned") or {}).items() if isinstance(v, dict)]
        out.append({"target": t, "label": label, "open": bool(ds.get("open")), "why": ds.get("why") or "",
                    "gates": gates, "review": {"alive": bool(rv.get("alive")), "on_timeout": rv.get("on_timeout"),
                                               "codes": rv.get("codes") or []} if rv else None,
                    "review_requests": len(ds.get("review_requests") or []) if isinstance(ds.get("review_requests"), list)
                    else int(ds.get("review_requests") or 0),
                    "inbox_waiting": ds.get("inbox_waiting") or 0, "owned": owned})
    return out


def _judge_of(name) -> str:
    """거르기 이름 <판단 id> · <판단 id>-<종목> → 판단 id."""
    s = str(name or "")
    m = re.match(r"^(.+)-([0-9A-Z]{6})$", s)
    return m.group(1) if m else s


# ---------- 판단 기록 ----------
def _rec_path(root: Path, day: str) -> Path:
    return root / "logs" / f"judge_{day}.jsonl"


def _days(root: Path) -> list[str]:
    try:
        ds = sorted({p.stem[len("judge_"):] for p in (root / "logs").glob("judge_*.jsonl")
                     if DAY_RX.match(p.stem[len("judge_"):])}, reverse=True)
    except OSError:
        ds = []
    t = _today()
    return ds if t in ds else [t] + ds


def _row(r: dict, n: int) -> dict:
    trig = r.get("trigger") if isinstance(r.get("trigger"), dict) else {}
    ev = r.get("event")
    row = {"n": n, "at": str(r.get("at") or "")[:19], "event": ev, "judge": r.get("judge") or "",
           "trigger": trig.get("kind") or "", "codes": [str(c) for c in trig.get("codes") or []][:10]}
    if ev == "judge":
        row.update(ref=r.get("ref") or "", answer=_answer_text(r.get("answer")), valid=bool(r.get("valid")),
                   invalid_reason=r.get("invalid_reason") or "", prov=r.get("provider") or "",
                   fake=bool(r.get("fake")), dry=bool(r.get("dry")), cost=r.get("cost_est"),
                   outputs=[f"{o.get('kind')}:{o.get('status')}" for o in r.get("outputs") or []], calls=r.get("calls"))
    else:
        row["reason"] = str(r.get("reason") or r.get("error") or "")[:200]
    return row


def get_records(ctx, req):
    day = str(req.get("day") or _today())
    if not DAY_RX.match(day):
        return 400, {"ok": False, "error": "날짜는 YYYYMMDD"}
    r = tail.read_from(_rec_path(ctx.root, day), req.get("pos") or 0)
    base = int(req.get("n0") or 0) if not r["reset"] else 0
    rows = [_row(x, base + i) for i, x in enumerate(r["lines"])]
    return {"ok": True, "day": day, "days": _days(ctx.root), "pos": r["pos"], "reset": r["reset"], "more": r["more"],
            "exists": r["exists"], "rows": rows, "n_next": base + len(rows)}


def _titles_of(rec: dict) -> list[tuple[str, str]]:
    out = []
    for s in rec.get("inputs") or []:
        if isinstance(s, dict) and s.get("kind") == "news_titles":
            for it in s.get("items") or []:
                if isinstance(it, dict) and it.get("title"):
                    out.append((str(it["title"]), f"#{it.get('srno', '')}"))
    return out


def _hide_titles(rec: dict) -> dict:
    """뉴스 제목 원문을 가린 판(기록에 원문이 있을 때 ?mask=1 이면). 원문이 없는 기록은 이미 '#일련번호 · 해시' 꼴이다."""
    from judge import record
    titles = _titles_of(rec)
    out = dict(rec)
    if titles:
        for k in ("user", "raw"):
            if isinstance(out.get(k), str):
                out[k] = record.mask_titles(out[k], titles)
        if isinstance(out.get("answer"), dict) and isinstance(out["answer"].get("reason"), str):
            out["answer"] = dict(out["answer"], reason=record.mask_titles(out["answer"]["reason"], titles))
        ins = []
        for s in out.get("inputs") or []:
            if isinstance(s, dict) and s.get("kind") == "news_titles":
                s = dict(s, items=[{k: v for k, v in it.items() if k != "title"} | {"title_hidden": True}
                                   for it in s.get("items") or [] if isinstance(it, dict)])
            ins.append(s)
        out["inputs"] = ins
    return out


def get_record(ctx, req):
    day, ref = str(req.get("day") or _today()), str(req.get("ref") or "")
    if not DAY_RX.match(day) or not ref:
        return 400, {"ok": False, "error": "날짜 · ref 가 필요"}
    hit = None
    for r in tail.read_all(_rec_path(ctx.root, day)):
        if r.get("event") == "judge" and r.get("ref") == ref:
            hit = r
    if hit is None:
        return 404, {"ok": False, "error": "기록에 없는 판단"}
    had_titles = bool(_titles_of(hit))
    rec = _hide_titles(hit) if req.mask else hit
    question = ""
    try:
        defs, _ = _defs(ctx.root)
        d = next((x for x in defs if x["id"] == hit.get("judge")), None)
        if d:
            question = d["question"] + ("" if d["_hash"] == hit.get("def_hash") else "\n(지금 정의 파일이 그때와 다름 — 그때 질문은 아래 프롬프트 끝)")
    except Exception:
        pass
    body = {k: rec.get(k) for k in ("ref", "at", "judge", "def_hash", "trigger", "inputs", "notes", "model", "usage",
                                    "cost_est", "cost_note", "answer", "valid", "invalid_reason", "attempts",
                                    "outputs", "sec", "calls", "dry", "fake", "targets", "prompt_sha")}
    # 'provider' 칸 이름은 가림 대상(뉴스 공급사)과 겹쳐서 prov 로 바꿔 보낸다
    body["raw_answer"] = rec.get("raw")                     # 칸 이름 raw 는 화면 응답에서 늘 빠진다(mask.DROP_KEYS)
    body["prov"] = rec.get("provider")
    if isinstance(body.get("model"), dict):
        body["model"] = {("prov" if k == "provider" else k): v for k, v in body["model"].items()}
    body["question"] = question
    if req.mask:
        body["prompt"] = "(가림 모드 — 프롬프트 본문은 보이지 않음. 입력 요약만)"
    else:
        body["prompt"] = (rec.get("system") or "") + "\n\n" + (rec.get("user") or "")
    body["titles_note"] = ("기록에 뉴스 제목 원문이 있음 — 가림 모드에서 가림" if had_titles else
                           "기록에 뉴스 제목 원문이 없음(일련번호 · 시각 · 해시만)")
    return {"ok": True, "mark": MARK, "record": body}


# ---------- 한 번 묻기 ----------
def post_ask(ctx, req):
    b = req.body
    jid, mode = str(b.get("judge") or ""), str(b.get("mode") or "")
    if mode not in ("fake", "real"):
        return 400, {"ok": False, "message": "mode 는 fake(가짜 응답) · real(실제 호출)"}
    if b.get("confirm") is not True:
        return 400, {"ok": False, "message": "확인 창을 거쳐야 합니다"}
    defs, _ = _defs(ctx.root)
    d = next((x for x in defs if x["id"] == jid), None)
    if d is None:
        return 400, {"ok": False, "message": "판단 정의에 없는 id"}
    codes = [str(c).strip().upper() for c in (b.get("codes") or []) if str(c).strip()][:20]
    if any(not CODE_RX.match(c) for c in codes):
        return 400, {"ok": False, "message": "종목은 6자리(숫자 · 영문 대문자)"}
    text = str(b.get("text") or "")
    if len(text) > 4000:
        return 400, {"ok": False, "message": "글은 4,000자 안"}
    running, _pid = _lock_pid(ctx.root)
    g, _ = _global(ctx.root)
    if mode == "real":
        if d["model"]["provider"] != "anthropic":
            return 400, {"ok": False, "message": f"이 정의의 공급자는 {d['model']['provider']} — 실제 호출이 아닙니다(정의 model.provider)"}
        if not _key_present("anthropic.yaml"):
            return 400, {"ok": False, "key_missing": True, "message": "키 없음: anthropic.yaml 을 설정 폴더에"}
        if not g["ai_enabled"] or not g["ack"]:
            return 400, {"ok": False, "message": "judge.yaml 의 ai_enabled · ack 가 true 여야 실제로 부릅니다(고지 확인)"}
        if running:
            if d["_example"]:
                return 409, {"ok": False, "message": "견본 정의는 돌고 있는 판단 프로세스가 읽지 않습니다 — judgments/ 에 복사하세요"}
            f = _write_ask(ctx.root, jid, codes, text)
            return {"ok": True, "status": "queued", "message": f"판단 프로세스에 넘김({f}) — 다음 바퀴에 처리"}
    elif running:
        return 409, {"ok": False, "message": "판단 프로세스가 돌고 있어(잠금) 가짜 한 번 묻기를 띄우지 않습니다 — 멈춘 뒤 다시"}
    script = CODE_ROOT / "scripts" / "judge_run.py"             # 이 프로그램의 스크립트로 --root 뿌리를 읽고 쓴다
    if not script.is_file():
        return 400, {"ok": False, "message": "스크립트 없음: scripts/judge_run.py"}
    args = [ctx.python, "-X", "utf8", str(script), "--root", str(ctx.root), "--once", jid]
    if d["_example"]:
        args.append("--examples")
    if mode == "fake":
        args += ["--fake", "--dry"]
    if codes:
        args += ["--codes", ",".join(codes)]
    if text:
        args += ["--text", text]
    label = f"한 번 묻기 {jid} — " + ("가짜 응답(기록만)" if mode == "fake" else "실제 호출")
    r = ctx.jobs.start("judge_once", args, cwd=ctx.root, label=label)
    return (200 if r["ok"] else 409 if r["status"] == "busy" else 500), r


def _write_ask(root: Path, jid: str, codes: list[str], text: str) -> str:
    d = root / "state" / "judge_ask"
    d.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time() * 1000)}-desk.json"
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps({"judge": jid, "text": text, "codes": codes, "actor": "desk"}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, d / name)
    return name


def get_jobs(ctx, req):
    return {"ok": True, "job": ctx.jobs.view("judge_once")}


ROUTES = {
    "GET overview": get_overview,
    "GET records": get_records,
    "GET record": get_record,
    "POST ask": post_ask,
    "GET jobs": get_jobs,
}
ROUTES.update(mine.routes(MINE))
