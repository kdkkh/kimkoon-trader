"""설정 파일(config.yaml) 편집 — 해당 줄만 바꾼다. 화면 · 봇 · AI 판단이 같은 함수를 쓴다.

순서(edit)
  1. 호출자(actor) 확인: desk · telegram · judge:<id>. judge 는 amount · max_orders 둘만, 내리는 쪽만, 하루 횟수 상한.
  2. 바꿀 수 있는 칸인지(strategy.live.LiveRunner.RELOADABLE) — 봉 길이 · 시작 · 끝 시각 · 금액 상한은 다시 시작해야 하므로 여기서 받지 않는다.
  3. 잠금(state/config.lock, O_EXCL) — 연달아 쓰기는 잠금 안에서 하나씩. 앞 변경이 실행기에 적용된 것을 본 뒤에 다음 것을 쓴다
     (봉 사이에 두 번 쓰면 가운데 값이 기록 없이 사라지는 것을 막는다). 죽은 프로세스 · 오래된 잠금은 넘겨받는다.
  4. 줄 바꾸기 → 임시 파일 → 미리 검사: load_settings 로 읽어 봄 · 금액은 상한 이하 · 주문 방식은 check_kinds ·
     전략은 등록된 이름만(경로 꼴 .py · / · \\ 거부 — 경로를 주면 그 파이썬 파일이 실행된다) ·
     보유 중이거나 진행 중 주문이 있는 종목을 codes 에서 빼는 변경 거부(빼면 그 포지션은 팔 길이 없어진다).
  5. 이전 판을 state/config_history/<시각>.yaml 로 → os.replace(상대가 열고 있으면 짧게 재시도).
  6. 돌고 있는 실행기(모의 · 시뮬레이션 · dry-run — 모두 같은 config.yaml 을 읽는다)의 기록에서 settings_changed / settings_rejected 를
     기다린다. 실행기가 안 돌면 '다음 시작 때 반영'. 거부면 이전 판으로 자동 복귀(거부된 값이 남으면 뒤 변경이 모두 거부되므로).
모든 시도(미리 검사에서 막힌 것 포함)는 logs/control_YYYYMMDD.jsonl 에 한 줄씩 남는다.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from control import state
from control.common import ROOT, journal, journal_today, log_path, now_ms, read_events, read_json, replace_retry, scrub

JUDGE_KEYS = ("amount", "max_orders")          # 판단이 바꿀 수 있는 칸(내리는 쪽만)
JUDGE_EDITS_PER_DAY = 5                        # 판단 하나의 하루 설정 변경 상한(적용 · 저장된 것만 셈)
LOCK_WAIT_S = 120.0                            # 앞 변경이 적용 확인을 기다리는 동안(봉 하나 + 여유) 뒤 변경이 기다리는 최대 시간
LOCK_STALE_S = 300.0                           # 이보다 오래된 잠금은 넘겨받는다
WRITTEN = ("applied", "saved", "unconfirmed")  # 파일을 바꾼 결과
SHOW_KEYS = ("codes", "strategy", "params", "order_mode", "amount", "max_amount", "wait", "max_orders", "loss_limit",
             "apply_to_open", "tf", "start", "until", "poll", "settle")
_ACTOR_RX = re.compile(r"^(desk|telegram|judge:[A-Za-z0-9_.-]{1,40})$")
_LINE = re.compile(r'^(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<sep>[ \t]*:[ \t]*)(?P<val>[^#\r\n]*?)(?P<cmt>[ \t]+#.*)?$')
_CODE_RX = re.compile(r"^\d{6}$")
_SL = None


# ---------- 설정 읽기(실행기와 같은 함수) ----------
def _settings_module():
    """scripts/strategy_live.py 의 load_settings(시뮬레이션 실행기도 같은 방식으로 불러 쓴다)."""
    global _SL
    if _SL is None:
        spec = importlib.util.spec_from_file_location("control_strategy_live_settings", ROOT / "scripts" / "strategy_live.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SL = mod
    return _SL


def load_settings(path: Path) -> dict:
    """실행기와 같은 규칙으로 읽는다. 잘못된 파일이면 ValueError(실행기 쪽 SystemExit 도 여기서 바꾼다)."""
    try:
        return _settings_module().load_settings(Path(path))[0]
    except BaseException as e:                     # SystemExit · yaml 오류 등
        if isinstance(e, KeyboardInterrupt):
            raise
        raise ValueError(scrub(str(e))[:200] or type(e).__name__) from None


def reloadable() -> tuple:
    from strategy.live import LiveRunner
    return tuple(LiveRunner.RELOADABLE)


def view(root: Path = ROOT, config_path: Path | None = None) -> dict:
    """지금 설정(보여 줄 칸만). 반환 {ok, settings, file, error}."""
    cfg = Path(config_path) if config_path else Path(root) / "config.yaml"
    if not cfg.exists():
        return {"ok": False, "settings": {}, "file": cfg.name, "error": "설정 파일 없음"}
    try:
        s = load_settings(cfg)
    except ValueError as e:
        return {"ok": False, "settings": {}, "file": cfg.name, "error": str(e)}
    return {"ok": True, "settings": {k: s.get(k) for k in SHOW_KEYS}, "file": cfg.name, "error": ""}


# ---------- 값 다루기 ----------
def parse_value(key: str, text: str):
    """사람이 친 글 → 설정 값. codes 는 쉼표로 여러 개, 숫자 칸은 천 단위 쉼표를 허용."""
    import yaml
    t = str(text).strip()
    if key == "codes":
        return [c.zfill(6) if c.isdigit() else c for c in re.split(r"[,\s]+", t) if c]
    if key in ("strategy", "order_mode"):
        return t
    if key in ("amount", "max_orders", "loss_limit", "wait", "poll", "settle"):
        t = t.replace(",", "")
    try:
        return yaml.safe_load(t)
    except Exception:
        raise ValueError(f"{key} 값을 읽지 못함") from None


def _norm(key: str, v):
    if key == "codes":
        if isinstance(v, str):
            v = [c for c in re.split(r"[,\s]+", v) if c]
        if isinstance(v, (list, tuple)):
            return [str(c).strip().zfill(6) if isinstance(c, str) and c.strip().isdigit() else c for c in v]
    if isinstance(v, float) and not isinstance(v, bool) and v.is_integer() and key in ("amount", "loss_limit"):
        return int(v)
    return v


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return float(a) == float(b)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return [str(x) for x in a] == [str(x) for x in b]
    return a == b


def _dump(v) -> str:
    import yaml
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(f'"{x}"' if isinstance(x, str) else _dump(x) for x in v) + "]"
    s = yaml.safe_dump(v, default_flow_style=True, allow_unicode=True, width=10_000).strip()
    if s.endswith("..."):
        s = s[:-3].strip()
    return s


def set_keys(text: str, changes: dict) -> str:
    """해당 칸의 줄만 바꾼다(주석 · 주석 열 위치 유지). 파일에 없는 칸은 끝에 덧붙인다.
    여러 줄(블록) 표기 · 같은 칸 두 번은 거부. 바꾼 뒤 다시 읽어 '바꾼 칸만 바뀌었는지' 대조한다."""
    import yaml
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    done = set()
    for i, ln in enumerate(lines):
        m = _LINE.match(ln)
        if not m or m["key"] not in changes:
            continue
        k = m["key"]
        if k in done:
            raise ValueError(f"설정 파일에 같은 칸이 두 번 있음: {k}")
        if m["val"].strip() == "" and i + 1 < len(lines) and lines[i + 1][:1] in (" ", "\t"):
            raise ValueError(f"{k} 가 여러 줄(블록) 표기라 한 줄 바꾸기를 못 함 — 파일에서 한 줄로 고친 뒤 다시")
        cmt = (m["cmt"] or "").lstrip()
        head = f"{k}{m['sep']}{_dump(changes[k])}"
        if cmt:                                   # 주석 '#' 의 열 위치를 될 수 있으면 그대로
            col = len(ln) - len(cmt)
            head = head.ljust(col) if len(head) < col else head + " "
        lines[i] = head + cmt
        done.add(k)
    for k in changes:
        if k not in done:
            lines.append(f"{k}: {_dump(changes[k])}")
    out = nl.join(lines) + (nl if text.endswith(("\n", "\r")) or not text else "")
    before = yaml.safe_load(text) or {}
    after = yaml.safe_load(out) or {}
    want = {**before, **changes}
    if after != want:
        bad = sorted(k for k in set(want) | set(after) if want.get(k) != after.get(k))
        raise ValueError(f"줄 바꾸기 뒤 다시 읽은 값이 기대와 다름: {', '.join(bad)}")
    return out


# ---------- 미리 검사 ----------
def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def registered_strategies() -> set[str]:
    from strategy.ported import _class_of, list_strategies
    import importlib as _il
    names = set()
    for stem in list_strategies():
        names.add(stem)
        try:
            names.add(_class_of(_il.import_module(f"strategy.ported.{stem}")).name)
        except Exception:
            pass
    return {n for n in names if n}


def amount_cap(new: dict, root: Path, running: list[str]) -> float:
    from kis.order import MAX_ORDER_WON
    cap = float(MAX_ORDER_WON)
    if _is_num(new.get("max_amount")):
        cap = min(cap, float(new["max_amount"]))
    for t in running:                                         # 돌고 있는 실행기는 시작할 때 읽은 상한을 쓴다
        st = state.last_event(root, t, "start")
        if st and _is_num(st.get("max_amount")):
            cap = min(cap, float(st["max_amount"]))
    return cap


def precheck(new: dict, diff: dict, root: Path, running: list[str], pid_alive=None) -> str | None:
    """None = 통과. new = load_settings 로 읽은 바뀐 뒤 설정 전체, diff = {칸: [전, 후]}."""
    a = new.get("amount")
    if not _is_num(a) or a <= 0:
        return "금액(amount)은 0보다 큰 숫자"
    cap = amount_cap(new, root, running)
    if a > cap:
        return f"1회 금액 {a:,.0f}원이 상한 {cap:,.0f}원을 넘음(깎지 않고 거부)"
    mo = new.get("max_orders")
    if not isinstance(mo, int) or isinstance(mo, bool) or mo < 0:
        return "하루 진입 상한(max_orders)은 0 이상 정수"
    for k, low in (("loss_limit", 0), ("wait", 0), ("poll", 0)):
        if not _is_num(new.get(k)) or new[k] <= low:
            return f"{k} 는 0보다 큰 숫자"
    if not _is_num(new.get("settle")) or new["settle"] < 0:
        return "settle 은 0 이상 숫자"
    if not isinstance(new.get("apply_to_open"), bool):
        return "apply_to_open 은 true · false"
    try:
        from execution.modes import OrderSettings, check_kinds
        st = OrderSettings(str(new.get("order_mode")), float(a), float(new.get("wait")))
        brokers = set()
        for t in running:
            hb = state.heartbeat(root, t)
            if hb.get("broker"):
                brokers.add(hb["broker"])
        for b in sorted(brokers or {"kis-paper", "sim"}):
            check_kinds(st, b)
    except Exception as e:
        return f"주문 방식: {scrub(str(e))[:120]}"
    if "strategy" in diff or "params" in diff:
        name = new.get("strategy")
        if not isinstance(name, str) or not name or name.endswith(".py") or "/" in name or "\\" in name:
            return "전략은 등록된 이름만(파일 경로는 받지 않음)"
        if name not in registered_strategies():
            return f"등록되지 않은 전략: {name[:40]}"
        if not isinstance(new.get("params") or {}, dict):
            return "params 는 {이름: 값} 꼴"
        try:
            from strategy.ported import load_strategy
            load_strategy(name, **(new.get("params") or {}))
        except Exception as e:
            return f"전략 값이 맞지 않음: {scrub(str(e))[:120]}"
    if "codes" in diff:
        codes = new.get("codes")
        if not isinstance(codes, list) or not codes or any(not isinstance(c, str) or not _CODE_RX.match(c) for c in codes):
            return "종목은 6자리 숫자 문자열 목록"
        if len(set(codes)) != len(codes):
            return "같은 종목이 두 번 있음"
        held = state.held_codes(root, pid_alive=pid_alive)
        gone = sorted(c for c in held if c not in codes)
        if gone:
            where = ", ".join(f"{c}({'·'.join(held[c])})" for c in gone)
            return f"보유 중 · 진행 중 주문이 있는 종목은 뺄 수 없음(빼면 팔 길이 없어짐): {where}"
    return None


# ---------- 잠금 ----------
def _lock_path(root: Path) -> Path:
    return Path(root) / "state" / "config.lock"


def acquire_lock(root: Path, wait_s: float = LOCK_WAIT_S, stale_s: float = LOCK_STALE_S, pid_alive=None) -> bool:
    p = _lock_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    alive_fn = pid_alive or state._pid_alive_default
    end = time.time() + max(wait_s, 0)
    while True:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "t": time.time()}).encode())
            os.close(fd)
            return True
        except FileExistsError:
            d = read_json(p)
            try:
                t = float(d.get("t")) if isinstance(d, dict) and d.get("t") is not None else p.stat().st_mtime
            except (OSError, TypeError, ValueError):
                t = time.time()
            pid = d.get("pid") if isinstance(d, dict) else None
            stale = time.time() - t > stale_s
            if isinstance(pid, int) and pid != os.getpid() and not alive_fn(pid):
                stale = True
            if stale:
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
        except OSError:
            pass
        if time.time() >= end:
            return False
        time.sleep(0.05)


def release_lock(root: Path) -> None:
    try:
        _lock_path(root).unlink()
    except OSError:
        pass


# ---------- 편집 ----------
def _judge_count(root: Path, actor: str) -> int:
    return sum(1 for r in journal_today(root)
               if r.get("action") == "settings_edit" and r.get("actor") == actor and r.get("status") in WRITTEN)


def _prepare(changes: dict, actor: str, root: Path, cfg: Path, tmp: Path, pid_alive=None):
    """(diff, 이전 바이트, 새 바이트, 돌고 있는 실행기, 오류). tmp 에 새 판을 써 둔다(오류면 지움)."""
    if not _ACTOR_RX.match(str(actor)):
        return None, None, None, [], "모르는 호출자(desk · telegram · judge:<id>)"
    if not isinstance(changes, dict) or not changes:
        return None, None, None, [], "바꿀 칸이 없음"
    bad = sorted(k for k in changes if k not in reloadable())
    if bad:
        return None, None, None, [], f"실행 중에 바꿀 수 없는 칸: {', '.join(bad)}"
    changes = {k: _norm(k, v) for k, v in changes.items()}
    is_judge = str(actor).startswith("judge:")
    if is_judge and any(k not in JUDGE_KEYS for k in changes):
        return None, None, None, [], f"판단이 바꿀 수 있는 칸은 {' · '.join(JUDGE_KEYS)} 뿐"
    if not cfg.exists():
        return None, None, None, [], "설정 파일 없음"
    try:
        raw = cfg.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        cur = load_settings(cfg)
    except (OSError, ValueError) as e:
        return None, None, None, [], f"지금 설정 파일을 읽지 못함: {scrub(str(e))[:120]}"
    diff = {k: [cur.get(k), v] for k, v in changes.items() if not _same(cur.get(k), v)}
    if not diff:
        return {}, raw, None, [], "바뀐 것 없음(지금 값과 같음)"
    if is_judge:
        for k, (old, new) in diff.items():
            if not _is_num(new) or not _is_num(old) or not new < old:
                return diff, raw, None, [], f"판단은 {k} 를 내리기만 할 수 있음({old} → {new} 거부)"
    try:
        new_text = set_keys(text, {k: v[1] for k, v in diff.items()})
    except ValueError as e:
        return diff, raw, None, [], str(e)
    new_raw = (b"\xef\xbb\xbf" if bom else b"") + new_text.encode("utf-8")
    tmp.write_bytes(new_raw)
    try:
        new = load_settings(tmp)
    except ValueError as e:
        _unlink(tmp)
        return diff, raw, None, [], f"바꾼 파일이 읽히지 않음: {e}"
    running = state.running_targets(root, pid_alive=pid_alive)
    why = precheck(new, diff, root, running, pid_alive)
    if why:
        _unlink(tmp)
        return diff, raw, None, running, why
    return diff, raw, new_raw, running, None


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def preview(changes: dict, actor: str = "desk", root: Path = ROOT, config_path: Path | None = None,
            pid_alive=None) -> dict:
    """쓰지 않고 미리 검사만. 반환 {ok, message, diff}. 바꾼 판은 시스템 임시 폴더에 잠깐 썼다 지운다
    (프로젝트 폴더에는 아무것도 쓰지 않는다)."""
    import tempfile
    root = Path(root)
    cfg = Path(config_path) if config_path else root / "config.yaml"
    fd, name = tempfile.mkstemp(prefix="kimkoon-config-preview-", suffix=".yaml")
    os.close(fd)
    tmp = Path(name)
    try:
        diff, _, new_raw, _, err = _prepare(changes, actor, root, cfg, tmp, pid_alive)
    finally:
        _unlink(tmp)
    if err:
        return {"ok": False, "message": err, "diff": diff or {}}
    return {"ok": True, "message": "미리 검사 통과", "diff": diff}


def _outcome(root: Path, target: str, since: int, diff: dict):
    for r in read_events(log_path(root, target), {"settings_changed", "settings_rejected"}):
        try:
            if int(r.get("ts_ms") or 0) < since:
                continue
        except (TypeError, ValueError):
            continue
        if r.get("event") == "settings_rejected":
            return "rejected", scrub(str(r.get("error", "")))[:160]
        ch = r.get("changes") if isinstance(r.get("changes"), dict) else {}
        if all(k in ch and isinstance(ch[k], list) and len(ch[k]) == 2 and _same(ch[k][1], diff[k][1]) for k in diff):
            return "applied", None
    return None


def _wait_default(root: Path, running: list[str]) -> float:
    w = 0.0
    for t in running:
        st = state.last_event(root, t, "start") or {}
        try:
            w = max(w, float(st.get("tf") or 1) * 60 + float(st.get("settle") or 3) + 15)
        except (TypeError, ValueError):
            w = max(w, 80.0)
    return w or 80.0


def edit(changes: dict, actor: str = "desk", root: Path = ROOT, config_path: Path | None = None,
         wait_s: float | None = None, lock_wait_s: float = LOCK_WAIT_S, after_write=None, fake: bool = False,
         judge_daily_max: int = JUDGE_EDITS_PER_DAY, pid_alive=None, poll_s: float = 0.2) -> dict:
    """설정 바꾸기. 반환 {ok, status, message, diff, id, history, targets}.
    status: applied(실행기 적용 확인) · saved(실행기 안 돎 — 다음 시작 때 반영) · unconfirmed(기다림 안에 확인 못 함 — 파일은 바뀜)
            rejected(실행기가 거부 → 이전 판으로 되돌림) · refused(미리 검사에서 막음 — 파일 그대로) · unchanged · busy(잠금)
    after_write = 바꿔 넣은 직후 부를 함수(시험용)."""
    root = Path(root)
    cfg = Path(config_path) if config_path else root / "config.yaml"
    rid = f"se-{now_ms()}-{secrets.token_hex(2)}"
    asked = {k: v for k, v in (changes or {}).items()} if isinstance(changes, dict) else {}

    def done(status, message, diff=None, history=None, targets=None, **extra):
        journal(root, "settings_edit", id=rid, actor=str(actor)[:50], asked=asked, diff=diff or {}, status=status,
                message=message, history=history, targets=targets or [], **extra)
        return {"ok": status in ("applied", "saved", "unconfirmed"), "status": status, "message": scrub(message),
                "diff": diff or {}, "id": rid, "history": history, "targets": targets or []}

    if fake:
        return done("refused", "가짜 공급자 — 설정 파일(모의 · 시뮬레이션 공용)에 쓰지 않고 기록만 함")
    if str(actor).startswith("judge:") and _judge_count(root, str(actor)) >= judge_daily_max:
        return done("refused", f"이 판단의 오늘 설정 변경 상한({judge_daily_max}번)에 닿음")
    if not acquire_lock(root, lock_wait_s, pid_alive=pid_alive):
        return done("busy", "다른 설정 변경이 진행 중(잠금) — 잠시 뒤 다시")
    tmp = cfg.with_name(cfg.name + f".edit.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        diff, old_raw, new_raw, running, err = _prepare(changes, actor, root, cfg, tmp, pid_alive)
        if err:
            return done("unchanged" if diff == {} else "refused", err, diff)
        hist_dir = root / "state" / "config_history"
        hist_dir.mkdir(parents=True, exist_ok=True)
        hist = hist_dir / f"{datetime.now():%Y%m%d_%H%M%S_%f}.yaml"
        hist.write_bytes(old_raw)
        since = now_ms()
        try:
            replace_retry(tmp, cfg)
        except OSError as e:
            return done("refused", f"설정 파일을 바꿔 넣지 못함({type(e).__name__}) — 파일 그대로", diff, hist.name)
        if after_write:
            after_write(cfg)
        if not running:
            return done("saved", "실행기가 돌고 있지 않음 — 다음 시작 때 반영", diff, hist.name)
        end = time.time() + (wait_s if wait_s is not None else _wait_default(root, running))
        results: dict = {}
        while True:
            for t in running:
                if t not in results:
                    o = _outcome(root, t, since, diff)
                    if o:
                        results[t] = o
            if any(o[0] == "rejected" for o in results.values()) or len(results) == len(running) or time.time() >= end:
                break
            time.sleep(poll_s)
        rej = [(t, o[1]) for t, o in results.items() if o[0] == "rejected"]
        if rej:
            back = cfg.with_name(cfg.name + f".revert.{os.getpid()}.{threading.get_ident()}.tmp")
            back.write_bytes(old_raw)
            try:
                replace_retry(back, cfg)
                how = "이전 판으로 되돌림"
            except OSError:
                _unlink(back)
                how = "되돌리기 실패 — 설정 파일을 직접 확인하세요"
            why = "; ".join(f"{state.runner_paths(root, t)['label']}: {e}" for t, e in rej)
            return done("rejected", f"실행기가 거부함({why}) — {how}", diff, hist.name, running)
        if len(results) == len(running):
            return done("applied", "실행기 적용 확인(" + ", ".join(state.runner_paths(root, t)["label"] for t in running) + ")",
                        diff, hist.name, running)
        miss = [state.runner_paths(root, t)["label"] for t in running if t not in results]
        return done("unconfirmed", f"파일은 바꿈 — 적용 기록을 기다림 안에 못 봄({', '.join(miss)})", diff, hist.name, running)
    finally:
        _unlink(tmp)
        release_lock(root)
