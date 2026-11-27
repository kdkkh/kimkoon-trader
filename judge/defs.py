"""판단 정의(judgments/*.yaml) 읽기 · 검사 · 정의 해시.

한 파일 = 판단 하나. 새 판단은 코드를 고치지 않고 파일만 더해 만든다. 검사는 엄격하다 — 모르는 칸은 거부한다
(오타 난 칸이 조용히 무시되면 '켰다고 생각한 안전장치'가 꺼진 채 돈다).

칸(맨 위): id · enabled · mode · target · note · per_code · when · inputs · question · answer · model · use · limits · fake
  id        영문 · 숫자 · _ · - (40자 안). 쓰임의 출처 이름(ai:<id>) · 거르기 파일 이름이 된다.
  mode      paper_only 만 받는다(모의 전용 — 실전 경로 없음).
  target    paper · sim · both · dry · sim_<태그> · 그 목록. 없으면 '지금 문을 켜고 돌고 있는 실행기 모두'.
  per_code  참이면 실행기 종목마다 따로 묻는다(방아쇠 종목 = 그 종목). 공시처럼 종목별 판단에.
  when      언제 묻나(목록): {at: "HH:MM"} · {every_min: N, from: "HH:MM", until: "HH:MM"} ·
            {on: new_news, codes: runner} · {on: signal, kinds: [long_entry]} · {on: review, on_timeout: skip|pass, alive_s: N}
            모두 min_gap_s(같은 방아쇠 최소 간격, 초)를 둘 수 있다.
  inputs    섞을 수 있다: bars · signals · news_titles · disclosures · browser · positions · text(아래 INPUTS)
  answer    {type: choice, options: [...]} 또는 {type: score, min: 0, max: 100}
  model     {provider: fake|anthropic, name, effort, max_tokens, retries, timeout_s, fallback: none|default}
  use       쓰임 목록: signal · gate · settings · notify · review(아래 USES). if 조건: answer · answer_in · score_gte · score_lte
  limits    {daily_calls: N, settings_per_day: N}
  fake      {answers: [글 또는 {text, stop_reason}]} 또는 {file: 정의 폴더 기준 json 파일(글 목록)}
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

ID_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
HHMM_RX = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SIM_TAG_RX = re.compile(r"^sim_[a-z0-9]{1,20}$")
TARGET_WORDS = ("paper", "sim", "both", "dry")
MAX_ORDER_WON = 3_000_000                   # kis/order.py 의 절대 상한과 같은 값(정의 검사용 — 실제 상한은 실행기 · control 이 다시 본다)

TOP_KEYS = {"id", "enabled", "mode", "target", "note", "per_code", "when", "inputs", "question", "answer", "model",
            "use", "limits", "fake"}
WHEN_KEYS = {"at": {"at", "min_gap_s"},
             "every_min": {"every_min", "from", "until", "min_gap_s"},
             "new_news": {"on", "codes", "min_gap_s"},
             "signal": {"on", "codes", "kinds", "min_gap_s"},
             "review": {"on", "on_timeout", "alive_s", "min_gap_s"}}
# 입력 종류 → 받는 칸. optional = 스위치가 꺼졌거나 자료가 없으면 이 입력만 빼고 묻는다(기본은 묻지 않음).
INPUTS = {"bars": {"kind", "last", "form", "codes", "optional"},
          "signals": {"kind", "last", "codes", "optional"},
          "news_titles": {"kind", "last", "codes", "optional", "skip_if_empty"},
          "disclosures": {"kind", "codes", "only_new", "optional", "skip_if_empty"},
          "browser": {"kind", "site", "last", "optional", "skip_if_empty"},
          "positions": {"kind", "optional"},
          "text": {"kind", "max_chars", "optional"}}
USES = {"signal": {"kind", "if", "action", "codes", "amount", "exit_by", "max_hold_min", "stop_pct", "ttl_s"},
        "gate": {"kind", "if", "ttl_min", "codes"},
        "settings": {"kind", "if", "change"},
        "notify": {"kind", "if"},
        "review": {"kind", "if"}}
IF_KEYS = {"answer", "answer_in", "score_gte", "score_lte"}
MODEL_KEYS = {"provider", "name", "effort", "max_tokens", "retries", "timeout_s", "fallback"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SETTINGS_KEYS = {"amount", "max_orders", "amount_pct", "max_orders_pct"}
LIMIT_KEYS = {"daily_calls", "settings_per_day"}
FAKE_KEYS = {"answers", "file"}
EVENT_ON = ("new_news", "signal", "review")


class DefError(ValueError):
    """정의 검사 실패 — 문구는 파일 이름 · 칸 이름만(사용자 폴더 경로를 넣지 않는다)."""


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _unknown(d: dict, allowed: set, where: str) -> None:
    bad = sorted(str(k) for k in d if k not in allowed)
    if bad:
        raise DefError(f"{where}: 모르는 칸 {', '.join(bad)}")


def _hhmm(v, where: str) -> str:
    if not isinstance(v, str) or not HHMM_RX.match(v):
        raise DefError(f"{where}: 시각은 \"HH:MM\" 글(따옴표로 감싼다)")
    return v


def _check_when(items, where="when") -> list[dict]:
    if not isinstance(items, list) or not items:
        raise DefError(f"{where}: 목록이 비었거나 목록이 아님")
    out = []
    for i, w in enumerate(items):
        at = f"{where}[{i}]"
        if not isinstance(w, dict):
            raise DefError(f"{at}: 칸 묶음이 아님")
        if True in w and "on" not in w:                     # yaml 1.1 은 칸 이름 on 을 참(True)으로 읽는다 — 되돌린다
            w = {("on" if k is True else k): v for k, v in w.items()}
        if "at" in w:
            kind = "at"
        elif "every_min" in w:
            kind = "every_min"
        elif w.get("on") in EVENT_ON:
            kind = w["on"]
        else:
            raise DefError(f"{at}: at · every_min · on(new_news · signal · review) 중 하나가 있어야 함")
        _unknown(w, WHEN_KEYS[kind], at)
        w = dict(w)
        if kind == "at":
            _hhmm(w["at"], at)
        elif kind == "every_min":
            if not _int(w["every_min"]) or not 1 <= w["every_min"] <= 1440:
                raise DefError(f"{at}: every_min 은 1~1440 정수(분)")
            w.setdefault("from", "09:00")
            w.setdefault("until", "15:30")
            _hhmm(w["from"], at)
            _hhmm(w["until"], at)
            if w["from"] >= w["until"]:
                raise DefError(f"{at}: from 은 until 보다 앞이어야 함")
        elif kind == "new_news":
            w.setdefault("codes", "runner")
            if w["codes"] != "runner":
                raise DefError(f"{at}: new_news 의 codes 는 runner(실행기 종목)만")
        elif kind == "signal":
            w.setdefault("codes", "runner")
            if w["codes"] != "runner":
                raise DefError(f"{at}: signal 의 codes 는 runner 만")
            kinds = w.setdefault("kinds", ["long_entry"])
            if not isinstance(kinds, list) or not kinds or any(k not in ("long_entry", "long_exit") for k in kinds):
                raise DefError(f"{at}: kinds 는 long_entry · long_exit 목록")
        elif kind == "review":
            w.setdefault("on_timeout", "skip")
            if w["on_timeout"] not in ("skip", "pass"):
                raise DefError(f"{at}: on_timeout 은 skip(답이 없으면 진입 안 함) · pass")
            w.setdefault("alive_s", 90)
            if not _num(w["alive_s"]) or not 20 <= w["alive_s"] <= 600:
                raise DefError(f"{at}: alive_s 는 20~600 초")
        gap = w.setdefault("min_gap_s", 60 if kind in EVENT_ON else 0)
        if not _num(gap) or not 0 <= gap <= 86400:
            raise DefError(f"{at}: min_gap_s 는 0~86400 초")
        w["_kind"] = kind
        out.append(w)
    if sum(1 for w in out if w["_kind"] == "review") > 1:
        raise DefError(f"{where}: on: review 는 하나만")
    return out


def _check_inputs(items, where="inputs") -> list[dict]:
    if not isinstance(items, list) or not items:
        raise DefError(f"{where}: 목록이 비었거나 목록이 아님")
    out = []
    for i, x in enumerate(items):
        at = f"{where}[{i}]"
        if not isinstance(x, dict) or x.get("kind") not in INPUTS:
            raise DefError(f"{at}: kind 는 {' · '.join(INPUTS)} 중 하나")
        _unknown(x, INPUTS[x["kind"]], at)
        x = dict(x)
        k = x["kind"]
        if "last" in INPUTS[k]:
            x.setdefault("last", {"bars": 30, "signals": 20, "news_titles": 20, "browser": 20}[k])
            if not _int(x["last"]) or not 1 <= x["last"] <= 240:
                raise DefError(f"{at}: last 는 1~240 정수")
        if k == "bars":
            x.setdefault("form", "derived")
            if x["form"] not in ("derived", "raw"):
                raise DefError(f"{at}: form 은 derived(가공값, 기본) · raw(원값 — judge.yaml send.bars_raw 를 켜야 함)")
            if x["last"] < 2:
                raise DefError(f"{at}: bars 의 last 는 2 이상")
        if "codes" in INPUTS[k]:
            default = {"bars": "runner", "signals": "runner", "news_titles": "all", "disclosures": "runner"}[k]
            x.setdefault("codes", default)
            allowed = ("runner", "trigger", "all") if k == "news_titles" else ("runner", "trigger")
            if x["codes"] not in allowed:
                raise DefError(f"{at}: codes 는 {' · '.join(allowed)} (종목 코드를 정의에 적지 않는다 — 실행기 종목을 따름)")
        if k == "browser":
            if not isinstance(x.get("site"), str) or not ID_RX.match(x["site"]):
                raise DefError(f"{at}: site 는 영문 · 숫자 · _ · - 이름")
        if k == "text":
            x.setdefault("max_chars", 1000)
            if not _int(x["max_chars"]) or not 1 <= x["max_chars"] <= 4000:
                raise DefError(f"{at}: max_chars 는 1~4000")
        for flag in ("optional", "skip_if_empty", "only_new"):
            if flag in x and not isinstance(x[flag], bool):
                raise DefError(f"{at}: {flag} 는 true · false")
        out.append(x)
    return out


def _check_answer(a, where="answer") -> dict:
    if not isinstance(a, dict) or a.get("type") not in ("choice", "score"):
        raise DefError(f"{where}: type 은 choice · score")
    if a["type"] == "choice":
        _unknown(a, {"type", "options"}, where)
        opts = a.get("options")
        if (not isinstance(opts, list) or not 2 <= len(opts) <= 10 or len(set(map(str, opts))) != len(opts)
                or any(not isinstance(o, str) or not o.strip() or len(o) > 40 for o in opts)):
            raise DefError(f"{where}: options 는 서로 다른 글 2~10개(각 40자 안)")
    else:
        _unknown(a, {"type", "min", "max"}, where)
        a = {"min": 0, "max": 100, **a}
        if not _int(a["min"]) or not _int(a["max"]) or a["min"] >= a["max"]:
            raise DefError(f"{where}: min · max 는 정수, min < max")
    return dict(a)


def _check_if(cond, answer: dict, where: str) -> dict:
    if cond is None:
        return {}
    if not isinstance(cond, dict) or not cond:
        raise DefError(f"{where}.if: 칸 묶음이어야 함")
    _unknown(cond, IF_KEYS, f"{where}.if")
    choice = answer["type"] == "choice"
    for k, v in cond.items():
        if k in ("answer", "answer_in"):
            if not choice:
                raise DefError(f"{where}.if.{k}: 점수 판단(score)에는 score_gte · score_lte 를 쓴다")
            vals = v if k == "answer_in" else [v]
            if not isinstance(vals, list) or not vals or any(x not in answer["options"] for x in vals):
                raise DefError(f"{where}.if.{k}: 답의 보기(options) 안의 값이어야 함")
        else:
            if choice:
                raise DefError(f"{where}.if.{k}: 보기 판단(choice)에는 answer · answer_in 을 쓴다")
            if not _num(v) or not answer["min"] <= v <= answer["max"]:
                raise DefError(f"{where}.if.{k}: 점수 범위({answer['min']}~{answer['max']}) 안의 숫자")
    return dict(cond)


def _check_use(items, answer: dict, when: list[dict], per_code: bool, where="use") -> list[dict]:
    if not isinstance(items, list) or not items:
        raise DefError(f"{where}: 목록이 비었거나 목록이 아님")
    has_review = any(w["_kind"] == "review" for w in when)
    trigger_codes = per_code or any(w["_kind"] in EVENT_ON for w in when)
    out = []
    for i, u in enumerate(items):
        at = f"{where}[{i}]"
        if not isinstance(u, dict) or u.get("kind") not in USES:
            raise DefError(f"{at}: kind 는 {' · '.join(USES)} 중 하나")
        _unknown(u, USES[u["kind"]], at)
        u = dict(u)
        u["if"] = _check_if(u.get("if"), answer, at)
        k = u["kind"]
        if k == "signal":
            if u.setdefault("action", "entry") not in ("entry", "exit"):
                raise DefError(f"{at}: action 은 entry · exit")
            if u.setdefault("codes", "trigger") != "trigger":
                raise DefError(f"{at}: 신호는 방아쇠 종목(codes: trigger)으로만 — 실행기 종목 전체에 신호를 내지 않는다")
            if not trigger_codes:
                raise DefError(f"{at}: 신호 쓰임은 방아쇠 종목이 있는 판단에만(on: new_news · signal · review 또는 per_code: true)")
            if not u["if"]:
                raise DefError(f"{at}: 신호 쓰임에는 if 조건이 있어야 함")
            exit_by = u.setdefault("exit_by", "signal")
            if exit_by not in ("signal", "strategy"):
                raise DefError(f"{at}: exit_by 는 signal · strategy")
            if u["action"] == "entry" and exit_by == "signal":
                mh = u.get("max_hold_min")
                if not _num(mh) or not 0 < mh <= 1440:
                    raise DefError(f"{at}: exit_by: signal 진입에는 max_hold_min(보유 시간 상한, 분)이 반드시 있어야 함")
            for key, lo, hi in (("amount", 1, MAX_ORDER_WON), ("stop_pct", 0.01, 50), ("max_hold_min", 0.01, 1440)):
                if key in u and (not _num(u[key]) or not lo <= u[key] <= hi):
                    raise DefError(f"{at}: {key} 는 {lo}~{hi}")
            u.setdefault("ttl_s", 120)
            if not _num(u["ttl_s"]) or not 10 <= u["ttl_s"] <= 3600:
                raise DefError(f"{at}: ttl_s(신호 만료, 초)는 10~3600")
        elif k == "gate":
            if not _num(u.get("ttl_min")) or not 1 <= u["ttl_min"] <= 1440:
                raise DefError(f"{at}: ttl_min(거르기 만료, 분)은 1~1440 — 판단이 멈추면 만료로 저절로 풀리게")
            codes = u.setdefault("codes", "trigger" if per_code else "all")
            if codes not in ("all", "trigger"):
                raise DefError(f"{at}: codes 는 all(모든 종목) · trigger(방아쇠 종목)")
            if codes == "trigger" and not trigger_codes:
                raise DefError(f"{at}: codes: trigger 는 방아쇠 종목이 있는 판단에만")
        elif k == "settings":
            ch = u.get("change")
            if not isinstance(ch, dict) or not ch:
                raise DefError(f"{at}: change 가 있어야 함")
            _unknown(ch, SETTINGS_KEYS, f"{at}.change")
            if ("amount" in ch and "amount_pct" in ch) or ("max_orders" in ch and "max_orders_pct" in ch):
                raise DefError(f"{at}.change: 같은 칸을 두 번 적음")
            for key, v in ch.items():
                if key.endswith("_pct"):
                    if not _num(v) or not 0 < v < 100:
                        raise DefError(f"{at}.change.{key}: 지금 값의 몇 %로 내릴지(0 초과 100 미만) — 올리는 변경은 받지 않는다")
                elif key == "amount":
                    if not _num(v) or not 0 < v <= MAX_ORDER_WON:
                        raise DefError(f"{at}.change.amount: 0 초과 {MAX_ORDER_WON:,} 이하")
                elif not _int(v) or v < 0:
                    raise DefError(f"{at}.change.max_orders: 0 이상 정수")
            if not u["if"]:
                raise DefError(f"{at}: 설정 쓰임에는 if 조건이 있어야 함")
        elif k == "review":
            if not has_review:
                raise DefError(f"{at}: review 쓰임은 on: review 방아쇠와 함께만")
            if not u["if"]:
                raise DefError(f"{at}: review 쓰임의 if = 통과 조건(아니면 거절)")
        out.append(u)
    if has_review and not any(u["kind"] == "review" for u in out):
        raise DefError(f"{where}: on: review 방아쇠에는 review 쓰임이 있어야 함")
    if sum(1 for u in out if u["kind"] == "review") > 1:
        raise DefError(f"{where}: review 쓰임은 하나만")
    return out


def _check_model(m, where="model") -> dict:
    if not isinstance(m, dict):
        raise DefError(f"{where}: 칸 묶음이 아님")
    _unknown(m, MODEL_KEYS, where)
    m = {"provider": "fake", "name": "claude-opus-5-5", "effort": "low", "max_tokens": 2000, "retries": 0,
         "timeout_s": 60, "fallback": "none", **m}
    if m["provider"] not in ("fake", "anthropic"):
        raise DefError(f"{where}.provider: fake · anthropic")
    if not isinstance(m["name"], str) or not re.match(r"^[a-z0-9][a-z0-9.-]{1,60}$", m["name"]):
        raise DefError(f"{where}.name: 모델 ID 글")
    if m["effort"] not in EFFORTS:
        raise DefError(f"{where}.effort: {' · '.join(EFFORTS)}")
    if not _int(m["max_tokens"]) or not 16 <= m["max_tokens"] <= 64000:
        raise DefError(f"{where}.max_tokens: 16~64000 정수")
    if not _int(m["retries"]) or not 0 <= m["retries"] <= 3:
        raise DefError(f"{where}.retries: 0~3 정수(다시 묻는 횟수 — 묻는 만큼 호출 · 비용이 는다)")
    if not _num(m["timeout_s"]) or not 5 <= m["timeout_s"] <= 600:
        raise DefError(f"{where}.timeout_s: 5~600 초")
    if m["fallback"] not in ("none", "default"):
        raise DefError(f"{where}.fallback: none(기본) · default(거부되면 서버가 다른 모델로 — 누가 판단했는지가 바뀜)")
    return m


def _check_fake(f, where="fake") -> dict:
    if f is None:
        return {"answers": []}
    if not isinstance(f, dict):
        raise DefError(f"{where}: 칸 묶음이 아님")
    _unknown(f, FAKE_KEYS, where)
    ans = f.get("answers", [])
    if not isinstance(ans, list):
        raise DefError(f"{where}.answers: 목록")
    for i, a in enumerate(ans):
        if isinstance(a, dict):
            _unknown(a, {"text", "stop_reason"}, f"{where}.answers[{i}]")
            if not isinstance(a.get("text", ""), str):
                raise DefError(f"{where}.answers[{i}].text: 글")
        elif not isinstance(a, str):
            raise DefError(f"{where}.answers[{i}]: 글 또는 {{text, stop_reason}}")
    if "file" in f and (not isinstance(f["file"], str) or "/" in f["file"] or "\\" in f["file"] or ".." in f["file"]):
        raise DefError(f"{where}.file: 정의 폴더 안의 파일 이름만")
    return dict(f)


def _check_target(t, where="target"):
    if t is None or t == "running":
        return "running"
    vals = t if isinstance(t, list) else [t]
    if not vals:
        raise DefError(f"{where}: 비었음")
    for v in vals:
        if not isinstance(v, str) or (v not in TARGET_WORDS and not SIM_TAG_RX.match(v)):
            raise DefError(f"{where}: paper · sim · both · dry · sim_<태그> (또는 그 목록)")
    return t


def check(d, name: str = "?") -> dict:
    """정의 하나(yaml 에서 읽은 dict)를 검사해 기본값을 채운 dict 를 돌려준다. 어긋나면 DefError."""
    if not isinstance(d, dict):
        raise DefError(f"{name}: 칸 묶음(yaml 사전)이 아님")
    _unknown(d, TOP_KEYS, name)
    for k in ("id", "mode", "when", "inputs", "question", "answer", "use"):
        if k not in d:
            raise DefError(f"{name}: {k} 칸이 없음")
    if not isinstance(d["id"], str) or not ID_RX.match(d["id"]):
        raise DefError(f"{name}: id 는 영문 · 숫자 · _ · - (40자 안)")
    if d["mode"] != "paper_only":
        raise DefError(f"{name}: mode 는 paper_only 만(모의 전용)")
    enabled = d.get("enabled", False)
    if not isinstance(enabled, bool):
        raise DefError(f"{name}: enabled 는 true · false")
    per_code = d.get("per_code", False)
    if not isinstance(per_code, bool):
        raise DefError(f"{name}: per_code 는 true · false")
    q = d["question"]
    if not isinstance(q, str) or not q.strip() or len(q) > 4000:
        raise DefError(f"{name}: question 은 비지 않은 글(4,000자 안)")
    if "note" in d and not isinstance(d["note"], str):
        raise DefError(f"{name}: note 는 글")
    out = {"id": d["id"], "enabled": enabled, "mode": "paper_only", "target": _check_target(d.get("target")),
           "note": d.get("note", ""), "per_code": per_code}
    out["when"] = _check_when(d["when"])
    out["inputs"] = _check_inputs(d["inputs"])
    out["question"] = q.strip()
    out["answer"] = _check_answer(d["answer"])
    out["model"] = _check_model(d.get("model", {}))
    out["use"] = _check_use(d["use"], out["answer"], out["when"], per_code)
    lim = d.get("limits", {}) or {}
    if not isinstance(lim, dict):
        raise DefError(f"{name}: limits 는 칸 묶음")
    _unknown(lim, LIMIT_KEYS, "limits")
    lim = {"daily_calls": 50, "settings_per_day": 3, **lim}
    if not _int(lim["daily_calls"]) or not 1 <= lim["daily_calls"] <= 10000:
        raise DefError("limits.daily_calls: 1~10000 정수")
    if not _int(lim["settings_per_day"]) or not 0 <= lim["settings_per_day"] <= 20:
        raise DefError("limits.settings_per_day: 0~20 정수")
    out["limits"] = lim
    out["fake"] = _check_fake(d.get("fake"))
    if any(w["_kind"] == "review" for w in out["when"]) and per_code:
        raise DefError(f"{name}: on: review 판단은 per_code 를 쓰지 않는다(검토 요청마다 그 종목으로 묻는다)")
    return out


def def_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:12]


def load_file(p: Path) -> dict:
    """파일 하나 → 검사한 정의(+ _file · _hash · _dir). 어긋나면 DefError."""
    p = Path(p)
    raw = p.read_bytes()
    try:
        d = yaml.safe_load(raw.decode("utf-8-sig"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise DefError(f"{p.name}: yaml 을 읽지 못함({type(e).__name__})") from None
    out = check(d, p.name)
    out["_file"], out["_hash"], out["_dir"] = p.name, def_hash(raw), str(p.parent)
    return out


def files_in(folder: Path, examples: bool = False) -> list[Path]:
    """judgments 폴더의 정의 파일. 기본은 *.yaml 중 *.example.yaml 을 뺀 것(견본은 examples=True 일 때만)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.yaml")):
        if p.name.endswith(".example.yaml") and not examples:
            continue
        out.append(p)
    return out


