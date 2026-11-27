"""바깥 신호 문 — 실행기 밖(AI 판단 · 텔레그램 · 사람)에서 온 신호 · 거르기 · 검토 답을 실행기가 받는 단 하나의 입구.

왜 파일인가: 판단 · 봇 · 화면은 실행기에 직접 말하지 않는다. 문 폴더에 파일을 쓰고, 실행기가 봉 처리 때 읽는다.
  그래서 바깥 프로세스가 죽어도 실행기는 살고, 바깥은 주문 함수를 부르지 못한다(주문은 언제나 실행기의 위험 점검을 거친다).
꺼져 있으면(LiveRunner.ext = None) 실행기는 예전과 똑같이 움직이고 이 폴더를 만들지도 읽지도 않는다.
문 자리: 실행기 state 폴더 아래 ext/ (모의 state/ext/, 실시간 시뮬레이션 state/sim/ext/). 실행기마다 문이 따로라
  같은 신호를 두 실행기에 주려면 문마다 한 장씩 쓴다(신호 파일은 읽은 실행기가 옮겨 간다).

문 폴더
  inbox/*.json            신호 하나 = 파일 하나. 쓰는 쪽은 *.tmp 로 다 쓴 뒤 os.replace 로 *.json 을 만든다.
                          실행기가 봉 처리 첫머리에 읽고 inbox_done/ 으로 옮긴다(한 번만 읽음).
  gates/<이름>.json        새 진입 막음. 만료 시각이 지나면 무시한다(판단이 멈춰도 영원히 막지 않게).
  review.json             '전략 진입을 바로 내지 말고 먼저 물어라' 표시(검토). 없으면 검토 꺼짐.
  review_requests/<id>.json  실행기가 낸 검토 요청(판단이 읽는다). 결론이 나면 실행기가 지운다.
                             실행기를 다시 띄우면 남은 요청은 review_cancelled(버림)로 기록하고 지운다(대기는 메모리에만).
  owned.json              바깥 신호로 산 종목(실행기가 쓴다).
  bars/<종목>.csv          실행기가 받은 닫힌 1분봉 최근 240개(실행기가 쓴다) — 판단이 봉을 따로 조회하지 않게.

신호 칸(inbox)
  id(필수, 겹치면 버림) · source(필수: "ai:<판단>" · "telegram" · "manual" · "browser:<사이트>") · code(필수, 6자리 문자열,
  실행기 종목 목록 안) · action(필수: entry · exit · deny) · expires_ms(필수, 지나면 버림)
  amount(선택, 원 — 설정 금액을 넘으면 설정 금액) · exit_by(선택: signal 기본 · strategy) ·
  max_hold_min(entry + exit_by signal 이면 필수 — 바깥이 산 것이 팔 길 없이 남지 않게) · stop_pct(선택, 손절 %) ·
  review_of(검토 답일 때 그 검토 id: entry = 통과, deny = 거절) · ref · note(기록용)
거르기 칸(gates): block_entries(true) · expires_ms(필수) · reason · codes(선택, 없으면 모든 종목) · ref
검토 표시 칸(review.json): expires_ms(판단이 살아 있다는 표시 — 판단이 주기적으로 늘린다) ·
  on_timeout(skip 기본 = 답이 없으면 진입하지 않음 · pass = 답이 없으면 전략대로 진입) · codes(선택) · ref
검토 규칙: 전략 진입이 위험 점검 · 거르기를 통과하면 진입하지 않고 entry_review 를 남긴다. 답 마감은 한 봉 —
  다음 봉 처리 첫머리까지 inbox 에 답이 없으면 review_expired(on_timeout 대로). 종목마다 검토는 하나.
  검토 대기는 메모리에만 둔다(실행기를 다시 띄우면 대기 중 검토는 사라지고, 늦은 답은 '맞는 검토 없음'으로 버린다).
재현: ReplayDoor 는 기록해 둔 신호 · 거르기 · 검토 표시를 가상 시계에 맞춰 내준다(백테스트에서 판단을 다시 틀기).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

CODE_RX = re.compile(r"^\d{6}$")
SOURCES = ("ai:", "telegram", "manual", "browser:")
ACTIONS = ("entry", "exit", "deny")
BARS_KEEP = 240


def atomic_write(path: Path, obj) -> None:
    """임시 파일에 다 쓴 뒤 바꿔 넣는다. 윈도우에서는 상대가 파일을 열어 둔 순간 실패하므로 잠깐씩 다시 한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    _replace(tmp, path)


