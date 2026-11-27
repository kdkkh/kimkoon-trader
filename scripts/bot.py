"""텔레그램 관제 봇 — 휴대폰에서 자동매매(모의 · 시뮬레이션)를 보고, 켜고 끄고, 설정을 바꾸고, 문에 사고팔기 신호를 쓴다.
본인 개인 대화의 명령만 받는다. 속은 control/ 의 함수(화면 · AI 판단과 같은 함수)를 부른다.

명령(괄호 안은 영어 별칭)
  /상태 (/status)            모의 · 시뮬레이션: 살아 있는지 · 정지 파일 · 진행 중 주문 · 보유 · 오늘 진입 · 실현 · 문 상태
  /요약 (/summary)           오늘 기록 요약(끝났으면 하루 요약, 아니면 사건 수)
  /끄기 [모의|시뮬] (/off)    바로 정지 파일을 만든다(확인 없음). 대상을 안 주면 모의 + 돌고 있는 시뮬레이션
  /켜기 [모의|시뮬] (/on)     확인 번호 뒤: 휴장일 표 · 장 시각 확인 → run_daily.ps1 을 분리 프로세스로
  /설정 (/settings)          지금 설정 보기
  /설정 <칸> <값>             확인 번호 뒤: control.settings_edit 로 그 줄만 바꾸고 실행기 적용을 확인
  /사기 <종목> <금액> (/buy)  확인 번호 뒤: 문에 진입 신호(출처 telegram). 봇은 주문 함수를 부르지 않는다 — 실행기가 위험 점검 뒤 주문
  /팔기 <종목> (/sell)        확인 번호 뒤: 문에 청산 신호
  /읽기 <사이트 id> (/read)   브라우저 읽기 자리(모듈이 없으면 '모듈 없음')
  /검토끄기 [모의|시뮬] (/reviewoff)  검토 표시(review.json)를 걷는다(확인 없음 — 전략 진입을 예전처럼 바로 낸다).
                             대상을 안 주면 모의 · 시뮬레이션 둘 다. 판단 프로세스가 죽어 표시가 남았을 때 쓴다
  /확인 <번호> (/ok) · /취소 (/cancel) · /도움 (/help)
확인 단계: 켜기 · 설정 바꾸기 · 사기 · 팔기는 바뀔 내용(전 → 후)과 6자리 번호를 보내고, 60초 안의 '/확인 번호'에만 실행한다.
  번호는 한 번 쓰면 버리고, 3번 틀리면 버린다. 새 확인 명령을 보내면 앞의 것은 버린다.
사고팔기 명령은 기본 꺼짐 — 봇 설정 파일(이 폴더의 bot.yaml, 없으면 기본값)에서 켠다:
    trade_enabled: false     # true 면 /사기 · /팔기 를 받는다
    trade_target: paper      # 신호를 쓸 문(paper = 모의 state/ext/ · sim = 시뮬레이션 state/sim/ext/)
    buy_hold_min: 30         # 사기 신호의 보유 시간(분) — 지나면 실행기가 판다(문 규칙: 바깥이 산 것은 보유 시간 필수)
    buy_stop_pct: 2.0        # 사기 신호의 손절(%) — 비우면 손절 없음(보유 시간만)
    signal_expires_s: 120    # 신호 유효 시간(초) — 실행기가 그 안에 읽지 못하면 버린다
    sim_notify: false        # true 면 시뮬레이션 기록을 따라 읽어 '[시뮬]' 머리로 알림
    confirm_s: 60            # 확인 번호 유효 시간(초)
  (위 숫자는 예시 기본값이다. 매매 규칙 · 금액은 쓰는 사람이 정한다.)
지키는 것: 본인 id · 개인 대화만 · 봇이 켜지기 전에 쌓인 명령은 버림 · 명령 처리 · 보내기 · 받기 어디서 예외가 나도 기록하고 계속 돈다
  · 토큰은 출력 · 기록하지 않음 · 계좌 · 주문번호 · 키는 보내지 않음.
설정: 홈 폴더 KIS/config/telegram.yaml (bot_token, owner_id) — 사용자가 직접 넣는다.
실행: .venv/Scripts/python.exe -X utf8 scripts/bot.py      (Ctrl+C 로 끝)
종목 코드는 예시이며 추천이 아니다. 신호는 쓰는 사람이 보낸 것이고 매매 권유가 아니다.
"""
from __future__ import annotations

