"""쓰임 넷(+ 검토 답) — 판단의 답을 정해 둔 규칙대로 내보낸다. AI 는 주문하지 않는다.

  signal    진입/청산 신호 → control.ext_write.write_signal(실행기 문 inbox). 방아쇠 종목으로만. 실행기가 위험 점검 뒤 주문.
  gate      '새 진입 막음' + 만료 → control.ext_write.write_gate. 진입만 막고 청산은 막지 않는다. 판단이 멈추면 만료로 풀린다.
  settings  control.settings_edit.edit(actor="judge:<id>") — amount · max_orders 를 내리는 쪽만(올리는 변경은 여기서도 거부).
  notify    알림 글(텔레그램 보내기 함수는 주입식 — 기본은 notify.telegram, 봇 토큰이 없으면 조용히 건너뜀). 'AI 판단' 표시를 붙인다.
  review    실행기 검토 요청에 답 → control.ext_write.answer_review(if 조건이 맞으면 통과, 아니면 거절).
dry = 내보내지 않고 기록만(무엇을 내보냈을지는 obj 에 남긴다 — 다시 틀기에 쓸 수 있게).
fake = 가짜 공급자의 답: control 함수에 fake=True 를 넘긴다(한투 모의 문 · 설정 파일에는 쓰지 않고 기록만 — control 이 그렇게 한다).
"""
from __future__ import annotations

import threading

from . import record
from .check import matches, show

AI_MARK = "AI 판단"


def _targets_arg(targets):
    return list(targets) if targets else []


def notice_text(d: dict, ans: dict, fake: bool, trigger_codes: list[str]) -> str:
    who = f" · {', '.join(trigger_codes)}" if trigger_codes else ""
    tail = " (가짜 응답 — 시험)" if fake else ""
    return f"[{AI_MARK}] {d['id']}{who}: {show(ans)} — {ans.get('reason', '')[:120]}{tail}"