def _replace(tmp: Path, path: Path) -> bool:
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.05)
    try:
        tmp.unlink()
    except OSError:
        pass
    return False


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_signal(d, now_ms: int, codes: list[str]) -> str | None:
    """None = 통과, 문자열 = 버린 이유."""
    if not isinstance(d, dict):
        return "객체 아님"
    for k in ("id", "source", "code", "action", "expires_ms"):
        if k not in d:
            return f"칸 없음: {k}"
    if not any(str(d["source"]).startswith(s) for s in SOURCES):
        return f"모르는 출처: {str(d['source'])[:30]}"
    if not isinstance(d["code"], str) or not CODE_RX.match(d["code"]):
        return "종목 코드 형식(6자리 문자열)"
    if d["action"] not in ACTIONS:
        return f"모르는 action: {str(d['action'])[:20]}"
    exp = _num(d["expires_ms"])
    if exp is None:
        return "expires_ms 형식(ms 숫자)"
    if exp <= now_ms:
        return "만료됨"
    if d["code"] not in codes:
        return "실행기 종목 목록 밖(봉 · 현재가를 받지 않는 종목)"
    if d.get("amount") is not None and not (_num(d["amount"]) or 0) > 0:
        return "금액은 0보다 커야 함"
    if d.get("exit_by", "signal") not in ("strategy", "signal"):
        return "exit_by 는 strategy · signal"
    if d.get("review_of") is not None and d["action"] == "exit":
        return "review_of 는 진입(통과) · deny(거절)에만"
    if d["action"] == "deny" and d.get("review_of") is None:
        return "deny 는 검토 답(review_of)에만"
    if d.get("stop_pct") is not None and not (_num(d["stop_pct"]) or 0) > 0:
        return "stop_pct 는 0보다 큰 %"
    if d["action"] == "entry" and d.get("exit_by", "signal") == "signal" and d.get("review_of") is None:
        mh = _num(d.get("max_hold_min"))
        if mh is None or mh <= 0:
            return "max_hold_min 없음(바깥 규칙으로 파는 진입은 보유 시간 상한이 있어야 함)"
    return None


class Door:
    """문 공통(신호 거르기 · 소유 표시 · 검토 대기). 실시간은 FileDoor, 백테스트는 ReplayDoor."""
    publish = False

    def __init__(self, log):
        self.log = log
        self.owned: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}       # 검토 대기 {종목: 요청 + step · on_timeout}
        self.step_no = 0
        self._seen: set[str] = set()
        self._gates: dict[str, dict] = {}
        self._review_down = False

    # --- 바꿔 끼우는 자리 ---
    def raw_signals(self, now_ms: int) -> list[dict]:
        return []

    def gate_states(self, now_ms: int) -> dict[str, dict]:
        return self._gates

    def review_state(self, now_ms: int) -> dict | None:
        return None

    def on_review(self, req: dict) -> None:
        """검토 요청을 낸 뒤(실시간은 요청 파일을 쓴다)."""

    def on_review_done(self, rid: str) -> None:
        """검토가 결론 났을 때(통과 · 거절 · 마감 · 취소)."""

    def save(self) -> None:
        pass

    def publish_bars(self, code: str, bars) -> None:
        pass

    # --- 신호 ---
    def drain(self, now_ms: int, codes: list[str]) -> list[dict]:
        out = []
        for d in self.raw_signals(now_ms):
            sid = str(d.get("id", "")) if isinstance(d, dict) else ""
            if sid and sid in self._seen:
                self.log.write("ext_rejected", id=sid, reason="같은 id 두 번")
                continue
            why = validate_signal(d, now_ms, codes)
            if why:
                self.log.write("ext_rejected", id=sid, reason=why,
                               source=str((d if isinstance(d, dict) else {}).get("source", ""))[:40])
                continue
            self._seen.add(sid)
            if d.get("review_of") is not None and "exit_by" not in d:
                d = {**d, "exit_by": "strategy"}            # 검토를 통과한 전략 진입은 전략 청산이 판다
            out.append(d)
        return out

    # --- 거르기 ---
    def gate_block(self, code: str, now_ms: int) -> str | None:
        for name, g in sorted(self.gate_states(now_ms).items()):
            if not isinstance(g, dict) or not g.get("block_entries"):
                continue
            if (_num(g.get("expires_ms")) or 0) <= now_ms:
                continue
            cs = g.get("codes")
            if cs and code not in cs:
                continue
            return f"{name}: {str(g.get('reason', ''))[:80]}"
        return None

    # --- 소유 ---
    def owns(self, code: str) -> bool:
        """바깥 규칙으로만 파는 종목인가(exit_by = signal)."""
        o = self.owned.get(code)
        return bool(o) and o.get("exit_by") == "signal"

    def mark_owned(self, code: str, sig: dict, t_ms: int) -> None:
        self.owned[code] = {"signal_id": sig.get("id"), "source": sig.get("source"), "t_ms": int(t_ms),
                            "exit_by": sig.get("exit_by", "signal"), "stop_pct": sig.get("stop_pct"),
                            "max_hold_min": sig.get("max_hold_min")}
        self.save()

    def release(self, code: str) -> None:
        if self.owned.pop(code, None) is not None:
            self.save()