import importlib.util
import json
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from control import ext_write, settings_edit, state, switch  # noqa: E402
from control.common import runner_paths, scrub  # noqa: E402
from notify import telegram  # noqa: E402

OPTS_FILE = ROOT / "bot.yaml"
BOT_DEFAULTS = {"trade_enabled": False, "trade_target": "paper", "buy_hold_min": 30, "buy_stop_pct": 2.0,
                "signal_expires_s": 120, "sim_notify": False, "confirm_s": 60}
ALIASES = {"/상태": "status", "/status": "status", "/요약": "summary", "/summary": "summary",
           "/끄기": "off", "/off": "off", "/켜기": "on", "/on": "on", "/설정": "settings", "/settings": "settings",
           "/사기": "buy", "/buy": "buy", "/팔기": "sell", "/sell": "sell", "/읽기": "read", "/read": "read",
           "/확인": "ok", "/ok": "ok", "/취소": "cancel", "/cancel": "cancel",
           "/검토끄기": "review_off", "/reviewoff": "review_off",
           "/도움": "help", "/help": "help", "/start": "help"}
TARGET_WORDS = {"모의": "paper", "paper": "paper", "시뮬": "sim", "시뮬레이션": "sim", "sim": "sim"}
HELP = ("명령: /상태 · /요약 · /끄기 [모의|시뮬] · /켜기 [모의|시뮬] · /설정 [칸 값] · /사기 종목 금액 · /팔기 종목 · "
        "/읽기 id · /검토끄기 [모의|시뮬] · /확인 번호 · /취소 (모의투자 전용 · 종목은 예시)\n"
        "확인은 한 번에 하나만 기다립니다 — 답을 본 뒤 다음 명령을 보내세요(새 명령을 보내면 앞의 확인은 버려집니다)")
MAX_WRONG = 3


def _same_type(default, v) -> bool:
    if isinstance(default, bool):
        return isinstance(v, bool)
    if isinstance(default, str):
        return isinstance(v, str)
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def load_opts(path: Path = OPTS_FILE) -> tuple[dict, list[str]]:
    """봇 설정(bot.yaml) ← 기본값. 모르는 칸 · 형이 틀린 칸은 버리고 경고로 돌려준다."""
    o, warn = dict(BOT_DEFAULTS), []
    if not Path(path).exists():
        return o, warn
    try:
        import yaml
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
    except Exception as e:
        return o, [f"봇 설정을 읽지 못함({type(e).__name__}) — 기본값으로"]
    if not isinstance(d, dict):
        return o, ["봇 설정이 {칸: 값} 꼴이 아님 — 기본값으로"]
    for k, v in d.items():
        if k not in BOT_DEFAULTS:
            warn.append(f"모르는 칸 버림: {str(k)[:30]}")
        elif k == "buy_stop_pct" and v is None:
            o[k] = None
        elif not _same_type(BOT_DEFAULTS[k], v):
            warn.append(f"형이 맞지 않아 기본값: {k}")
        else:
            o[k] = v
    if o["trade_target"] not in ("paper", "sim"):
        warn.append("trade_target 은 paper · sim — paper 로")
        o["trade_target"] = "paper"
    return o, warn


def _won(v) -> str:
    try:
        return f"{float(v):,.0f}원"
    except (TypeError, ValueError):
        return "-"