def load_all(folder: Path, examples: bool = False) -> tuple[list[dict], list[tuple[str, str]]]:
    """(검사를 통과한 정의 목록, [(파일 이름, 거부 이유)]). id 가 겹치면 뒤 파일을 거부한다."""
    defs, errors, seen = [], [], {}
    for p in files_in(folder, examples):
        try:
            d = load_file(p)
        except DefError as e:
            errors.append((p.name, str(e)))
            continue
        except OSError as e:
            errors.append((p.name, f"읽지 못함({type(e).__name__})"))
            continue
        if d["id"] in seen:
            errors.append((p.name, f"id {d['id']} 가 {seen[d['id']]} 와 겹침"))
            continue
        seen[d["id"]] = p.name
        defs.append(d)
    if sum(1 for d in defs if d["enabled"] and any(w["_kind"] == "review" for w in d["when"])) > 1:
        errors.append(("*", "켜진 검토(on: review) 판단이 둘 이상 — 실행기 문의 검토 표시는 하나라 하나만 켠다"))
        keep, drop = False, []
        for d in defs:
            if d["enabled"] and any(w["_kind"] == "review" for w in d["when"]):
                if keep:
                    drop.append(d)
                keep = True
        defs = [d for d in defs if d not in drop]
    return defs, errors


def switches_needed(d: dict) -> list[tuple[str, str, bool]]:
    """정의가 쓰는 입력 → [(입력 종류, judge.yaml send 스위치 이름, optional)]."""
    out = []
    for x in d["inputs"]:
        k = x["kind"]
        sw = {"bars": "bars_raw" if x.get("form") == "raw" else None, "signals": "signals",
              "news_titles": "news_titles", "disclosures": "disclosures", "browser": "browser",
              "positions": "positions", "text": "text"}[k]
        if sw:
            out.append((k, sw, bool(x.get("optional"))))
    return out