class FileDoor(Door):
    """실시간: 문 폴더를 읽는다."""
    publish = True

    def __init__(self, root: Path, log):
        super().__init__(log)
        self.root = Path(root)
        self.inbox, self.done = self.root / "inbox", self.root / "inbox_done"
        self.gates_dir, self.req_dir = self.root / "gates", self.root / "review_requests"
        for p in (self.inbox, self.done, self.gates_dir, self.req_dir, self.root / "bars"):
            p.mkdir(parents=True, exist_ok=True)
        for p in sorted(self.req_dir.glob("*.json")):         # 검토 대기는 메모리에만 — 다시 띄우면 남은 요청은 버림으로 기록
            self.log.write("review_cancelled", id=p.stem, reason="실행기 다시 시작 — 대기 중이던 검토 버림")
            self.on_review_done(p.stem)                       # 지우기(실패해도 시작은 계속)
        st = self.root / "owned.json"
        if st.exists():
            try:
                self.owned = json.loads(st.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                self.log.write("ext_rejected", file="owned.json", reason="읽기 실패 — 빈 것으로 시작")
                self.owned = {}

    def raw_signals(self, now_ms: int) -> list[dict]:
        out = []
        for p in sorted(self.inbox.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                if isinstance(e, PermissionError):
                    continue                                  # 쓰는 쪽이 붙잡고 있음 — 다음 봉에
                self.log.write("ext_rejected", file=p.name, reason=f"읽기 실패 {type(e).__name__}")
                d = None
            try:
                os.replace(p, self.done / p.name)             # 한 번만 읽는다
            except OSError:
                continue
            if d is not None:
                out.append(d)
        return out

    def gate_states(self, now_ms: int) -> dict[str, dict]:
        names = set()
        for p in self.gates_dir.glob("*.json"):
            names.add(p.stem)
            try:
                self._gates[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass                                          # 바꿔 넣는 순간 — 마지막으로 읽은 값
        for name in list(self._gates):
            if name not in names:
                self._gates.pop(name)
        return self._gates

    def review_state(self, now_ms: int) -> dict | None:
        p = self.root / "review.json"
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {"expires_ms": 0}
        except (OSError, ValueError):
            return {"expires_ms": 0}                          # 깨진 표시 = 판단이 살아 있는지 모름 → 마감과 같게

    def on_review(self, req: dict) -> None:
        atomic_write(self.req_dir / f"{req['id']}.json", req)

    def on_review_done(self, rid: str) -> None:
        try:
            (self.req_dir / f"{rid}.json").unlink()
        except OSError:
            pass

    def save(self) -> None:
        atomic_write(self.root / "owned.json", self.owned)

    def publish_bars(self, code: str, bars) -> None:
        p = self.root / "bars" / f"{code}.csv"
        tmp = p.with_name(p.name + ".tmp")
        bars.tail(BARS_KEEP).to_csv(tmp, index=False)
        _replace(tmp, p)


class ReplayDoor(Door):
    """백테스트: 기록해 둔 바깥 판단(jsonl, 한 줄 = {type: signal|gate|review, at_ms: 문에 닿은 시각, …})을 가상 시계대로."""

    def __init__(self, path: Path, log):
        super().__init__(log)
        self.recs = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
        self.recs.sort(key=lambda r: r["at_ms"])
        self._i = 0
        self._review: dict | None = None

    def _apply(self, r: dict) -> None:
        if r["type"] == "gate":
            self._gates[r["name"]] = r["gate"]
        elif r["type"] == "review":
            self._review = r.get("review")

    def raw_signals(self, now_ms: int) -> list[dict]:
        out = []
        while self._i < len(self.recs) and self.recs[self._i]["at_ms"] <= now_ms:
            r = self.recs[self._i]
            self._i += 1
            if r["type"] == "signal":
                out.append(r["signal"])
            else:
                self._apply(r)
        return out

    def _catch_up(self, now_ms: int) -> None:
        # 신호는 drain 에서만 꺼내고, 거르기 · 검토 표시는 지금 시각까지의 것을 반영한다(봉 처리 중에도 가상 시계가 흐름)
        j = self._i
        while j < len(self.recs) and self.recs[j]["at_ms"] <= now_ms:
            if self.recs[j]["type"] != "signal":
                self._apply(self.recs[j])
            j += 1

    def gate_states(self, now_ms: int) -> dict[str, dict]:
        self._catch_up(now_ms)
        return self._gates

    def review_state(self, now_ms: int) -> dict | None:
        self._catch_up(now_ms)
        return self._review


# ---------- 실행기 쪽(LiveRunner 가 부른다 — 실행기 상태를 인자로 받는다) ----------
def warn_left_owned(root: Path, log, say=print) -> None:
    """문을 끈 채 시작했는데 바깥이 산 종목 표시가 남아 있으면 경고한다(그대로 두면 전략 청산이 그것을 판다).
    owned.json 하나만 본다 — 폴더를 만들거나 신호를 읽지 않는다."""
    p = Path(root) / "owned.json"
    if not p.exists():
        return
    try:
        owned = json.loads(p.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        owned = {"?": {}}
    if owned:
        log.write("ext_owned_left", codes=sorted(owned), note="문(--ext) 없이 시작 — 전략 청산이 바깥이 산 것을 팔 수 있음")
        say(f"  [주의] 바깥 신호로 산 종목 표시가 남아 있습니다({', '.join(sorted(owned))}) — 문(--ext) 없이 시작함")


def step(r) -> list[dict]:
    """봉 처리 첫머리: 들어온 신호 → _act(진입은 위험 점검 · 거르기를 거침), 검토 마감, 바깥이 산 것의 청산 규칙."""
    door, acts = r.ext, []
    door.step_no += 1
    now = r.clock_ms()
    try:
        sigs = door.drain(now, r.codes)
    except Exception as e:
        r.log.write("error", where="ext_drain", error=f"{type(e).__name__}: {str(e)[:200]}")
        sigs = []
    for s in sigs:
        code = s["code"]
        if s.get("review_of") is not None:                    # 검토 답은 대기 중인 그 검토와 맞아야 한다
            p = door.pending.get(code)
            if not p or p["id"] != s["review_of"]:
                r.log.write("ext_rejected", id=s["id"], reason="맞는 검토 대기 없음(마감 · 취소 · 다른 id)",
                            review_of=s["review_of"], source=s["source"])
                continue
            door.pending.pop(code)
            door.on_review_done(p["id"])
            if s["action"] == "deny":
                r.log.write("review_denied", id=p["id"], code=code, source=s["source"], note=str(s.get("note", ""))[:200])
                continue
        amount = s.get("amount")
        cap = None if amount is None else min(float(amount), float(r.amount))
        r.log.write("ext_signal", id=s["id"], source=s["source"], code=code, action=s["action"], amount=amount,
                    amount_used=cap, exit_by=s.get("exit_by", "signal"), max_hold_min=s.get("max_hold_min"),
                    review_of=s.get("review_of"), ref=s.get("ref"), note=str(s.get("note", ""))[:200], t_signal_ms=now)
        try:
            a = r._act(code, "long_entry" if s["action"] == "entry" else "long_exit", now, amount=amount, source=s)
            if a:
                acts.append(a)
        except Exception as e:
            r.log.write("error", code=code, where="ext_act", error=f"{type(e).__name__}: {str(e)[:200]}")
    for code, p in list(door.pending.items()):               # 답 마감 = 한 봉: 앞 봉에 낸 검토에 이번 봉까지 답이 없음
        if p["step"] >= door.step_no:
            continue
        door.pending.pop(code)
        door.on_review_done(p["id"])
        r.log.write("review_expired", id=p["id"], code=code, on_timeout=p["on_timeout"])
        if p["on_timeout"] == "pass":
            try:
                a = r._act(code, "long_entry", now,
                           source={"id": p["id"], "source": "review:on_timeout_pass", "exit_by": "strategy"})
                if a:
                    acts.append(a)
            except Exception as e:
                r.log.write("error", code=code, where="review_pass", error=f"{type(e).__name__}: {str(e)[:200]}")
    for code, o in list(door.owned.items()):                 # 바깥이 산 것: 다 팔렸으면 표시 풀기 · 손절 · 보유 시간
        if not r.oms.held(code) and not r.oms.active_for(code):
            door.release(code)
            continue
        if o.get("exit_by") != "signal" or r.oms.active_for(code) or not r.oms.held(code):
            continue
        why = None
        px = r.oms.current.get(code)                         # 판정에 쓴 값을 기록에 남긴다
        avg = (r.oms.positions.get(code) or {}).get("avg")
        if _num(o.get("stop_pct")):
            # 문은 봉 처리 첫머리에서 돈다 — oms.current 는 아직 앞 봉의 종가(산 직후라면 사기 전 값)라, 손절은 지금 현재가를 새로 받아 본다.
            # 받지 못하면 아는 값으로 판정한다(2026-11-26 장중: 산 값보다 높게 팔리고도 '손절'로 찍혔다).
            px = r._price_safe(code) or px
        mh = _num(o.get("max_hold_min"))
        if mh and now - int(o.get("t_ms") or 0) >= mh * 60_000:
            why = f"보유 {o['max_hold_min']}분 지남"
        elif _num(o.get("stop_pct")):
            if px and avg and (px / avg - 1) * 100 <= -abs(float(o["stop_pct"])):
                why = f"손절 {o['stop_pct']}%"
        if why:
            r.log.write("ext_rule_exit", code=code, reason=why, signal_id=o.get("signal_id"), px=px, avg=avg)
            try:
                a = r._act(code, "long_exit", now, source={"id": o.get("signal_id"), "source": "rule:" + why})
                if a:
                    acts.append(a)
            except Exception as e:
                r.log.write("error", code=code, where="ext_rule_exit", error=f"{type(e).__name__}: {str(e)[:200]}")
    return acts


def entry_held(r, code: str, t: int, source: dict | None) -> bool:
    """위험 점검을 통과한 진입 앞: 거르기(모든 진입) → 검토(전략 진입만). True = 이번 진입은 내지 않음."""
    door = r.ext
    why = door.gate_block(code, t)
    if why:
        r.log.write("entry_filtered", code=code, gate=why, **({"source": source.get("source")} if source else {}))
        r.say(f"  [거르기] {code}: {why}")
        return True
    if source is None:
        return _ask_review(r, code, t)
    return False


def _ask_review(r, code: str, t: int) -> bool:
    door = r.ext
    st = door.review_state(t)
    if st is None or (st.get("codes") and code not in st["codes"]):
        return False                                           # 검토 꺼짐(또는 이 종목은 대상 아님)
    on_timeout = "pass" if st.get("on_timeout") == "pass" else "skip"
    if (_num(st.get("expires_ms")) or 0) <= t:                 # 판단이 살아 있지 않음 — 마감과 같게 처리
        if not door._review_down:
            door._review_down = True
            r.log.write("review_unavailable", code=code, on_timeout=on_timeout,
                        note="검토 판단이 응답하지 않음(review.json 만료)")
        if on_timeout == "pass":
            return False
        r._skip(code, "검토 판단이 응답하지 않음 — 진입 안 함(on_timeout: skip)")
        return True
    door._review_down = False
    p = door.pending.get(code)
    if p:
        r._skip(code, f"검토 대기 중({p['id']})")
        return True
    tf_ms = int(r.tf) * 60_000
    deadline = (t // tf_ms) * tf_ms + tf_ms + int(float(r.settle) * 1000)    # 다음 봉 처리 어림(봉 마감 + settle)
    bar = r.last_bar.get(code)
    req = {"id": f"rv-{code}-{t}", "code": code, "t_signal_ms": int(t), "deadline_ms": deadline,
           "bar_time": bar.isoformat() if bar is not None else None, "amount": r.amount, "ref": st.get("ref")}
    door.pending[code] = {**req, "step": door.step_no, "on_timeout": on_timeout}
    r.log.write("entry_review", **req, on_timeout=on_timeout)
    r.say(f"  [검토 요청] {code}: 다음 봉까지 답을 기다림({req['id']})")
    door.on_review(req)
    return True


def strategy_exit(r, code: str) -> bool:
    """전략 청산 신호 앞: 대기 중 검토는 거두고, 바깥 규칙으로 파는 종목이면 True(전략 청산으로 팔지 않음)."""
    door = r.ext
    p = door.pending.pop(code, None)
    if p:
        door.on_review_done(p["id"])
        r.log.write("review_cancelled", id=p["id"], code=code, reason="검토 대기 중 전략 청산 신호")
    if door.owns(code):
        r._skip(code, "바깥 신호로 산 것(exit_by=signal) — 전략 청산 신호로는 팔지 않음")
        return True
    return False