def _settings_changes(u: dict, current: dict) -> tuple[dict | None, str]:
    """정의의 change → 실제 바꿀 값. 지금 값보다 내리는 것만(같거나 올리면 거부)."""
    out = {}
    for key, v in u["change"].items():
        base = key[:-4] if key.endswith("_pct") else key
        cur = current.get(base)
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            return None, f"지금 {base} 값을 읽지 못함"
        new = (int(cur * v / 100) if base == "amount" else int(cur * v // 100)) if key.endswith("_pct") else v
        if base == "amount":
            new = int(new)
        if not new < cur:
            return None, f"올리는 · 같은 변경은 하지 않음({base} {cur} → {new})"
        out[base] = new
    return out, ""


def emit(d: dict, ans: dict, ctx: dict, ref: str) -> list[dict]:
    """답 하나 → 내보낸 것 목록. 각 항목 = {kind, status, obj, results?, why?, at_ms}."""
    from control import ext_write, settings_edit
    outs = []
    root, fake, dry = ctx["root"], bool(ctx.get("fake")), bool(ctx.get("dry"))
    targets = _targets_arg(ctx.get("targets"))
    tcodes = list(ctx.get("trigger", {}).get("codes") or [])
    actor = f"judge:{d['id']}"
    now = ctx.get("now_ms") or record.now_ms()
    for u in d["use"]:
        k = u["kind"]
        if k == "review":
            continue                                         # 검토 답은 emit_review 가 따로
        if not matches(u.get("if") or {}, ans):
            continue
        item = {"kind": k, "status": "", "at_ms": now, "targets": targets}
        if k in ("signal", "gate") and not targets and not dry:
            item.update(status="skipped", why="문을 켠 실행기 없음")
            outs.append(item)
            continue
        if k == "signal":
            if not tcodes:
                item.update(status="skipped", why="방아쇠 종목 없음")
                outs.append(item)
                continue
            for code in tcodes:
                obj = {"id": f"{ref}-{code}", "source": f"ai:{d['id']}", "code": code, "action": u["action"],
                       "ttl_s": u["ttl_s"],
                       "amount": u.get("amount"), "exit_by": u.get("exit_by"), "max_hold_min": u.get("max_hold_min"),
                       "stop_pct": u.get("stop_pct"), "ref": ref, "note": f"{AI_MARK}: {ans.get('reason', '')[:80]}"}
                it = dict(item, obj=obj)
                if dry:
                    it["status"] = "dry"
                else:
                    r = ext_write.write_signal(root, targets, source=obj["source"], code=code, action=u["action"],
                                               expires_s=u["ttl_s"], amount=u.get("amount"), exit_by=u.get("exit_by"),
                                               max_hold_min=u.get("max_hold_min"), stop_pct=u.get("stop_pct"),
                                               ref=ref, note=obj["note"], sid=obj["id"], fake=fake, actor=actor,
                                               pid_alive=ctx.get("pid_alive"))
                    it.update(status="written" if any(x["written"] for x in r["results"]) else "refused",
                              results=r["results"])
                outs.append(it)
        elif k == "gate":
            codes = tcodes if u.get("codes") == "trigger" else None
            if u.get("codes") == "trigger" and not tcodes:
                item.update(status="skipped", why="방아쇠 종목 없음")
                outs.append(item)
                continue
            name = d["id"] + (f"-{'-'.join(codes)}" if codes else "")
            obj = {"name": name[:60], "reason": f"{AI_MARK} {d['id']}: {show(ans)} — {ans.get('reason', '')[:100]}",
                   "ttl_s": int(u["ttl_min"] * 60), "codes": codes, "ref": ref}
            item["obj"] = obj
            if dry:
                item["status"] = "dry"
            else:
                r = ext_write.write_gate(root, targets, name=obj["name"], reason=obj["reason"], expires_s=obj["ttl_s"],
                                         codes=codes, ref=ref, fake=fake, actor=actor, pid_alive=ctx.get("pid_alive"))
                item.update(status="written" if any(x["written"] for x in r["results"]) else "refused",
                            results=r["results"])
            outs.append(item)
        elif k == "settings":
            view = settings_edit.view(root)
            if not view["ok"]:
                item.update(status="refused", why=f"설정을 읽지 못함: {view['error']}")
                outs.append(item)
                continue
            changes, why = _settings_changes(u, view["settings"])
            item["obj"] = {"change": changes}
            if changes is None:
                item.update(status="refused", why=why)
            elif dry:
                item["status"] = "dry"
            else:
                def run(ch=changes, it=item):
                    res = settings_edit.edit(ch, actor=actor, root=root, fake=fake,
                                             judge_daily_max=d["limits"]["settings_per_day"],
                                             pid_alive=ctx.get("pid_alive"))
                    it.update(status=res["status"], why=res["message"])
                    record.append(root, "settings_done", judge=d["id"], ref=ref, status=res["status"],
                                  message=res["message"], diff=res["diff"])
                    return res
                if ctx.get("run_async"):
                    item["status"] = "started"
                    threading.Thread(target=run, daemon=True).start()
                else:
                    run()
            outs.append(item)
        elif k == "notify":
            text = notice_text(d, ans, fake, tcodes)
            item["obj"] = {"text": text}
            send = ctx.get("notify")
            if dry:
                item["status"] = "dry"
            elif ctx.get("no_outside"):
                item.update(status="skipped", why="--fake: 바깥으로 보내지 않음")
            elif send is None:
                item.update(status="skipped", why="보내기 함수 없음(봇 토큰 없음)")
            else:
                try:
                    ok = send(text)
                    item["status"] = "sent" if ok is not False else "skipped"
                    if ok is False:
                        item["why"] = "봇 토큰 없음 — 건너뜀"
                except Exception as e:
                    item.update(status="error", why=f"보내기 실패 {type(e).__name__}")
            outs.append(item)
    for o in outs:
        if o.get("status") == "dry" and o["kind"] in ("signal", "gate") and not targets:
            o.setdefault("why", "문을 켠 실행기 없음 — 내보냈을 것만 기록")
    return outs


def emit_review(d: dict, ans: dict | None, ctx: dict, ref: str) -> list[dict]:
    """검토 요청 하나에 답. ans 가 없으면(버린 답) 답하지 않는다 — 실행기가 마감 뒤 on_timeout 대로 처리."""
    from control import ext_write
    req = ctx.get("trigger", {}).get("request") or {}
    target = ctx.get("trigger", {}).get("target")
    u = next((x for x in d["use"] if x["kind"] == "review"), None)
    if u is None or not req:
        return []
    now = ctx.get("now_ms") or record.now_ms()
    item = {"kind": "review", "at_ms": now, "target": target}
    if ans is None:
        item.update(status="skipped", why="답이 없음 — 실행기가 마감 뒤 on_timeout 대로")
        return [item]
    allow = matches(u["if"], ans)
    item["obj"] = {"review_id": req.get("id"), "code": req.get("code"), "allow": allow}
    if ctx.get("dry"):
        item["status"] = "dry"
        return [item]
    r = ext_write.answer_review(ctx["root"], target, review_id=str(req.get("id")), code=str(req.get("code")),
                                allow=allow, source=f"ai:{d['id']}", expires_s=60,
                                note=f"{AI_MARK}: {ans.get('reason', '')[:80]}", fake=bool(ctx.get("fake")),
                                pid_alive=ctx.get("pid_alive"))
    item.update(status="written" if any(x["written"] for x in r["results"]) else "refused", results=r["results"])
    return [item]
