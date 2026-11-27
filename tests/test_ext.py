"""바깥 신호 문 · 거르기 · risk_block · 장부 주입 · 금액 상한 시험 — 네트워크 없이 dry-run 과 가짜 봉으로."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import ext as extdoor  # noqa: E402
from strategy import live  # noqa: E402
from tests.step_fakes import events, kinds, make_runner  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, ext=True)
    return r, st, tmp_path / "state" / "ext"


def put(root: Path, name: str, obj) -> None:
    p = root / "inbox" / f"{name}.json"
    p.write_text(json.dumps(obj), encoding="utf-8")


def sig(i, code="005930", action="entry", **kw):
    d = {"id": f"s{i}", "source": "ai:test", "code": code, "action": action, "expires_ms": live.now_ms() + 60_000}
    if action == "entry" and kw.get("exit_by", "signal") == "signal" and "review_of" not in kw:
        d["max_hold_min"] = 30
    d.update(kw)
    return {k: v for k, v in d.items() if v is not None}


def gate(root: Path, name="market", ms=60_000, **kw):
    g = {"block_entries": True, "reason": "시황 위험 회피", "expires_ms": live.now_ms() + ms, **kw}
    (root / "gates" / f"{name}.json").write_text(json.dumps(g), encoding="utf-8")


def rejected(r) -> list[str]:
    return [e["reason"] for e in events(r, "ext_rejected")]


# ---------- 신호 ----------
def test_ext_entry_riskgate_then_owned(env):
    r, st, root = env
    put(root, "a", sig(1, amount=500_000))
    r.step()
    plan = events(r, "order_plan")[0]
    assert plan["qty"] == 500_000 // 61300 and plan["side"] == "buy"            # 금액은 신호 값(설정 이하)
    oi = events(r, "order_intent")[0]
    assert oi["source"] == "ai:test" and oi["ext_id"] == "s1"
    assert events(r, "ext_signal")[0]["amount_used"] == 500_000
    assert r.ext.owns("005930")
    owned = json.loads((root / "owned.json").read_text(encoding="utf-8"))
    assert owned["005930"]["signal_id"] == "s1" and owned["005930"]["max_hold_min"] == 30
    assert not list((root / "inbox").glob("*.json")) and (root / "inbox_done" / "a.json").exists()   # 한 번만 읽음


def test_ext_amount_never_exceeds_setting(env):
    r, st, root = env
    put(root, "a", sig(1, amount=50_000_000))
    r.step()
    assert events(r, "order_plan")[0]["qty"] == 1_000_000 // 61300               # 설정 금액(100만) 까지만
    assert events(r, "ext_signal")[0]["amount_used"] == 1_000_000


def test_ext_entry_cap_risk_block_once(env):
    r, st, root = env
    r.oms.entries_today = 2                                                     # 상한 2 에 닿음
    put(root, "a", sig(1))
    put(root, "b", sig(2, code="000660"))
    r.step()
    st.next = "long_entry"
    r.last_bar.clear()
    r.step()
    rb = events(r, "risk_block")
    assert len(rb) == 1 and rb[0]["kind"] == "cap" and rb[0]["source"] == "ai:test"   # (날, 종류)마다 한 번
    assert "order_plan" not in kinds(r)


def test_max_hold_min_required(env):
    r, st, root = env
    put(root, "a", {**sig(1), "max_hold_min": None})
    put(root, "b", {"id": "s2", "source": "ai:test", "code": "005930", "action": "entry",
                    "expires_ms": live.now_ms() + 60_000})
    r.step()
    assert sum("max_hold_min 없음" in x for x in rejected(r)) == 2 and r.oms.held("005930") == 0
    put(root, "c", sig(3, exit_by="strategy"))                                 # 전략이 파는 진입은 없어도 됨
    r.step()
    assert r.oms.held("005930") > 0 and not r.ext.owns("005930")


def test_bad_signals_rejected_not_crash(env):
    r, st, root = env
    put(root, "a", sig(1, code="5930"))
    put(root, "b", sig(2, code="035420"))                                      # 실행기 종목 목록 밖
    put(root, "c", {**sig(3), "expires_ms": 1})                                # 만료
    put(root, "d", {**sig(4), "source": "anyone"})
    (root / "inbox" / "e.json").write_text("{깨짐", encoding="utf-8")
    put(root, "f", sig(5))
    put(root, "g", sig(5))                                                     # 같은 id
    put(root, "h", sig(6, action="buy"))
    r.step()
    rej = rejected(r)
    assert len(rej) == 7 and r.oms.held("005930") > 0
    assert "만료됨" in rej and any("목록 밖" in x for x in rej) and "같은 id 두 번" in rej
    assert any("출처" in x for x in rej) and any("형식" in x for x in rej)


def test_expired_signal_rejected_first(env):
    r, st, root = env
    put(root, "a", {"id": "j2", "source": "ai:x", "code": "005930", "action": "entry", "expires_ms": live.now_ms() - 1})
    r.step()
    assert rejected(r) == ["만료됨"] and r.oms.held("005930") == 0


# ---------- 청산 · 소유 ----------
def test_strategy_exit_skips_ext_owned(env):
    r, st, root = env
    put(root, "a", sig(1))
    r.step()
    assert r.oms.held("005930") > 0
    st.next = "long_exit"
    r.last_bar.clear()
    r.step()
    assert r.oms.held("005930") > 0                                            # 전략 청산으로는 안 팖(exit_by signal)
    assert any("exit_by=signal" in e["reason"] for e in events(r, "order_skip"))
    put(root, "b", sig(2, action="exit"))
    r.last_bar.clear()
    st.next = None
    r.step()
    assert r.oms.held("005930") == 0                                           # 바깥 매도 신호로 팖
    # 표시는 '들고 있지 않고 진행 중 주문도 없는' 첫 확인 때 풀린다 — dry-run 은 바로 체결되므로 같은 봉의 소유 확인에서
    assert not r.ext.owned and json.loads((root / "owned.json").read_text(encoding="utf-8")) == {}


def test_exit_by_strategy_sells(env):
    r, st, root = env
    put(root, "a", sig(1, exit_by="strategy"))
    r.step()
    st.next = "long_exit"
    r.last_bar.clear()
    r.step()
    assert r.oms.held("005930") == 0


def test_max_hold_rule_exit_and_release(env):
    r, st, root = env
    put(root, "a", sig(1, max_hold_min=1))
    r.step()
    r.ext.owned["005930"]["t_ms"] -= 120_000
    r.step()
    ex = events(r, "ext_rule_exit")
    assert ex and "보유 1분" in ex[0]["reason"] and r.oms.held("005930") == 0
    assert events(r, "order_intent")[-1]["source"].startswith("rule:")
    r.step()
    assert not r.ext.owned


def test_stop_pct_rule_exit(env):
    r, st, root = env
    put(root, "a", sig(1, stop_pct=2))
    r.step()
    r.price_now = lambda code: int(61300 * 0.97)                                 # 지금 현재가가 평균가보다 3% 아래
    r.step()
    ex = events(r, "ext_rule_exit")[0]
    assert ex["reason"] == "손절 2%" and r.oms.held("005930") == 0
    assert ex["px"] == int(61300 * 0.97) and ex["avg"]                           # 판정에 쓴 값이 기록에 남는다


def test_stop_uses_fresh_price(env):
    """손절은 판정 직전에 받은 현재가로 본다 — 문은 봉 처리 첫머리에서 돌아 oms.current 가 앞 봉 종가(사기 전 값)다.
    2026-11-26 장중: 묵은 값이 낮아 산 값보다 높게 팔리고도 '손절'로 찍혔다."""
    r, st, root = env
    put(root, "a", sig(1, stop_pct=2))
    r.step()
    r.oms.current["005930"] = int(61300 * 0.90)                                  # 묵은 값은 10% 아래
    r.step()                                                                     # 지금 현재가(가짜 61,234)는 평균 근처 → 손절 아님
    assert not events(r, "ext_rule_exit") and r.oms.held("005930") > 0

    def fail(code):
        raise RuntimeError("조회 실패")
    r.price_now = fail                                                           # 현재가를 못 받으면 아는 값으로 판정
    r.oms.current["005930"] = int(61300 * 0.90)
    r.step()
    assert events(r, "ext_rule_exit")[0]["reason"] == "손절 2%"


def test_dry_door_refused(env, monkeypatch):
    """dry-run 과 모의는 같은 문 폴더를 쓴다 — 모의가 살아 있으면 dry-run 은 문을 연 채 시작하지 않는다."""
    r, st, root = env
    lock = live.STATE_DIR / "run_paper.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(live, "pid_alive", lambda pid: pid == 4242)
    r.run(once=True)
    assert "door shared with paper runner" in events(r, "start_refused")[0]["reason"]
    assert lock.read_text(encoding="utf-8") == "4242"                            # 모의의 잠금은 건드리지 않는다


def test_owned_released_if_never_filled(env):
    r, st, root = env
    put(root, "a", sig(1))
    r.step()
    r.oms.positions.pop("005930", None)                                        # 진입이 체결 없이 끝난 것과 같게
    r.step()
    assert not r.ext.owned


# ---------- 거르기 ----------
def test_gate_blocks_until_expiry(env):
    r, st, root = env
    gate(root)
    st.next = "long_entry"
    r.step()
    assert "entry_filtered" in kinds(r) and r.oms.held("005930") == 0
    assert r.entries_today() == 0                                               # 거르기에 막힌 진입은 상한을 쓰지 않음
    gate(root, ms=-1)                                                          # 만료 → 무시
    r.last_bar.clear()
    r.step()
    assert r.oms.held("005930") > 0


def test_gate_blocks_outside_entries_only(env):
    r, st, root = env
    put(root, "a", sig(1))
    r.step()
    assert r.oms.held("005930") > 0
    gate(root)
    put(root, "b", sig(2, code="000660", source="telegram"))
    put(root, "c", sig(3, action="exit", source="telegram"))
    r.step()
    f = events(r, "entry_filtered")
    assert len(f) == 1 and f[0]["code"] == "000660" and f[0]["source"] == "telegram"
    assert r.oms.held("000660") == 0 and r.oms.held("005930") == 0              # 진입은 막고 청산은 나감


def test_gate_codes_limit_and_risk_first(env):
    r, st, root = env
    gate(root, codes=["000660"])
    st.next = "long_entry"
    r.step()
    assert r.oms.held("005930") > 0 and r.oms.held("000660") == 0               # 거르기 종목만 막음
    r2_root = root
    r.oms.entries_today = 99                                                    # 상한이 먼저 — 거르기 기록 없이 risk_block
    r.risk.max_entries = 1
    n = len(events(r, "entry_filtered"))
    put(r2_root, "x", sig(9, code="000660"))
    r.step()
    assert len(events(r, "entry_filtered")) == n and events(r, "risk_block")


# ---------- 봉 내놓기 · 문 꺼짐 ----------
def test_publish_bars_for_judge(env):
    r, st, root = env
    r.step()
    p = root / "bars" / "005930.csv"
    assert p.exists() and p.read_text(encoding="utf-8").splitlines()[0].startswith("time,open")
    assert not list((root / "bars").glob("*.tmp"))


def test_door_off_creates_and_reads_nothing(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch)                                  # ext 없음(기본)
    assert r.ext is None
    st.next = "long_entry"
    r.step()
    assert not (tmp_path / "state" / "ext").exists()                           # 폴더를 만들지 않음
    (tmp_path / "state" / "ext" / "inbox").mkdir(parents=True)
    put(tmp_path / "state" / "ext", "a", sig(1, code="000660", action="exit"))
    (tmp_path / "state" / "ext" / "gates").mkdir()
    gate(tmp_path / "state" / "ext")
    r.last_bar.clear()
    r.step()
    assert (tmp_path / "state" / "ext" / "inbox" / "a.json").exists()          # 읽지 않음(옮기지도 않음)
    assert not any(k.startswith("ext_") or k == "entry_filtered" for k in kinds(r))
    assert not (tmp_path / "state" / "ext" / "bars").exists()


def test_no_door_warns_if_owned_left(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch)
    (tmp_path / "state" / "ext").mkdir(parents=True)
    (tmp_path / "state" / "ext" / "owned.json").write_text(json.dumps({"005930": {"exit_by": "signal"}}),
                                                           encoding="utf-8")
    r.run(once=True)
    w = events(r, "ext_owned_left")
    assert w and w[0]["codes"] == ["005930"]
    assert sorted(p.name for p in (tmp_path / "state" / "ext").iterdir()) == ["owned.json"]   # 다른 것은 만들지 않음


def test_start_with_door_no_warning(env):
    r, st, root = env
    (root / "owned.json").write_text(json.dumps({"005930": {"exit_by": "signal"}}), encoding="utf-8")
    r.run(once=True)
    assert not events(r, "ext_owned_left")


# ---------- 재현 문 ----------
def test_replay_door_feeds_by_virtual_clock(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch)
    t = {"ms": 1_000_000}
    r.clock_ms = lambda: t["ms"]
    p = tmp_path / "rec.jsonl"
    recs = [{"type": "gate", "at_ms": 900_000, "name": "m", "gate": {"block_entries": True, "expires_ms": 1_100_000}},
            {"type": "signal", "at_ms": 1_200_000, "signal": {**sig(1, code="000660"), "expires_ms": 1_300_000}}]
    p.write_text("\n".join(json.dumps(x) for x in recs), encoding="utf-8")
    r.ext = extdoor.ReplayDoor(p, r.log)
    st.next = "long_entry"
    r.step()
    assert len(events(r, "entry_filtered")) == 2                                 # 거르기가 걸린 동안
    t["ms"] = 1_200_000
    st.next = None
    r.step()
    assert r.oms.held("000660") > 0 and events(r, "ext_signal")[0]["id"] == "s1"


# ---------- 장부 주입 · 주문 종류 검사 ----------
def test_book_injection(tmp_path, monkeypatch):
    import importlib.util
    r0, _ = make_runner(tmp_path, monkeypatch)
    base = type(r0.oms)               # 기본 장부: 주문 관리(oms.manager)가 있는 판은 OrderManager, 없는 판은 SimpleBook
    assert base.__name__ == ("OrderManager" if importlib.util.find_spec("oms.manager") else "SimpleBook")
    made = []

    class Book(base):
        def __init__(self, *a, **k):
            made.append(a[2].name)
            super().__init__(*a, **k)
    r, st = make_runner(tmp_path, monkeypatch, book=Book)
    assert made == ["oms_dry.json"] and isinstance(r.oms, Book)
    r2, _ = make_runner(tmp_path, monkeypatch)
    assert type(r2.oms) is base                                                  # 기본은 그대로


def _watch(r, tmp_path, cur, box):
    p = tmp_path / "c.yaml"
    p.write_text("x", encoding="utf-8")
    r.watch_settings(p, lambda _p: box["v"] if not isinstance(box["v"], Exception) else (_ for _ in ()).throw(box["v"]),
                     cur)
    return p


CUR = {"order_mode": "limit", "amount": 1_000_000, "wait": 50, "max_orders": 5, "loss_limit": 30_000,
       "codes": ["005930"], "strategy": "supertrend", "params": {}, "poll": 2, "settle": 3, "apply_to_open": False,
       "tf": 1, "max_amount": None}


def test_reload_uses_check_kinds(tmp_path, monkeypatch):
    import execution.modes as modes
    r, st = make_runner(tmp_path, monkeypatch, codes=("005930",))
    box = {"v": dict(CUR)}
    _watch(r, tmp_path, CUR, box)
    calls = []

    def fake(s, name):
        calls.append((s.mode, name))
        raise ValueError("가짜 검사 — 이 연결에선 못 냄")
    monkeypatch.setattr(modes, "check_kinds", fake)
    box["v"] = {**CUR, "order_mode": "market"}
    assert r.reload_settings(force=True) is None
    assert calls == [("market", "dry-run")] and events(r, "settings_rejected")[-1]["error"].startswith("ValueError")


def test_reload_oserror_retries_quietly(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, codes=("005930",))
    import os
    box = {"v": PermissionError("다른 곳이 바꿔 넣는 중")}
    p = _watch(r, tmp_path, CUR, box)
    t = p.stat().st_mtime + 10
    os.utime(p, (t, t))                                                         # 파일이 바뀜 → 읽다가 OSError
    assert r.reload_settings() is None and not events(r, "settings_rejected")
    box["v"] = {**CUR, "amount": 500_000}
    assert r.reload_settings() == {"amount": [1_000_000, 500_000]}               # 다음 봉에 다시 읽어 적용


def test_reload_error_hides_user_folder(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, codes=("005930",))
    box = {"v": ValueError(f"읽기 실패: {Path.home() / 'x' / 'config.yaml'}")}
    _watch(r, tmp_path, CUR, box)
    r.reload_settings(force=True)
    err = events(r, "settings_rejected")[-1]["error"]
    assert str(Path.home()) not in err and "~" in err


# ---------- 금액 상한 두 층 ----------
def test_amount_over_cap_refuses_start(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, amount=1_500_000, max_amount=1_000_000)
    r.run(once=True)
    ev = r.log.today_events()
    assert ev[-1]["event"] == "start_refused" and ev[-1]["reason"] == "amount over cap"
    assert "1,500,000" in ev[-1]["detail"] and "start" not in kinds(r)


def test_cap_above_code_cap_refuses(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, amount=1_000_000, max_amount=5_000_000)
    assert r.max_amount == live.order.MAX_ORDER_WON
    r.run(once=True)
    assert r.log.today_events()[-1]["event"] == "start_refused"


def test_cap_lower_than_code_cap_works(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, codes=("005930",), amount=500_000, max_amount=500_000)
    r.run(once=True)
    start = events(r, "start")[0]
    assert start["max_amount"] == 500_000 and start["amount"] == 500_000 and start["ext"] is False


def test_reload_over_cap_rejected(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, codes=("005930",), max_amount=1_000_000)
    box = {"v": dict(CUR)}
    _watch(r, tmp_path, {**CUR, "max_amount": 1_000_000}, box)
    box["v"] = {**CUR, "max_amount": 1_000_000, "amount": 2_000_000}
    assert r.reload_settings(force=True) is None and r.amount == 1_000_000
    rej = events(r, "settings_rejected")[-1]
    assert "상한" in rej["error"] and rej["changes"] == {"amount": [1_000_000, 2_000_000]}
    box["v"] = {**CUR, "max_amount": 3_000_000, "amount": 1_000_000}            # 상한은 실행 중에 못 올림
    r.reload_settings(force=True)
    assert r.max_amount == 1_000_000 and events(r, "settings_partial")[-1]["not_applied"] == ["max_amount"]


def test_reload_without_max_amount_same(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, codes=("005930",))
    box = {"v": dict(CUR)}
    _watch(r, tmp_path, CUR, box)
    box["v"] = {**CUR, "amount": 3_000_000}
    assert r.reload_settings(force=True) == {"amount": [1_000_000, 3_000_000]} and r.amount == 3_000_000


# ---------- 알림 문구 ----------
def test_telegram_texts_for_new_events():
    from notify import telegram
    f = telegram.format_event
    assert "새 진입 멈춤" in f({"event": "risk_block", "reason": "오늘 진입 상한 5회에 닿음"})
    assert "telegram → 005930 진입" in f({"event": "ext_signal", "source": "telegram", "code": "005930", "action": "entry"})
    assert "만료됨" in f({"event": "ext_rejected", "reason": "만료됨"})
    assert "거르기" in f({"event": "entry_filtered", "code": "005930", "gate": "market: 시황"})
    assert "보유 20분" in f({"event": "ext_rule_exit", "code": "068270", "reason": "보유 20분 지남"})
    assert "005930" in f({"event": "ext_owned_left", "codes": ["005930"]})
    assert "진입하지 않음" in f({"event": "review_expired", "code": "005930", "on_timeout": "skip"})
    assert "전략대로" in f({"event": "review_unavailable", "on_timeout": "pass"})
    assert "정지 파일" in f({"event": "start_refused", "reason": "stop file"})
    assert "상한" in f({"event": "start_refused", "reason": "amount over cap", "detail": "1회 금액 … 상한 …"})
    assert f({"event": "entry_review", "code": "005930"}) is None                 # 검토 요청은 판단이 읽는다(알림 없음)