class Bot:
    """명령 하나 → 답 글. 확인 번호 · 대기 중 작업을 들고 있다. 시험은 root 를 임시 폴더로, launcher 를 가짜로 준다."""

    def __init__(self, root: Path = ROOT, opts: dict | None = None, launcher=None, clock=time.time,
                 market_now=datetime.now, code_fn=None, run_async: bool = True, reply=None,
                 config_path: Path | None = None, pid_alive=None, holidays: Path = switch.HOLIDAYS):
        self.root = Path(root)
        self.opts = {**BOT_DEFAULTS, **(opts or {})}
        self.launcher = launcher
        self.clock = clock
        self.market_now = market_now
        self.code_fn = code_fn or (lambda: f"{secrets.randbelow(10 ** 6):06d}")
        self.run_async = run_async
        self.reply = reply
        self.config_path = config_path
        self.pid_alive = pid_alive
        self.holidays = holidays
        self.pending: dict | None = None
        self.watch: list[dict] = []              # 띄운 실행기의 시작 확인 기다림
        self.outbox: list[str] = []              # 뒤에서 끝난 일의 답(reply 가 없을 때)
        self._lock = threading.Lock()

    # ---------- 입구 ----------
    def handle(self, text: str) -> str:
        parts = (text or "").strip().split()
        what = ALIASES.get(parts[0].split("@")[0].lower(), "") if parts else ""
        args = parts[1:]
        fn = {"status": self.cmd_status, "summary": self.cmd_summary, "off": self.cmd_off, "on": self.cmd_on,
              "settings": self.cmd_settings, "buy": self.cmd_buy, "sell": self.cmd_sell, "read": self.cmd_read,
              "ok": self.cmd_ok, "cancel": self.cmd_cancel, "review_off": self.cmd_review_off}.get(what)
        return fn(args) if fn else HELP

    # ---------- 확인 단계 ----------
    def _ask(self, desc: str, run) -> str:
        replaced = self.pending is not None
        code = self.code_fn()
        self.pending = {"code": code, "t": self.clock(), "wrong": 0, "run": run, "desc": desc}
        return (desc + f"\n확인: /확인 {code} ({int(self.opts['confirm_s'])}초 안 · 한 번만 · 취소는 /취소)"
                + ("\n(앞서 기다리던 확인은 버렸습니다)" if replaced else ""))

    def cmd_ok(self, args) -> str:
        p = self.pending
        if not p:
            return "[확인] 기다리는 명령이 없습니다"
        if self.clock() - p["t"] > float(self.opts["confirm_s"]):
            self.pending = None
            return f"[확인] {int(self.opts['confirm_s'])}초가 지나 버렸습니다 — 명령을 다시 보내세요"
        if not args or args[0] != p["code"]:
            p["wrong"] += 1
            if p["wrong"] >= MAX_WRONG:
                self.pending = None
                return f"[확인] 번호가 {MAX_WRONG}번 틀려 버렸습니다 — 명령을 다시 보내세요"
            return f"[확인] 번호가 틀렸습니다({p['wrong']}/{MAX_WRONG})"
        self.pending = None                       # 한 번 쓰면 버린다
        return p["run"]()

    def cmd_cancel(self, args) -> str:
        had, self.pending = self.pending is not None, None
        return "[취소] 기다리던 확인을 버렸습니다" if had else "[취소] 기다리는 명령이 없습니다"

    def _later(self, fn, fmt, wait_msg: str) -> str:
        """오래 걸리는 일(설정 적용 확인은 최대 봉 하나): 뒤에서 하고 끝나면 답을 보낸다. 시험은 run_async=False."""
        def job():
            try:
                return fmt(fn())
            except Exception as e:
                return f"[오류] 처리 중 {type(e).__name__}: {scrub(str(e))[:120]}"
        if not self.run_async:
            return job()

        def bg():
            msg = job()
            if self.reply:
                try:
                    self.reply(msg)
                    return
                except Exception:
                    pass
            with self._lock:
                self.outbox.append(msg)
        threading.Thread(target=bg, daemon=True).start()
        return wait_msg

    # ---------- 보기 ----------
    def _target_arg(self, args, default):
        if not args:
            return default, None
        t = TARGET_WORDS.get(args[0].lower())
        return (t, None) if t else (None, f"대상은 모의 · 시뮬 중 하나(받은 것: {args[0][:10]})")

    def _status_one(self, t: str) -> str:
        s = state.runner_status(self.root, t, pid_alive=self.pid_alive)
        held = ", ".join(f"{c} {p['qty']}주(평균 {p['avg']:,.0f})" for c, p in s["positions"].items()) or "없음"
        d = s["door"]
        door = "켜짐" if d["open"] else f"꺼짐({d['why']})"
        extra = ""
        if d["open"] or d["gates"] or d["owned"]:
            extra = (f"\n  문 {door} · 걸린 거르기 {len(d['gates'])} · 바깥이 산 것 "
                     + (", ".join(f"{c} {o['qty']}주" for c, o in d["owned"].items()) or "없음"))
        head = "돌고 있음" if s["alive"] else "꺼짐"
        if s["why"] and s["why"] != "실행기 " + head:           # '꺼짐 · 실행기 꺼짐'처럼 같은 말을 두 번 하지 않는다
            head += " · " + s["why"]
        # 장부의 진입 수 · 실현은 실행기가 마지막으로 돈 날의 값이다 — 그날이 오늘이 아니면 날짜를 밝힌다.
        day = str(s.get("book_day") or "")
        if day == datetime.now().strftime("%Y%m%d"):
            when = "오늘"
        elif len(day) == 8 and day.isdigit():
            when = f"마지막 운행일({day[4:6]}-{day[6:]})"
        else:
            when = "장부 기준"
        kind = "모의" if t in ("paper", "dry") else "시뮬레이션"
        return (f"[{s['label']}] 실행기 {head}\n"
                f"  정지 파일 {'있음' if s['stop_file'] else '없음'} · 진행 중 주문 {len(s['orders'])}건\n"
                f"  들고 있는 것(체결 기준) {held}\n"
                f"  {when} 진입 {s['entries_today']}회 · 실현 {_won(s['realized'])}({kind} · 수수료 전)" + extra)

    def cmd_status(self, args) -> str:
        t, err = self._target_arg(args, None)
        if err:
            return "[상태] " + err
        return "[상태]\n" + "\n".join(self._status_one(x) for x in ([t] if t else ["paper", "sim"]))

    def cmd_summary(self, args) -> str:
        lines = ["[요약]"]
        for t in ("paper", "sim"):
            s = state.today_summary(self.root, t)
            lab = runner_paths(self.root, t)["label"]
            if not s["exists"]:
                lines.append(f"[{lab}] 오늘 기록 없음")
            elif s["stop_summary"] is not None:
                lines.append(f"[{lab}] 끝({s['stop_reason']}) · " + telegram.summary_line(s["stop_summary"]))
            else:
                c = s["events"]
                lines.append(f"[{lab}] 신호 {c.get('signal', 0)} · 주문 {c.get('order_sent', 0)} · 체결 확인 {c.get('fill', 0)} · "
                             f"끝난 주문 {s['orders_done'] or '-'} · 건너뜀 {c.get('order_skip', 0)} · "
                             f"위험 막힘 {c.get('risk_block', 0)} · 거르기 {c.get('entry_filtered', 0)} · 오류 {c.get('error', 0)}")
        return "\n".join(lines)

    # ---------- 끄기 · 켜기 ----------
    def cmd_off(self, args) -> str:
        t, err = self._target_arg(args, None)
        if err:
            return "[끄기] " + err
        targets = [t] if t else ["paper"] + (["sim"] if state.alive(self.root, "sim", pid_alive=self.pid_alive)[0] else [])
        out = [switch.stop(self.root, x, actor="telegram", pid_alive=self.pid_alive)["message"] for x in targets]
        return "[끄기] " + "\n".join(out)

    def _until(self) -> str:
        v = settings_edit.view(self.root, self.config_path)
        u = (v.get("settings") or {}).get("until")
        return u if isinstance(u, str) and re.fullmatch(r"\d\d:\d\d", u) else "15:20"

    def cmd_on(self, args) -> str:
        t, err = self._target_arg(args, "paper")
        if err:
            return "[켜기] " + err
        until = self._until()
        chk = switch.check_start(self.root, t, now=self.market_now(), until=until, holidays=self.holidays,
                                 pid_alive=self.pid_alive)
        lab = runner_paths(self.root, t)["label"]
        notes = "".join(f" · {n}" for n in chk["notes"])
        if chk["status"] in ("running", "bad"):
            return "[켜기] " + chk["message"]
        if chk["status"] == "closed":
            if not chk["stop_file"]:
                return f"[켜기] {chk['message']} — 띄우지 않습니다{notes}"
            desc = f"[켜기 확인] {chk['message']} — 띄우지 않고 정지 파일만 지웁니다(다음 예약 때 시작){notes}"
        else:
            desc = (f"[켜기 확인] {lab} 실행기를 띄웁니다({'모의 주문' if t == 'paper' else '주문 안 보냄'}) · "
                    f"바깥 신호 문 {'켜짐' if chk['ext'] else '꺼짐'}" + (" · 정지 파일을 지웁니다" if chk["stop_file"] else "")
                    + notes)
        return self._ask(desc, lambda: self._do_on(t, until))

    def _do_on(self, t: str, until: str) -> str:
        r = switch.start(self.root, t, actor="telegram", now=self.market_now(), until=until, launcher=self.launcher,
                         holidays=self.holidays, pid_alive=self.pid_alive)
        if r["launched"]:
            self.watch.append({"target": t, "since": r["since_ms"], "until": self.clock() + 120})
        return "[켜기] " + r["message"]

    def poll_background(self) -> list[str]:
        """띄운 실행기의 시작 확인 + 뒤에서 끝난 일의 답. 루프가 돌 때마다 부른다."""
        out = []
        for w in list(self.watch):
            res = switch.start_result(self.root, w["target"], w["since"])
            if res:
                out.append("[켜기 확인됨] " + res["message"] if res["ok"] else "[켜기 실패] " + res["message"])
                self.watch.remove(w)
            elif self.clock() > w["until"]:
                out.append(f"[켜기 확인 안 됨] {runner_paths(self.root, w['target'])['label']}: 2분 안에 실행기 시작 기록이 없음 — "
                           "run_daily 기록(logs/run_daily_날짜.log)을 확인하세요")
                self.watch.remove(w)
        with self._lock:
            out += self.outbox
            self.outbox = []
        return out

    # ---------- 설정 ----------
    def cmd_settings(self, args) -> str:
        if not args:
            v = settings_edit.view(self.root, self.config_path)
            if not v["ok"]:
                return f"[설정] 읽지 못함 — {v['error']}"
            s = v["settings"]
            show = []
            for k, val in s.items():
                if k == "codes":
                    val = ",".join(val or [])
                elif k in ("amount", "max_amount", "loss_limit") and isinstance(val, (int, float)):
                    val = _won(val)
                show.append(f"{k} {val}")
            return ("[설정] " + " · ".join(show) + "\n바꾸기: /설정 칸 값 (바꿀 수 있는 칸: "
                    + ", ".join(settings_edit.reloadable()) + ")")
        key = args[0]
        if len(args) < 2:
            return "[설정] 값이 없습니다 — 예: /설정 amount 500000"
        try:
            val = settings_edit.parse_value(key, " ".join(args[1:]))
        except ValueError as e:
            return f"[설정] {e}"
        pv = settings_edit.preview({key: val}, actor="telegram", root=self.root, config_path=self.config_path,
                                   pid_alive=self.pid_alive)
        if not pv["ok"]:
            return f"[설정] 바꾸지 않음 — {pv['message']}"
        ch = " · ".join(f"{k}: {o} → {n}" for k, (o, n) in pv["diff"].items())
        desc = f"[설정 확인] {ch} (모의 · 시뮬레이션이 함께 읽는 설정 파일)"

        def fmt(r):
            return f"[설정] {r['message']}" + (f" · {ch}" if r["ok"] else "")
        return self._ask(desc, lambda: self._later(
            lambda: settings_edit.edit({key: val}, actor="telegram", root=self.root, config_path=self.config_path,
                                       pid_alive=self.pid_alive),
            fmt, "[설정] 바꾸는 중 — 실행기 적용을 기다립니다(봉 하나 안팎)"))

    # ---------- 사고팔기(문에 신호) ----------
    def _trade_ready(self, word: str, code: str) -> tuple[str | None, str]:
        if not self.opts["trade_enabled"]:
            return f"[{word}] 사고팔기 명령이 꺼져 있습니다(bot.yaml 의 trade_enabled: true 로 켬)", ""
        if not re.fullmatch(r"\d{6}", code or ""):
            return f"[{word}] 종목은 6자리 숫자", ""
        t = self.opts["trade_target"]
        d = state.door_state(self.root, t, pid_alive=self.pid_alive)
        lab = runner_paths(self.root, t)["label"]
        if not d["open"]:
            return f"[{word}] {lab} 문이 꺼져 있습니다({d['why']}) — 신호를 쓰지 않습니다", t
        if code not in state.runner_codes(self.root, t):
            return f"[{word}] {code} 는 {lab} 실행기 종목 목록 밖입니다(봉 · 현재가를 받지 않는 종목)", t
        return None, t

    def _sig_reply(self, word: str, r: dict) -> str:
        it = r["results"][0] if r["results"] else {"ok": False, "why": "대상 없음"}
        if it["ok"]:
            return f"[{word}] 문에 신호를 썼습니다 — 실행기가 다음 봉 마감 때 읽고 위험 점검 뒤 주문합니다(최대 1분)"
        return f"[{word}] 쓰지 않음 — {it['why']}"

    def cmd_buy(self, args) -> str:
        code = args[0] if args else ""
        err, t = self._trade_ready("사기", code)
        if err:
            return err
        try:
            amount = int(str(args[1]).replace(",", "")) if len(args) > 1 else 0
        except ValueError:
            amount = 0
        if amount <= 0:
            return "[사기] 금액(원)이 없습니다 — 예: /사기 <종목코드> 500000"
        hold, stop, exp = self.opts["buy_hold_min"], self.opts["buy_stop_pct"], self.opts["signal_expires_s"]
        lab = runner_paths(self.root, t)["label"]
        desc = (f"[사기 확인] {lab} · {code} · 금액 {amount:,}원(설정 금액을 넘으면 설정 금액까지) · 보유 {hold}분 뒤 청산"
                + (f" · 손절 {stop}%" if stop else " · 손절 없음") + f" · 신호 유효 {exp}초 · 다음 봉 마감 때 위험 점검 · 거르기 뒤 주문")
        return self._ask(desc, lambda: self._sig_reply("사기", ext_write.write_signal(
            self.root, t, source="telegram", code=code, action="entry", amount=amount, max_hold_min=hold,
            stop_pct=stop, expires_s=exp, note="텔레그램 /사기", actor="telegram", pid_alive=self.pid_alive)))

    def cmd_sell(self, args) -> str:
        code = args[0] if args else ""
        err, t = self._trade_ready("팔기", code)
        if err:
            return err
        lab = runner_paths(self.root, t)["label"]
        q = (state.book(self.root, t)["positions"].get(code) or {}).get("qty", 0)
        desc = (f"[팔기 확인] {lab} · {code} · 이 실행기가 들고 있는 {q}주 전부(체결 기준) · "
                f"신호 유효 {self.opts['signal_expires_s']}초 · 다음 봉 마감 때 주문")
        return self._ask(desc, lambda: self._sig_reply("팔기", ext_write.write_signal(
            self.root, t, source="telegram", code=code, action="exit", expires_s=self.opts["signal_expires_s"],
            note="텔레그램 /팔기", actor="telegram", pid_alive=self.pid_alive)))

    # ---------- 검토 끄기 ----------
    def cmd_review_off(self, args) -> str:
        """검토 표시(review.json)를 걷는다 — 확인 번호 없음(푸는 쪽 = 전략 진입을 예전처럼 바로 낸다)."""
        t, err = self._target_arg(args, None)
        if err:
            return "[검토끄기] " + err
        targets = [t] if t else ["paper", "sim"]
        r = ext_write.clear_review(self.root, targets, actor="telegram")
        out = []
        for it in r["results"]:
            lab = runner_paths(self.root, it["target"])["label"]
            if not it["ok"]:
                out.append(f"{lab}: 걷지 못함({it['why']})")
            elif it.get("file"):
                out.append(f"{lab}: 검토 표시를 걷었습니다 — 다음 전략 진입부터 묻지 않고 냅니다")
            else:
                out.append(f"{lab}: 검토 표시 없음")
        return "[검토끄기] " + "\n".join(out)

    # ---------- 브라우저 읽기(자리만) ----------
    def cmd_read(self, args) -> str:
        if not args or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args[0]):
            return "[읽기] 사이트 id 가 필요합니다 — 예: /읽기 sample"
        if importlib.util.find_spec("browser_reader") is None:
            return "[읽기] 브라우저 모듈 없음 — 이 명령은 자리만 있습니다"
        return "[읽기] 브라우저 모듈 연결은 아직 없습니다(자리만 있음)"


