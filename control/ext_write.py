"""실행기 문에 쓰기 — 신호 · 거르기 · 검토 표시 · 검토 답을 쓰는 유일한 함수들(봇 · AI 판단 · 화면이 함께 쓴다).

형식 검사는 실행기와 같은 strategy.ext.validate_signal, 쓰기는 strategy.ext.atomic_write(임시 파일 → os.replace)를 그대로 쓴다.
대상(target): paper(모의 state/ext/) · sim(시뮬레이션 state/sim/ext/) · both(둘 다 — 신호 파일은 읽은 실행기가 옮겨 가므로
  문마다 한 장씩 쓴다) · sim_<태그>.
거부: 문이 꺼져 있으면(오늘 start 사건의 ext 가 거짓 · 실행기가 끝남 · 응답 없음) 쓰지 않는다.
가짜 공급자(fake=True): 대상이 모의면 쓰지 않고 기록만 한다 — 가짜 응답이 실제 모의 주문이 되지 않게(시뮬레이션에는 쓴다).
dry-run 은 모의와 같은 문 폴더(state/ext/)를 쓰므로, 모의 실행기가 돌고 있으면 dry-run 문에는 쓰지 않는다.
걷기: clear_gate(거르기) · clear_review(검토 표시) — 막거나 묻는 쪽을 푸는 일이라 문이 꺼져 있어도 된다.
정리: cleanup(앞선 날의 inbox_done · 만료된 gates · review_requests, 오늘 것은 남김) — 실행기 시작 전에 run_daily.ps1 이 부른다.
모든 시도는 logs/control_YYYYMMDD.jsonl 에 남는다. 돌려주는 값 {ok, results: [{target, ok, written, why, file}]}.
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path

from control import state
from control.common import ROOT, journal, now_ms, read_json, runner_paths, scrub

_NAME_RX = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")
_CODE_RX = re.compile(r"^\d{6}$")
GATE_MAX_S = 24 * 3600                # 거르기 한 장의 최대 유효 시간(판단이 멈춰도 하루를 넘겨 막지 않게)


def _targets(target) -> list[str]:
    if target in ("both", "둘 다", None):
        return ["paper", "sim"]
    if isinstance(target, (list, tuple)):
        return [str(t) for t in target]
    return [str(target)]


def _gatekeep(root: Path, t: str, fake: bool, now: int, pid_alive=None) -> tuple[str | None, dict | None]:
    """(거부 이유, 문 상태)."""
    try:
        runner_paths(root, t)
    except ValueError as e:
        return str(e), None
    if fake and t == "paper":
        return "가짜 공급자 — 모의 문에는 쓰지 않고 기록만 함", None
    if t == "dry" and state.alive(root, "paper", pid_alive=pid_alive)[0]:
        # dry-run 과 모의 실행기는 같은 문 폴더(state/ext/)를 쓴다 — 모의가 돌면 dry-run 앞으로 쓴 것을 모의가 읽는다
        return "모의 실행기가 같은 문(state/ext)을 쓰는 중 — dry-run 문에는 쓰지 않음", None
    d = state.door_state(root, t, now=now, pid_alive=pid_alive)
    if not d["open"]:
        return f"문이 꺼져 있음({d['why']})", d
    return None, d


def _write_each(root: Path, kind: str, target, fake: bool, actor: str, build, pid_alive=None, **meta) -> dict:
    from strategy.ext import atomic_write
    now = now_ms()
    results = []
    for t in _targets(target):
        why, door = _gatekeep(root, t, fake, now, pid_alive)
        item = {"target": t, "ok": False, "written": False, "why": why or "", "file": None}
        if not why:
            try:
                rel, obj, err = build(t, now)
            except Exception as e:
                rel, obj, err = None, None, f"만들기 실패 {type(e).__name__}"
            if err:
                item["why"] = err
            else:
                p = runner_paths(root, t)["ext"] / rel
                try:
                    atomic_write(p, obj)
                    item.update(ok=True, written=True, why="", file=rel.replace("\\", "/"))
                except OSError as e:
                    item["why"] = f"쓰기 실패 {type(e).__name__}"
        results.append(item)
        journal(root, f"ext_{kind}", actor=str(actor)[:50], target=t, ok=item["ok"], why=scrub(item["why"]),
                file=item["file"], fake=bool(fake), **meta)
    return {"ok": bool(results) and all(r["ok"] for r in results), "results": results}


def write_signal(root: Path = ROOT, target="paper", *, source: str, code: str, action: str, expires_s: float = 120,
                 amount: float | None = None, exit_by: str | None = None, max_hold_min: float | None = None,
                 stop_pct: float | None = None, review_of: str | None = None, ref: str | None = None,
                 note: str | None = None, sid: str | None = None, fake: bool = False, actor: str | None = None,
                 pid_alive=None) -> dict:
    """진입(entry) · 청산(exit) · 검토 거절(deny) 신호 한 장. 실행기가 다음 봉 처리 첫머리에 읽어 위험 점검 뒤 주문한다."""
    base_id = sid or f"{str(source).replace(':', '-')[:30]}-{code}-{now_ms()}-{secrets.token_hex(2)}"

    def build(t, now):
        sig = {"id": base_id + ("" if t == "paper" else f"-{t}"), "source": source, "code": code, "action": action,
               "expires_ms": now + int(float(expires_s) * 1000)}
        for k, v in (("amount", amount), ("exit_by", exit_by), ("max_hold_min", max_hold_min), ("stop_pct", stop_pct),
                     ("review_of", review_of), ("ref", ref), ("note", note)):
            if v is not None:
                sig[k] = v
        from strategy.ext import validate_signal
        why = validate_signal(sig, now, state.runner_codes(root, t))
        if why:
            return None, None, f"신호 형식: {why}"
        if not _NAME_RX.match(sig["id"].replace(":", "-")):
            return None, None, "신호 id 형식"
        return f"inbox/{sig['id'].replace(':', '-')}.json", sig, None
    return _write_each(root, "signal", target, fake, actor or source, build, pid_alive=pid_alive,
                       source=str(source)[:40], code=str(code)[:6], signal_action=str(action)[:10], amount=amount,
                       max_hold_min=max_hold_min, stop_pct=stop_pct, review_of=review_of)


def write_gate(root: Path = ROOT, target="paper", *, name: str, reason: str, expires_s: float, codes: list | None = None,
               ref: str | None = None, fake: bool = False, actor: str = "desk", pid_alive=None) -> dict:
    """'새 진입 막음' 한 장(gates/<name>.json). 진입만 막고 청산은 막지 않는다. 만료되면 실행기가 무시한다."""
    def build(t, now):
        if not _NAME_RX.match(str(name)):
            return None, None, "거르기 이름은 영문 · 숫자 · _ . - 만"
        try:
            sec = float(expires_s)
        except (TypeError, ValueError):
            return None, None, "만료 시간(초) 형식"
        if not 0 < sec <= GATE_MAX_S:
            return None, None, f"만료 시간은 0초 ~ {GATE_MAX_S // 3600}시간"
        if codes is not None and (not isinstance(codes, list) or any(not isinstance(c, str) or not _CODE_RX.match(c) for c in codes)):
            return None, None, "codes 는 6자리 문자열 목록"
        g = {"block_entries": True, "expires_ms": now + int(sec * 1000), "reason": str(reason)[:200]}
        if codes:
            g["codes"] = list(codes)
        if ref is not None:
            g["ref"] = ref
        return f"gates/{name}.json", g, None
    return _write_each(root, "gate", target, fake, actor, build, pid_alive=pid_alive, name=str(name)[:60],
                       reason=str(reason)[:120], expires_s=expires_s, codes=codes)


def clear_gate(root: Path = ROOT, target="paper", *, name: str, actor: str = "desk") -> dict:
    """거르기를 걷는다(파일을 지움). 문이 꺼져 있어도 지울 수 있다(막는 쪽을 푸는 일이라)."""
    results = []
    for t in _targets(target):
        item = {"target": t, "ok": False, "written": False, "why": "", "file": None}
        if not _NAME_RX.match(str(name)):
            item["why"] = "거르기 이름 형식"
        else:
            p = runner_paths(root, t)["ext"] / "gates" / f"{name}.json"
            try:
                p.unlink()
                item.update(ok=True, file=f"gates/{name}.json")
            except FileNotFoundError:
                item.update(ok=True, why="이미 없음")
            except OSError as e:
                item["why"] = f"지우기 실패 {type(e).__name__}"
        results.append(item)
        journal(root, "ext_gate_clear", actor=actor, target=t, ok=item["ok"], why=item["why"], name=str(name)[:60])
    return {"ok": all(r["ok"] for r in results), "results": results}


def write_review(root: Path = ROOT, target="paper", *, expires_s: float, on_timeout: str = "skip",
                 codes: list | None = None, ref: str | None = None, fake: bool = False, actor: str = "desk",
                 pid_alive=None) -> dict:
    """검토 표시(review.json) — '전략 진입을 바로 내지 말고 먼저 물어라' + 판단이 살아 있다는 표시(만료 시각)."""
    def build(t, now):
        if on_timeout not in ("skip", "pass"):
            return None, None, "on_timeout 은 skip · pass"
        if codes is not None and (not isinstance(codes, list) or any(not isinstance(c, str) or not _CODE_RX.match(c) for c in codes)):
            return None, None, "codes 는 6자리 문자열 목록"
        r = {"expires_ms": now + int(float(expires_s) * 1000), "on_timeout": on_timeout}
        if codes:
            r["codes"] = list(codes)
        if ref is not None:
            r["ref"] = ref
        return "review.json", r, None
    return _write_each(root, "review", target, fake, actor, build, pid_alive=pid_alive, on_timeout=on_timeout)


def clear_review(root: Path = ROOT, target="paper", *, ref: str | None = None, actor: str = "desk") -> dict:
    """검토 표시를 걷는다(review.json 을 지움) — 실행기는 다음 전략 진입부터 묻지 않고 예전처럼 바로 낸다.
    문이 꺼져 있어도 지울 수 있다(묻는 쪽을 푸는 일이라). ref 를 주면 그 ref 로 쓴 표시만 지운다
    (판단 프로세스가 끝날 때 자기가 쓴 것만 걷게 — 다른 판단이 쓴 표시는 남김)."""
    results = []
    for t in _targets(target):
        item = {"target": t, "ok": False, "written": False, "why": "", "file": None}
        try:
            p = runner_paths(root, t)["ext"] / "review.json"
        except ValueError as e:
            item["why"] = str(e)
            results.append(item)
            continue
        cur = read_json(p, default=None) if p.exists() else None
        if cur is None:
            item.update(ok=True, why="이미 없음")
        elif ref is not None and (not isinstance(cur, dict) or cur.get("ref") != ref):
            item.update(ok=True, why="다른 쪽이 쓴 표시 — 남김")
        else:
            try:
                p.unlink()
                item.update(ok=True, file="review.json")
            except FileNotFoundError:
                item.update(ok=True, why="이미 없음")
            except OSError as e:
                item["why"] = f"지우기 실패 {type(e).__name__}"
        results.append(item)
        journal(root, "ext_review_clear", actor=str(actor)[:50], target=t, ok=item["ok"], why=item["why"],
                ref=None if ref is None else str(ref)[:60])
    return {"ok": all(r["ok"] for r in results), "results": results}


CLEAN_DIRS = ("inbox_done", "gates", "review_requests")


def cleanup(root: Path = ROOT, target="paper", *, keep_days: int = 7, now: float | None = None,
            actor: str = "run_daily") -> dict:
    """문 폴더 정리 — 실행기가 시작하기 전에 한 번 부른다(run_daily.ps1). 오늘 고친 파일은 어떤 것도 지우지 않는다.
      inbox_done/      읽고 옮겨 둔 신호: 마지막 keep_days 날(오늘 포함)보다 앞선 날 것을 지움
      gates/           앞선 날에 쓴 거르기 중 만료된 것(또는 읽히지 않는 것)을 지움 — 걸려 있는 것은 남김
      review_requests/ 앞선 날의 검토 요청: 검토 대기는 실행기 메모리에만 있어 다음 날에는 답할 상대가 없다 → 지움
    review.json · owned.json · inbox/ · bars/ 는 건드리지 않는다. 반환 {ok, results: [{target, removed{폴더: 수}, failed}]}."""
    import time as _time
    from datetime import date, datetime, timedelta
    now = _time.time() if now is None else float(now)
    today = datetime.fromtimestamp(now).date()
    keep_from = today - timedelta(days=max(int(keep_days), 1) - 1)    # 이 날부터는 남김(keep_days=1 → 오늘만)

    def day_of(p: Path) -> date | None:
        try:
            return datetime.fromtimestamp(p.stat().st_mtime).date()
        except OSError:
            return None
    results = []
    for t in _targets(target):
        item = {"target": t, "removed": {k: 0 for k in CLEAN_DIRS}, "failed": 0, "why": ""}
        try:
            ext = runner_paths(root, t)["ext"]
        except ValueError as e:
            item["why"] = str(e)
            results.append(item)
            continue
        for sub in CLEAN_DIRS:
            try:
                files = sorted((ext / sub).glob("*.json"))
            except OSError:
                files = []
            for f in files:
                d = day_of(f)
                if d is None or d >= today:
                    continue                                   # 오늘 것은 남김(돌고 있는 실행기 · 판단이 쓰는 중일 수 있음)
                if sub == "inbox_done" and d >= keep_from:
                    continue
                if sub == "gates":
                    g = read_json(f, default=None)
                    try:
                        live = isinstance(g, dict) and float(g.get("expires_ms") or 0) > now * 1000
                    except (TypeError, ValueError):
                        live = False
                    if live:
                        continue                               # 앞선 날에 썼어도 아직 걸려 있는 거르기
                try:
                    f.unlink()
                    item["removed"][sub] += 1
                except OSError:
                    item["failed"] += 1
        results.append(item)
        journal(root, "ext_cleanup", actor=str(actor)[:50], target=t, keep_days=int(keep_days), why=item["why"],
                removed=item["removed"], failed=item["failed"])
    return {"ok": all(not r["why"] and not r["failed"] for r in results), "results": results}


def answer_review(root: Path = ROOT, target="paper", *, review_id: str, code: str, allow: bool, source: str,
                  expires_s: float = 60, note: str | None = None, fake: bool = False, pid_alive=None) -> dict:
    """검토 요청에 답: allow=True 면 진입(통과, 전략 청산이 판다), False 면 deny(거절)."""
    return write_signal(root, target, source=source, code=code, action="entry" if allow else "deny",
                        expires_s=expires_s, review_of=review_id, note=note, fake=fake, pid_alive=pid_alive)


def main(argv=None) -> int:
    """명령줄: python -X utf8 -m control.ext_write cleanup <paper|sim|sim_태그> [--keep-days N] (run_daily.ps1 이 부름)."""
    import argparse
    ap = argparse.ArgumentParser(description="문 폴더 정리(오늘 것은 남김)")
    ap.add_argument("what", choices=["cleanup"])
    ap.add_argument("target")
    ap.add_argument("--keep-days", type=int, default=7)
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args(argv)
    r = cleanup(a.root, a.target, keep_days=a.keep_days)
    for it in r["results"]:
        rm = " · ".join(f"{k} {v}" for k, v in it["removed"].items())
        print(f"문 정리 {it['target']}: 지움 {rm} · 실패 {it['failed']}{(' · ' + it['why']) if it['why'] else ''}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
