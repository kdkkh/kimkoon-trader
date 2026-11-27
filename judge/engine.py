"""판단 프로세스 본체 — 방아쇠마다 판단 하나를 묻고(ask) 내보내고 기록한다. scripts/judge_run.py 가 띄운다.

한 번 묻기(ask) 순서
  1. 전역 상한(하루 호출 수 · 하루 달러) · 판단별 하루 호출 상한 → 넘으면 묻지 않음(전역 상한은 그날 멈춤 + 알림 한 번)
  2. 공급자가 anthropic 이면 judge.yaml ai_enabled · ack 확인(ack 전에는 고지만 출력하고 부르지 않음)
  3. 입력 모으기(스위치가 꺼진 입력 · 자료 없음 → 묻지 않고 이유 기록)
  4. 프롬프트 → 공급자 → 검증(어긋나면 버림, 재시도는 정의의 retries 만큼만)
  5. 쓰임(신호 · 거르기 · 설정 · 알림 · 검토 답) — dry 면 내보내지 않음, 가짜 공급자면 control 에 fake=True
  6. 기록(logs/judge_YYYYMMDD.jsonl) · 상태 파일(state/judge_status.json — 화면의 'AI 판단' 탭이 읽음)
실행기가 돌지 않으면(문을 켠 실행기가 없으면) 방아쇠를 돌리지 않는다 — 켜 둔 판단이 쓰는 만큼만, 실행기가 도는 동안만 조회한다.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime
from pathlib import Path

from control import state
from control.common import runner_paths, scrub

from . import check, inputs, limits, outputs, prompt, record
from .providers import KeyMissing, ProviderError, Providers, config_dir
from .schedule import NewsWatcher, Schedule, SignalWatcher, key_of, review_requests

NOTICE = """[AI 판단 기능 — 처음 켤 때 읽을 것]
AI 판단 기능을 켜면, 고른 자료가 사용자 본인의 Anthropic API 계정으로 전송됩니다. 이 프로그램과 제작자는 자료를 받거나 보관하지 않습니다.
자료마다 제공자의 이용 조건이 있습니다(한국투자증권 오픈 API 서비스 이용약관 제5조③: 시세는 개인의 업무에 한하여 이용, 제3자 제공 금지 /
OpenDART: 출처 표시 / 웹사이트 자료: 각 사이트 고지). 보낼 자료를 직접 확인한 뒤 자신의 책임하에 켜십시오.
Anthropic 상업 약관에 따라 입력할 권리는 보내는 사람이 보증합니다.
Anthropic 상업 약관상 API 입력은 모델 학습에 쓰이지 않습니다. AI 결과의 사실 주장은 직접 확인하지 않고 믿지 마십시오.
모의투자로만 검증한 기능입니다.
지금 AI 로 보내도록 켜진 자료: {on}
읽었으면 judge.yaml 에 ack: true 를 적으십시오. 그 전에는 AI 를 부르지 않습니다."""
SKIP_REPEAT_MS = 30 * 60_000          # 같은 판단 · 같은 이유의 '묻지 않음' 기록은 30분에 한 번


def default_notifier():
    """notify.telegram 의 보내기. 봇 토큰(telegram.yaml)이 없으면 False(조용히 건너뜀). 처음 보낼 때 설정을 읽는다."""
    holder = {}

    def send(text: str):
        if "tg" not in holder:
            from notify import telegram
            cfg = telegram.load_config(config_dir() / "telegram.yaml")
            holder["tg"] = telegram.Telegram(cfg["token"], cfg["owner_id"]) if cfg else None
        if holder["tg"] is None:
            return False
        r = holder["tg"].send(text)
        return bool(r.get("ok", True)) if isinstance(r, dict) else True
    return send


class Engine:
    def __init__(self, root: Path, defs: list[dict], g: dict, *, fake: bool = False, dry: bool = False,
                 sources=None, providers=None, notify="default", clock=None, sleep=time.sleep, run_async: bool = True,
                 codes: list[str] | None = None, say=print, pid_alive=None, cfg_dir: Path | None = None,
                 def_errors: list | None = None, always: bool = False, dry_runner: bool = False):
        """dry_runner=True 면 돌고 있는 dry-run 실행기도 기본 대상('running')에 넣는다(시험용 — 기본은 모의 · 시뮬레이션만)."""
        self.root = Path(root)
        self.dry_runner = dry_runner
        self.review_marked: set = set()          # 검토 표시(review.json)를 쓴 (대상, ref) — 끝날 때 걷는다
        self.defs, self.g = defs, g
        self.fake_mode, self.dry = fake, dry
        self.sources = sources or (inputs.FakeSources() if fake else inputs.Sources(self.root, g["opendart"]["daily_cap"]))
        self.providers = providers or Providers(cfg_dir, force_fake=fake)
        self.notify = (None if fake else default_notifier()) if notify == "default" else notify
        self.clock = clock or record.now_ms
        self.sleep, self.run_async, self.say = sleep, run_async, say
        self.codes_override = list(codes) if codes else None
        self.pid_alive, self.def_errors, self.always = pid_alive, def_errors or [], always
        self.sched = Schedule()
        today = record.read_day(self.root, limits.today(self.clock()))
        self.sched.restore(today)
        self._use_day, self._use = limits.today(self.clock()), limits.usage_of(today)
        self.news_watch, self.sig_watch = NewsWatcher(), SignalWatcher(self.clock())
        self.seen_disc: dict[str, set] = {}
        for r in today:                                             # 오늘 이미 물어본 공시(only_new)
            for s in r.get("inputs") or [] if r.get("event") == "judge" else []:
                if isinstance(s, dict) and s.get("kind") == "disclosures":
                    self.seen_disc.setdefault(r.get("judge"), set()).update(
                        str(i.get("rcept_no")) for i in s.get("items") or [] if isinstance(i, dict))
        self.answered: set = set()
        self.review_beat: dict = {}
        self._beat_warned: set = set()
        self._news_at = 0
        self._notice_said = False
        self._skip_said: dict = {}
        self._skip_hidden: dict = {}
        self._open: list[str] | None = None
        self.started_ms = self.clock()

    # ---------------- 대상 · 종목 ----------------
    def open_targets(self) -> list[str]:
        out = []
        for t in state.running_targets(self.root, pid_alive=self.pid_alive, include_dry=self.dry_runner):
            try:
                if state.door_state(self.root, t, pid_alive=self.pid_alive)["open"]:
                    out.append(t)
            except Exception:
                continue
        return out

    def targets_for(self, d: dict) -> list[str]:
        t = d["target"]
        if t == "running":
            return list(self._open) if self._open is not None else self.open_targets()
        vals = t if isinstance(t, list) else [t]
        out = []
        for v in vals:
            for x in (["paper", "sim"] if v == "both" else [v]):
                if x not in out:
                    out.append(x)
        return out

    def codes(self, targets: list[str] | None = None) -> list[str]:
        if self.codes_override:
            return list(self.codes_override)
        out = []
        for t in targets if targets is not None else (self._open if self._open is not None else self.open_targets()):
            for c in state.runner_codes(self.root, t):
                if c not in out:
                    out.append(c)
        if not out:
            try:
                from control import settings_edit
                v = settings_edit.view(self.root)
                out = [str(c) for c in (v["settings"].get("codes") or [])] if v["ok"] else []
            except Exception:
                out = []
        return out

    def _bars_dirs(self, targets: list[str]) -> list[Path]:
        ts = list(targets) + [t for t in ("paper", "sim", "dry") if t not in targets]
        out = []
        for t in ts:
            try:
                out.append(runner_paths(self.root, t)["ext"] / "bars")
            except ValueError:
                continue
        return out

    # ---------------- 사용량 · 상한 ----------------
    def usage(self) -> dict:
        day = limits.today(self.clock())
        if day != self._use_day:
            self._use_day, self._use = day, limits.usage_of(record.read_day(self.root, day))
        return self._use

    def _count(self, rec: dict) -> None:
        u = self.usage()
        u["calls"] += int(rec.get("calls") or 0)
        u["per_judge"][rec["judge"]] = u["per_judge"].get(rec["judge"], 0) + int(rec.get("calls") or 0)
        if isinstance(rec.get("cost_est"), (int, float)):
            u["usd"] = round(u["usd"] + float(rec["cost_est"]), 6)

    def _limit(self, why: str) -> None:
        u = self.usage()
        if u["limit_hit"]:
            return
        u["limit_hit"], u["limit_reason"] = True, why
        sent = self._send(f"[{outputs.AI_MARK}] 오늘 판단을 멈춤 — {why}. 내일 다시 돈다.")
        record.append(self.root, "limit", ts_ms=self.clock(), reason=why, calls=u["calls"], usd=u["usd"], notify=sent)
        self.say(f"  [상한] {why} — 오늘 판단 멈춤")

    def _send(self, text: str) -> str:
        if self.dry:
            return "dry"
        if self.fake_mode:
            return "skipped(--fake)"
        if self.notify is None:
            return "skipped(보내기 함수 없음)"
        try:
            return "sent" if self.notify(text) is not False else "skipped(봇 토큰 없음)"
        except Exception as e:
            return f"error {type(e).__name__}"

    def _notice(self) -> None:
        if self._notice_said:
            return
        self._notice_said = True
        on = [k for k, v in self.g["send"].items() if v] or ["없음"]
        self.say(NOTICE.format(on=", ".join(on)))
        record.append(self.root, "notice", ts_ms=self.clock(), reason="judge.yaml ack 없음 — 고지만 출력, 부르지 않음")

    def _skip(self, d: dict, trig: dict, reason: str) -> dict:
        now = self.clock()
        k = (d["id"], reason)
        rec = {"event": "skip", "judge": d["id"], "reason": reason, "trigger": trig, "asked": False}
        if trig.get("kind") in ("once", "ask") or now - self._skip_said.get(k, -SKIP_REPEAT_MS) >= SKIP_REPEAT_MS:
            self._skip_said[k] = now
            # hidden = 앞 기록 뒤로 같은 이유라 적지 않은 횟수(종목마다 묻는 판단은 이 한 줄에 첫 종목만 실린다)
            rec = record.append(self.root, "skip", ts_ms=now, judge=d["id"], def_hash=d["_hash"], trigger=trig,
                                reason=reason, asked=False, dry=self.dry, fake=self.providers.name_for(d) == "fake",
                                hidden=self._skip_hidden.pop(k, 0))
        else:
            self._skip_hidden[k] = self._skip_hidden.get(k, 0) + 1
        return rec

    # ---------------- 한 번 묻기 ----------------
    def ask(self, d: dict, trig: dict) -> dict:
        t0 = self.clock()
        tw = time.time()
        prov_name = self.providers.name_for(d)
        fake = prov_name == "fake"
        u = self.usage()
        why = limits.over(u, self.g)
        if u["limit_hit"] or why:
            if why:
                self._limit(why)
            return self._skip(d, trig, f"전역 상한: {u['limit_reason'] or why}")
        if u["per_judge"].get(d["id"], 0) >= d["limits"]["daily_calls"]:
            return self._skip(d, trig, f"이 판단의 하루 호출 상한({d['limits']['daily_calls']}번)에 닿음")
        if prov_name == "anthropic":
            if not self.g["ai_enabled"]:
                return self._skip(d, trig, "AI 기능 꺼짐(judge.yaml ai_enabled: false)")
            if not self.g["ack"]:
                self._notice()
                return self._skip(d, trig, "고지 확인 전(judge.yaml ack: true 가 없음) — 부르지 않음")
        targets = self.targets_for(d)
        codes = self.codes(targets if d["target"] != "running" else None)
        ctx = {"root": self.root, "targets": targets, "codes": codes, "trigger": trig, "send": self.g["send"],
               "record_titles": self.g["record_titles"], "sources": self.sources, "bars_dirs": self._bars_dirs(targets),
               "seen_disclosures": self.seen_disc.get(d["id"], set()), "pid_alive": self.pid_alive,
               "browser_out": (self.root / self.g["browser_out"]) if self.g.get("browser_out") else None}
        got = inputs.gather(d, ctx)
        if got["skip"]:
            return self._skip(d, trig, got["skip"])
        p = prompt.build(d["question"], d["answer"], got["parts"])
        try:
            prov = self.providers.get(d)
        except KeyMissing as e:
            return self._skip(d, trig, f"키 없음: {e}")
        except ProviderError as e:
            return self._skip(d, trig, f"공급자를 쓸 수 없음: {e}")
        attempts, calls, resp, ans, bad = [], 0, None, None, ""
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        for a in range(int(d["model"]["retries"]) + 1):
            if a > 0 and limits.over({**u, "calls": u["calls"] + calls}, self.g):
                bad += " · 다시 묻기 전에 전역 상한"
                break
            calls += 1
            try:
                resp = prov.call(d, p["system"], p["user"], p["schema"])
            except ProviderError as e:
                attempts.append({"error": e.kind, "message": scrub(str(e))[:160]})
                bad = f"호출 실패: {scrub(str(e))[:160]}"
                if not e.retryable:
                    break
                self.sleep(min(2 ** a, 8))
                continue
            for k in usage:
                usage[k] += int((resp.get("usage") or {}).get(k) or 0)
            ans, bad = check.validate(d["answer"], resp)
            attempts.append({"stop_reason": resp.get("stop_reason"), "valid": ans is not None, "why": bad})
            if ans:
                break
        served = (resp or {}).get("model") or d["model"]["name"]
        if fake:
            cost, cost_note = 0.0, "가짜 공급자 — 비용 없음"
        else:
            cost, cost_note = limits.cost(served, usage, self.g["prices"])
        if d["id"] not in self.seen_disc:
            self.seen_disc[d["id"]] = set()
        self.seen_disc[d["id"]].update(str(x) for x in got["disclosures_new"])
        ref = f"{d['id'][:20]}-{t0}-{secrets.token_hex(2)}"
        octx = {"root": self.root, "targets": targets, "fake": fake, "dry": self.dry, "trigger": trig,
                "notify": self.notify, "no_outside": self.fake_mode, "run_async": self.run_async,
                "pid_alive": self.pid_alive, "now_ms": self.clock()}
        outs = []
        if ans:
            try:
                outs = outputs.emit(d, ans, octx, ref)
            except Exception as e:
                outs = [{"kind": "error", "status": "error", "why": f"{type(e).__name__}: {scrub(str(e))[:120]}"}]
        if trig.get("kind") == "review":
            outs += outputs.emit_review(d, ans, octx, ref)
        titles = [] if self.g["record_titles"] else got["titles"]
        raw = record.mask_titles((resp or {}).get("text", ""), titles)
        if ans and titles:
            ans = dict(ans, reason=record.mask_titles(ans.get("reason", ""), titles))
        rec = record.append(
            self.root, "judge", ts_ms=t0, ref=ref, judge=d["id"], def_hash=d["_hash"], trigger=trig,
            inputs=got["summary"], notes=got["notes"], prompt_sha=record.prompt_sha(p["system"], p["user"]),
            system=p["system"], user=p["user"] if self.g["record_titles"] else p["user_masked"], schema=p["schema"],
            model={"provider": prov_name, "name": d["model"]["name"], "served": served, "effort": d["model"]["effort"],
                   "max_tokens": d["model"]["max_tokens"], "fallback": d["model"]["fallback"]},
            usage=usage, cost_est=cost, cost_note=cost_note, raw=raw, answer=ans, valid=ans is not None,
            invalid_reason="" if ans else bad, outputs=outs, sec=round(time.time() - tw, 3), provider=prov_name,
            calls=calls, attempts=attempts, dry=self.dry, fake=fake, targets=targets)
        self._count(rec)
        self.sched.mark(trig.get("key") or key_of(d, -1), t0)
        why = limits.over(self.usage(), self.g)
        if why:
            self._limit(why)
        self.say(f"  [판단] {d['id']} {trig.get('kind')}{(' ' + ','.join(trig.get('codes') or [])) if trig.get('codes') else ''}"
                 f" → {check.show(ans) if ans else '버림: ' + bad} · 쓰임 {[(o['kind'], o.get('status')) for o in outs]}")
        return rec

    # ---------------- 방아쇠 ----------------
    def _enabled(self) -> list[dict]:
        return [d for d in self.defs if d["enabled"]]

    def once(self, ids: list[str], codes: list[str] | None = None, text: str | None = None) -> list[dict]:
        """손으로 한 번(--once). per_code 판단은 종목마다. 꺼진(enabled: false) 정의도 손으로는 물을 수 있다."""
        by = {d["id"]: d for d in self.defs}
        out = []
        for i in ids:
            d = by.get(i)
            if d is None:
                self.say(f"  판단 정의 없음: {i}")
                continue
            cs = list(codes or [])
            if d["per_code"]:
                for c in cs or self.codes(self.targets_for(d) if d["target"] != "running" else None):
                    out.append(self.ask(d, {"kind": "once", "key": f"{i}#once#{c}", "codes": [c], "text": text}))
            else:
                out.append(self.ask(d, {"kind": "once", "key": f"{i}#once", "codes": cs, "text": text}))
        self.write_status()
        return out

    def _news_ok(self, d: dict) -> bool:
        return bool(self.g["send"].get("news_titles"))

    def tick(self) -> list[dict]:
        now = self.clock()
        self._open = self.open_targets()
        recs: list[dict] = []
        try:
            if not self._open and not self.always:
                return recs
            codes = self.codes()
            for d in self._enabled():
                for trig in self.sched.clock_due(d, now):
                    if d["per_code"]:
                        for c in codes:                     # 열쇠는 그대로(다시 띄워도 at 이 하루 한 번이게)
                            recs.append(self.ask(d, {**trig, "codes": [c]}))
                    else:
                        recs.append(self.ask(d, {**trig, "codes": []}))
            recs += self._tick_news(now, codes)
            recs += self._tick_signals(now)
            recs += self._tick_review(now)
            recs += self._tick_asks()
        finally:
            self.write_status()
            self._open = None
        return recs

    def _tick_news(self, now: int, codes: list[str]) -> list[dict]:
        nd = [(d, i, w) for d in self._enabled() for i, w in enumerate(d["when"]) if w["_kind"] == "new_news"]
        if not nd or now - self._news_at < self.g["news"]["poll_s"] * 1000:
            return []
        self._news_at = now
        recs = []
        if not self.g["send"].get("news_titles"):
            for d, i, w in nd:                                  # 뉴스를 보낼 수 없으면 새 제목을 볼 까닭도 없다 — 조회하지 않음
                recs.append(self._skip(d, {"kind": "new_news", "key": key_of(d, i)},
                                       "news_titles: AI 로 보내기 스위치 꺼짐(judge.yaml send.news_titles)"))
            return recs
        try:
            new = self.news_watch.poll(self.sources, codes, self.g["news"]["limit"])
        except inputs.InputError as e:
            record.append(self.root, "error", ts_ms=now, where="new_news", error=str(e))
            return recs
        for code, rows in new.items():
            for d, i, w in nd:
                k = key_of(d, i, code)
                if self.sched.gap_ok(k, now, w["min_gap_s"]):
                    self.sched.mark(k, now)
                    recs.append(self.ask(d, {"kind": "new_news", "key": k, "codes": [code],
                                             "detail": f"새 제목 {len(rows)}건"}))
        return recs

    def _tick_signals(self, now: int) -> list[dict]:
        sd = [(d, i, w) for d in self._enabled() for i, w in enumerate(d["when"]) if w["_kind"] == "signal"]
        if not sd:
            return []
        recs = []
        for e in self.sig_watch.poll(self.root, self._open or []):
            for d, i, w in sd:
                if e.get("kind") not in w["kinds"]:
                    continue
                k = key_of(d, i, e.get("code"))
                if self.sched.gap_ok(k, now, w["min_gap_s"]):
                    self.sched.mark(k, now)
                    recs.append(self.ask(d, {"kind": "signal", "key": k, "codes": [str(e.get("code"))],
                                             "detail": f"{e.get('kind')} {e.get('bar_time', '')}"[:60],
                                             "target": e.get("_target")}))
        return recs

    def _tick_review(self, now: int) -> list[dict]:
        recs = []
        for d in self._enabled():
            w = next((x for x in d["when"] if x["_kind"] == "review"), None)
            if w is None:
                continue
            stopped = self.usage()["limit_hit"]
            for t in self.targets_for(d):
                if not self.dry and not stopped and now - self.review_beat.get(t, 0) >= w["alive_s"] * 1000 / 3:
                    from control import ext_write
                    r = ext_write.write_review(self.root, t, expires_s=w["alive_s"], on_timeout=w["on_timeout"],
                                               ref=f"judge:{d['id']}", fake=self.providers.name_for(d) == "fake",
                                               actor=f"judge:{d['id']}", pid_alive=self.pid_alive)
                    self.review_beat[t] = now
                    if r["ok"]:
                        self.review_marked.add((t, f"judge:{d['id']}"))
                    if not r["ok"] and t not in self._beat_warned:
                        self._beat_warned.add(t)
                        self.say(f"  [검토] {t}: 살아 있음 표시를 쓰지 못함 — {r['results'][0]['why'] if r['results'] else ''}")
                for req in review_requests(self.root, t):
                    rid = f"{t}:{req['id']}"
                    if rid in self.answered:
                        continue
                    self.answered.add(rid)
                    trig = {"kind": "review", "key": key_of(d, 0, str(req.get("code"))), "codes": [str(req.get("code"))],
                            "request": {k: req.get(k) for k in ("id", "code", "t_signal_ms", "deadline_ms", "bar_time")},
                            "target": t}
                    if req.get("deadline_ms") and now > int(req["deadline_ms"]):
                        recs.append(self._skip(d, trig, "검토 마감 지남 — 실행기가 on_timeout 대로"))
                        continue
                    recs.append(self.ask(d, trig))
        return recs

    def _tick_asks(self) -> list[dict]:
        """봇 · 화면이 넘긴 '한 번 묻기'(state/judge_ask/*.json = {judge, text, codes}) — 읽으면 judge_ask_done/ 으로 옮긴다."""
        import json
        import os
        d_in, d_done = self.root / "state" / "judge_ask", self.root / "state" / "judge_ask_done"
        if not d_in.is_dir():
            return []
        by, recs = {d["id"]: d for d in self.defs}, []
        for f in sorted(d_in.glob("*.json")):
            try:
                req = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                req = None
            try:
                d_done.mkdir(parents=True, exist_ok=True)
                os.replace(f, d_done / f.name)
            except OSError:
                continue
            if not isinstance(req, dict) or req.get("judge") not in by:
                continue
            codes = [str(c) for c in req.get("codes") or [] if isinstance(c, str)][:20]
            recs.append(self.ask(by[req["judge"]], {"kind": "ask", "key": f"{req['judge']}#ask", "codes": codes,
                                                    "text": str(req.get("text") or "")[:4000],
                                                    "actor": str(req.get("actor") or "")[:20]}))
        return recs

    # ---------------- 상태 파일 ----------------
    def write_status(self) -> dict:
        from strategy.ext import atomic_write
        u = self.usage()
        last: dict = {}
        for r in record.read_day(self.root, self._use_day):
            if r.get("event") == "judge":
                last[r.get("judge")] = {"ts_ms": r.get("ts_ms"), "answer": r.get("answer"), "valid": r.get("valid"),
                                        "invalid_reason": r.get("invalid_reason"), "cost_est": r.get("cost_est"),
                                        "outputs": [(o.get("kind"), o.get("status")) for o in r.get("outputs") or []]}
        st = {"updated_ms": self.clock(), "started_ms": self.started_ms, "dry": self.dry, "fake": self.fake_mode,
              "open_targets": list(self._open or []), "switches": dict(self.g["send"]),
              "ai_enabled": self.g["ai_enabled"], "ack": self.g["ack"], "record_titles": self.g["record_titles"],
              "limits": {"daily_calls": self.g["limits"]["daily_calls"], "daily_usd": self.g["limits"]["daily_usd"],
                         "calls_today": u["calls"], "usd_today": u["usd"], "stopped": u["limit_hit"],
                         "stopped_reason": u["limit_reason"]},
              "prices_checked": self.g["prices"].get("checked"),
              "judges": [{"id": d["id"], "file": d["_file"], "enabled": d["enabled"], "def_hash": d["_hash"],
                          "provider": self.providers.name_for(d), "model": d["model"]["name"],
                          "when": [w["_kind"] for w in d["when"]], "uses": [x["kind"] for x in d["use"]],
                          "calls_today": u["per_judge"].get(d["id"], 0), "daily_calls": d["limits"]["daily_calls"],
                          "last": last.get(d["id"])} for d in self.defs],
              "def_errors": [{"file": f, "error": e} for f, e in self.def_errors]}
        try:
            atomic_write(self.root / "state" / "judge_status.json", st)
        except OSError:
            pass
        return st

    def run(self, until: str | None = None, poll_s: float = 5.0) -> str:
        """멈추는 때: state/judge.stop 파일 · until 시각(HH:MM) · Ctrl+C. 거르기는 만료로 저절로 풀리고,
        검토 표시는 정상으로 끝날 때 걷는다(release_reviews)."""
        stop = self.root / "state" / "judge.stop"
        record.append(self.root, "start", ts_ms=self.clock(), judges=[d["id"] for d in self._enabled()], dry=self.dry,
                      fake=self.fake_mode, def_errors=[f for f, _ in self.def_errors])
        reason = "?"
        try:
            while True:
                if stop.exists():
                    reason = "stop file"
                    break
                if until and datetime.fromtimestamp(self.clock() / 1000).strftime("%H:%M") >= until:
                    reason = f"until {until}"
                    break
                try:
                    self.tick()
                except Exception as e:                              # 한 바퀴 오류가 프로세스를 멈추지 않게
                    record.append(self.root, "error", ts_ms=self.clock(), where="tick",
                                  error=f"{type(e).__name__}: {scrub(str(e))[:200]}")
                self.sleep(poll_s)
        except KeyboardInterrupt:
            reason = "ctrl-c"
        cleared = self.release_reviews()
        record.append(self.root, "stop", ts_ms=self.clock(), reason=reason, calls=self.usage()["calls"],
                      usd=self.usage()["usd"], review_cleared=cleared)
        return reason

    def release_reviews(self) -> list[str]:
        """정상으로 끝날 때: 이 프로세스가 쓴 검토 표시만 걷는다(ref 가 같은 것만). 걷으면 실행기는 다음 전략 진입부터
        묻지 않고 예전처럼 바로 낸다. 비정상으로 죽으면 여기까지 오지 못해 표시가 만료된 채 남고,
        실행기는 on_timeout 대로 한다(기본 = 진입하지 않음) — 그때는 봇 /검토끄기 · 화면으로 걷는다."""
        from control import ext_write
        out = []
        for t, ref in sorted(self.review_marked):
            r = ext_write.clear_review(self.root, t, ref=ref, actor=ref)
            if r["results"] and r["results"][0].get("file"):
                out.append(t)
        self.review_marked.clear()
        return out