class BotLog:
    def __init__(self, log_dir: Path = ROOT / "logs"):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.dir = log_dir

    def write(self, event: str, **kw):
        rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "event": event,
               **{k: (scrub(v) if isinstance(v, str) else v) for k, v in kw.items()}}
        try:
            with (self.dir / f"bot_{datetime.now():%Y%m%d}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass


class LogFollower:
    """실행기 기록(jsonl)을 새 줄만 따라 읽는다 — 시뮬레이션 알림용. 처음엔 파일 끝에서 시작(옛 사건은 보내지 않음).
    마지막 줄바꿈까지만 읽어 반쯤 쓴 줄은 다음 번으로 넘긴다. 날이 바뀌어 파일이 바뀌면 처음부터."""

    def __init__(self, path_fn):
        self.path_fn = path_fn
        self.path = path_fn()
        try:
            self.pos = self.path.stat().st_size
        except OSError:
            self.pos = 0

    def new(self) -> list[dict]:
        p = self.path_fn()
        if p != self.path:
            self.path, self.pos = p, 0
        try:
            size = p.stat().st_size
        except OSError:
            return []
        if size < self.pos:
            self.pos = 0
        if size == self.pos:
            return []
        with p.open("rb") as f:
            f.seek(self.pos)
            chunk = f.read(size - self.pos)
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return []
        self.pos += cut + 1
        out = []
        for line in chunk[:cut + 1].decode("utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                out.append(r)
        return out


class BotLoop:
    """받기 → 명령 처리 → 보내기 한 바퀴(tick). 어디서 예외가 나도 기록하고 다음 바퀴로 간다."""

    def __init__(self, tg, bot: Bot, log: BotLog, sleep=time.sleep, started: int | None = None,
                 sim_notifier=None, follower: LogFollower | None = None):
        self.tg, self.bot, self.log, self.sleep = tg, bot, log, sleep
        self.started = int(time.time()) if started is None else started
        self.offset = None
        self.sim_notifier = sim_notifier
        self.follower = follower
        bot.reply = self.send

    def send(self, text: str) -> None:
        try:
            self.tg.send(text)
        except Exception as e:
            self.log.write("send_error", error=telegram.redact(str(e))[:200])

    def drop_old(self) -> int:
        """켜기 전에 쌓인 업데이트는 건너뛴다(텔레그램은 못 받은 업데이트를 최대 하루 보관)."""
        try:
            old = self.tg.updates(None, wait_s=0)
        except Exception as e:
            self.log.write("poll_error", error=telegram.redact(str(e))[:200])
            return 0
        if old:
            self.offset = old[-1]["update_id"] + 1
            self.log.write("dropped_old", n=len(old))
        return len(old or [])

    def tick(self, wait_s: int = 30) -> None:
        try:
            ups = self.tg.updates(self.offset, wait_s=wait_s)
        except Exception as e:
            self.log.write("poll_error", error=telegram.redact(str(e))[:200])
            self.sleep(5)
            ups = []
        for u in ups or []:
            try:
                self.offset = int(u["update_id"]) + 1
                ok, why = self.tg.is_owner_message(u, self.started)
                if not ok:
                    self.log.write("ignored", reason=why)
                    continue
                text = ((u.get("message") or {}).get("text") or "")[:200]
                try:
                    reply = self.bot.handle(text)
                except Exception as e:
                    self.log.write("handle_error", text=text[:50], error=f"{type(e).__name__}: {str(e)[:150]}")
                    reply = "[오류] 명령을 처리하다 오류가 났습니다 — 봇은 계속 돕니다(기록 확인)"
                self.log.write("command", text=text[:50], reply=reply[:200])
                self.send(reply)
            except Exception as e:
                self.log.write("loop_error", error=f"{type(e).__name__}: {str(e)[:150]}")
        try:
            for msg in self.bot.poll_background():
                self.send(msg)
        except Exception as e:
            self.log.write("loop_error", where="background", error=f"{type(e).__name__}: {str(e)[:150]}")
        if self.follower and self.sim_notifier:
            try:
                for rec in self.follower.new():
                    self.sim_notifier(rec)
            except Exception as e:
                self.log.write("loop_error", where="sim_follow", error=f"{type(e).__name__}: {str(e)[:150]}")


def main() -> int:
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    cfg = telegram.load_config()
    if not cfg:
        print(telegram.status(), "— 봇을 시작하지 않습니다")
        return 2
    opts, warns = load_opts()
    tg = telegram.Telegram(cfg["token"], cfg["owner_id"])
    log = BotLog()
    for w in warns:
        log.write("opts_warning", note=w)
    bot = Bot(ROOT, opts)
    sim_n = follower = None
    if opts["sim_notify"]:
        from control.common import log_path
        sim_n = telegram.Notifier(tg, prefix="[시뮬] ", on_error=lambda m: log.write("notify_error", error=m))
        follower = LogFollower(lambda: log_path(ROOT, "sim"))
    loop = BotLoop(tg, bot, log, sim_notifier=sim_n, follower=follower)
    loop.drop_old()
    loop.send("[봇 시작] " + HELP + ("" if opts["trade_enabled"] else " · 사고팔기 명령 꺼짐"))
    log.write("start", trade_enabled=opts["trade_enabled"], sim_notify=opts["sim_notify"])
    print("봇 시작 — Ctrl+C 로 끝")
    while True:
        try:
            loop.tick()
        except KeyboardInterrupt:
            break
        except Exception as e:                   # 마지막 그물 — 무엇이 새어 나와도 기록하고 계속
            log.write("loop_error", where="main", error=f"{type(e).__name__}: {str(e)[:150]}")
            time.sleep(5)
    log.write("stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
