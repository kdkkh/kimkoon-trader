"""control/ 시험 — 상태 읽기 · 끄기/켜기 · 설정 편집 · 문에 쓰기. 모두 임시 폴더에서, 바깥 API 없이.

실행기는 기존 시험과 같은 가짜(가짜 봉 · 가짜 현재가 · dry-run 연결)를 임시 폴더에 띄운다.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from control import ext_write, settings_edit as se, state, switch  # noqa: E402
from control.common import journal_today, log_path  # noqa: E402
from strategy import live  # noqa: E402
from strategy.ext import validate_signal  # noqa: E402
from tests.step_fakes import NOW, FakeStrat, bars  # noqa: E402


def cfg_text(**kw) -> str:
    kw.setdefault("max_amount", 3_000_000)          # 예시 설정의 상한은 판마다 다르다 — 시험은 코드 상한으로 고정
    return se.set_keys((ROOT / "config.example.yaml").read_text(encoding="utf-8"), kw)


def make_live(tmp, monkeypatch, codes=("005930", "000660"), amount=3_000_000, max_orders=9, price=61234,
              max_amount=None):
    """임시 폴더(tmp/state · tmp/logs · tmp/config.yaml)에 dry-run 실행기 + 설정 지켜보기."""
    monkeypatch.setattr(live, "LOG_DIR", tmp / "logs")
    monkeypatch.setattr(live, "STATE_DIR", tmp / "state")
    monkeypatch.setattr(live.data.PACER, "call", lambda fn, *a, **k: {"stck_prpr": str(price)})
    monkeypatch.setattr(live.data, "cfg_of", lambda mode: None)
    cfg = tmp / "config.yaml"
    cfg.write_text(cfg_text(codes=list(codes), amount=amount, max_orders=max_orders), encoding="utf-8")
    s = se.load_settings(cfg)
    st = FakeStrat()
    r = live.LiveRunner(list(codes), st, amount=s["amount"], max_orders=s["max_orders"], printer=lambda *a: None,
                        max_amount=max_amount)
    r.watch_settings(cfg, se.load_settings, s)
    b = bars(float(price))

    def upd(code):
        r.bars[code] = b
        return b, 0, 0
    r._update_bars = upd
    r.now_dt = lambda: pd.Timestamp(NOW).to_pydatetime()
    r.save_bars = lambda: None
    r.beat(force=True)                                # heartbeat → 'dry' 실행기가 살아 있음
    return r, st, cfg


def bar(r, st, sig=None):
    st.next = sig
    r.last_bar.clear()
    return r.step()


def ev(r, name):
    return [e for e in r.log.today_events() if e["event"] == name]


def fake_running(root: Path, target: str, ext=True, codes=("005930", "000660"), broker=None):
    """실행기 없이 '돌고 있는 실행기' 흔적만 만든다: heartbeat + 오늘 기록의 start 사건."""
    p = state.runner_paths(root, target)
    p["state"].mkdir(parents=True, exist_ok=True)
    br = broker or p["brokers"][0]
    p["heartbeat"].write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "broker": br,
                                          "codes": list(codes)}), encoding="utf-8")
    lp = log_path(root, target)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts_ms": int(time.time() * 1000), "event": "start", "codes": list(codes), "ext": ext,
                            "max_amount": 3_000_000, "tf": 1, "settle": 3}) + "\n")
    if ext:
        (p["ext"] / "inbox").mkdir(parents=True, exist_ok=True)


# ---------- 설정 편집 ----------
def test_amount_edit_changes_next_qty(tmp_path, monkeypatch):
    r, st, cfg = make_live(tmp_path, monkeypatch)
    assert [a["qty"] for a in bar(r, st, "long_entry")] == [48, 48]          # 300만 ÷ 61,300
    bar(r, st, "long_exit")
    res = se.edit({"amount": 1_000_000}, actor="desk", root=tmp_path, after_write=lambda p: r.reload_settings())
    assert res["status"] == "applied" and res["diff"] == {"amount": [3_000_000, 1_000_000]}
    assert ev(r, "settings_changed")[-1]["changes"] == {"amount": [3_000_000, 1_000_000]}
    assert [a["qty"] for a in bar(r, st, "long_entry")] == [16, 16]          # 100만 ÷ 61,300
    assert (tmp_path / "state" / "config_history").is_dir() and res["history"]
    text = cfg.read_text(encoding="utf-8")
    assert "amount: 1000000" in text and "# 금액 기준 수량" in text            # 그 줄만 · 주석 유지


def test_path_like_strategy_refused(tmp_path, monkeypatch):
    r, st, cfg = make_live(tmp_path, monkeypatch)
    before = cfg.read_bytes()
    for bad in ("strategy/ported/evil.py", "evil.py", "..\\evil", "no_such_strategy"):
        res = se.edit({"strategy": bad}, actor="desk", root=tmp_path, after_write=lambda p: r.reload_settings())
        assert res["status"] == "refused" and "전략" in res["message"]
    assert cfg.read_bytes() == before and not ev(r, "settings_rejected")


def test_amount_over_cap_refused(tmp_path, monkeypatch):
    r, st, cfg = make_live(tmp_path, monkeypatch)
    before = cfg.read_bytes()
    res = se.edit({"amount": 5_000_000}, actor="desk", root=tmp_path)
    assert res["status"] == "refused" and "상한" in res["message"] and cfg.read_bytes() == before


def test_held_code_removal_refused(tmp_path, monkeypatch):
    r, st, cfg = make_live(tmp_path, monkeypatch)
    bar(r, st, "long_entry")                                                  # 두 종목 다 보유
    before = cfg.read_bytes()
    res = se.edit({"codes": ["005930"]}, actor="desk", root=tmp_path)
    assert res["status"] == "refused" and "000660" in res["message"] and "뺄 수 없음" in res["message"]
    assert cfg.read_bytes() == before
    res = se.edit({"codes": ["005930", "000660", "035420"]}, actor="desk", root=tmp_path,
                  after_write=lambda p: r.reload_settings())               # 더하는 것은 된다
    assert res["status"] == "applied" and r.codes == ["005930", "000660", "035420"]


def test_runner_reject_reverts_file(tmp_path, monkeypatch):
    # 실행기는 시작할 때 상한 100만 원으로 떴고 파일 상한은 300만 → 미리 검사는 통과, 실행기가 거부
    r, st, cfg = make_live(tmp_path, monkeypatch, amount=1_000_000, max_amount=1_000_000)
    before = cfg.read_bytes()
    res = se.edit({"amount": 2_000_000}, actor="desk", root=tmp_path, after_write=lambda p: r.reload_settings())
    assert res["status"] == "rejected" and "되돌림" in res["message"] and cfg.read_bytes() == before
    assert ev(r, "settings_rejected") and r.amount == 1_000_000
    res = se.edit({"amount": 500_000}, actor="desk", root=tmp_path, after_write=lambda p: r.reload_settings())
    assert res["status"] == "applied" and r.amount == 500_000                # 되돌렸으니 뒤 변경이 막히지 않는다


def test_judge_lower_only_and_fields(tmp_path, monkeypatch):
    r, st, cfg = make_live(tmp_path, monkeypatch)
    hook = {"after_write": lambda p: r.reload_settings()}
    assert se.edit({"amount": 3_000_000 - 1}, actor="judge:regime", root=tmp_path, **hook)["status"] == "applied"
    up = se.edit({"amount": 3_000_000}, actor="judge:regime", root=tmp_path, **hook)
    assert up["status"] == "refused" and "내리기만" in up["message"]
    for k, v in (("codes", ["005930"]), ("loss_limit", 10_000), ("order_mode", "market"), ("strategy", "supertrend")):
        res = se.edit({k: v}, actor="judge:regime", root=tmp_path, **hook)
        assert res["status"] == "refused" and "판단이 바꿀 수 있는 칸" in res["message"]
    assert se.edit({"max_orders": 3}, actor="judge:regime", root=tmp_path, **hook)["status"] == "applied"
    lim = se.edit({"max_orders": 2}, actor="judge:regime", root=tmp_path, judge_daily_max=2, **hook)
    assert lim["status"] == "refused" and "상한" in lim["message"]
    assert se.edit({"amount": 1}, actor="judge:bad id", root=tmp_path)["status"] == "refused"


def test_two_quick_writes_both_logged(tmp_path, monkeypatch):
    r, st, cfg = make_live(tmp_path, monkeypatch)
    stop = threading.Event()

    def runner_loop():                                                        # 실행기가 봉마다 읽는 것을 빠르게 흉내
        while not stop.is_set():
            r.reload_settings()
            time.sleep(0.03)
    th = threading.Thread(target=runner_loop, daemon=True)
    th.start()
    out = {}
    ws = [threading.Thread(target=lambda v=v: out.__setitem__(v, se.edit({"amount": v}, root=tmp_path, wait_s=10)))
          for v in (700_000, 200_000)]
    for w in ws:
        w.start()
    for w in ws:
        w.join(30)
    stop.set()
    th.join(5)
    assert {v: o["status"] for v, o in out.items()} == {700_000: "applied", 200_000: "applied"}
    seen = [c["changes"]["amount"][1] for c in ev(r, "settings_changed")]
    assert sorted(seen) == [200_000, 700_000]                                 # 가운데 값도 실행기 기록에 남음
    tries = [j for j in journal_today(tmp_path) if j["action"] == "settings_edit"]
    assert len(tries) == 2 and all(j["status"] == "applied" for j in tries)


def test_config_lock_busy_and_stale(tmp_path):
    (tmp_path / "config.yaml").write_text(cfg_text(), encoding="utf-8")
    lock = tmp_path / "state" / "config.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "t": time.time()}), encoding="utf-8")
    res = se.edit({"amount": 500_000}, root=tmp_path, lock_wait_s=0.2)
    assert res["status"] == "busy" and "amount: 1000000" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    lock.write_text(json.dumps({"pid": 999_999, "t": time.time()}), encoding="utf-8")     # 죽은 프로세스의 잠금
    res = se.edit({"amount": 500_000}, root=tmp_path, lock_wait_s=0.2, pid_alive=lambda p: False)
    assert res["status"] == "saved" and "다음 시작" in res["message"] and not lock.exists()
    lock.write_text(json.dumps({"pid": os.getpid(), "t": time.time() - 3600}), encoding="utf-8")   # 오래된 잠금
    assert se.edit({"amount": 400_000}, root=tmp_path, lock_wait_s=0.2)["status"] == "saved"
    assert len([j for j in journal_today(tmp_path) if j["action"] == "settings_edit"]) == 3


def test_edit_refuses_unreloadable_and_fake(tmp_path):
    (tmp_path / "config.yaml").write_text(cfg_text(), encoding="utf-8")
    before = (tmp_path / "config.yaml").read_bytes()
    assert "바꿀 수 없는 칸" in se.edit({"tf": 5}, root=tmp_path)["message"]
    assert "바꿀 수 없는 칸" in se.edit({"max_amount": 1}, root=tmp_path)["message"]
    assert se.edit({"amount": 500_000}, actor="judge:x", root=tmp_path, fake=True)["status"] == "refused"
    assert se.edit({"amount": 1_000_000}, root=tmp_path)["status"] == "unchanged"
    assert se.edit({"order_mode": "chase"}, root=tmp_path)["status"] == "refused"       # 잠긴 방식
    assert (tmp_path / "config.yaml").read_bytes() == before


def test_set_keys_keeps_comments(tmp_path):
    t = "amount: 1000000   # 금액\nparams:\n  a: 1\n"
    assert se.set_keys(t, {"amount": 5}).startswith("amount: 5         # 금액")
    for bad in ({"params": {"a": 2}},):
        try:
            se.set_keys(t, bad)
            raise AssertionError("블록 표기는 거부해야 함")
        except ValueError:
            pass
    assert se.parse_value("codes", "5930, 660") == ["005930", "000660"]
    assert se.parse_value("amount", "500,000") == 500000


# ---------- 문에 쓰기 ----------
def test_ext_write_door_closed_refused(tmp_path):
    r = ext_write.write_signal(tmp_path, "paper", source="telegram", code="005930", action="exit")
    assert not r["ok"] and "문이 꺼져 있음" in r["results"][0]["why"]
    fake_running(tmp_path, "paper", ext=False)
    r = ext_write.write_gate(tmp_path, "paper", name="regime", reason="시험", expires_s=60)
    assert not r["ok"] and "--ext 없이" in r["results"][0]["why"]
    assert not (tmp_path / "state" / "ext" / "gates").exists()


def test_ext_write_signal_valid_file(tmp_path):
    fake_running(tmp_path, "paper")
    r = ext_write.write_signal(tmp_path, "paper", source="telegram", code="005930", action="entry", amount=500_000,
                               max_hold_min=30, stop_pct=2.0)
    assert r["ok"]
    f = tmp_path / "state" / "ext" / r["results"][0]["file"]
    d = json.loads(f.read_text(encoding="utf-8"))
    assert validate_signal(d, int(time.time() * 1000), ["005930"]) is None and d["source"] == "telegram"
    bad = ext_write.write_signal(tmp_path, "paper", source="telegram", code="005930", action="entry")
    assert not bad["ok"] and "max_hold_min" in bad["results"][0]["why"]     # 보유 시간 없는 진입은 실행기와 같은 기준으로 거부
    out = ext_write.write_signal(tmp_path, "paper", source="telegram", code="999999", action="exit")
    assert not out["ok"] and "목록 밖" in out["results"][0]["why"]


def test_fake_source_not_written_to_paper(tmp_path):
    fake_running(tmp_path, "paper")
    fake_running(tmp_path, "sim")
    r = ext_write.write_gate(tmp_path, "both", name="regime", reason="가짜 응답", expires_s=60, fake=True)
    paper, sim = r["results"]
    assert not paper["written"] and "가짜" in paper["why"] and sim["written"]
    assert not list((tmp_path / "state" / "ext").glob("gates/*.json"))
    assert list((tmp_path / "state" / "sim" / "ext").glob("gates/*.json"))
    logged = [j for j in journal_today(tmp_path) if j["action"] == "ext_gate"]
    assert [j["target"] for j in logged] == ["paper", "sim"] and all(j["fake"] for j in logged)
    d = state.door_state(tmp_path, "sim")
    assert d["open"] and [g["name"] for g in d["gates"]] == ["regime"]


def test_gate_and_review_flow_on_runner(tmp_path, monkeypatch):
    r, st, cfg = make_live(tmp_path, monkeypatch)
    r.ext = live.extdoor.FileDoor(tmp_path / "state" / "ext", r.log)
    r.log.write("start", codes=r.codes, ext=True, max_amount=3_000_000)
    assert ext_write.write_gate(tmp_path, "dry", name="regime", reason="시험 거르기", expires_s=120)["ok"]
    bar(r, st, "long_entry")
    assert len(ev(r, "entry_filtered")) == 2 and not ev(r, "order_intent")
    ext_write.clear_gate(tmp_path, "dry", name="regime")
    sig = ext_write.write_signal(tmp_path, "dry", source="telegram", code="005930", action="entry", amount=1_000_000,
                                 max_hold_min=30)
    assert sig["ok"]
    bar(r, st)
    assert ev(r, "ext_signal")[0]["source"] == "telegram" and r.oms.held("005930") == 16
    assert state.door_state(tmp_path, "dry")["owned"]["005930"]["qty"] == 16


# ---------- 상태 읽기 ----------
def test_state_survives_broken_files(tmp_path):
    p = state.runner_paths(tmp_path, "paper")
    p["state"].mkdir(parents=True)
    p["oms"].write_text('{"positions": {"005930": {"qty": 3}}, "orders": [1, {"code": "x"}]', encoding="utf-8")
    p["heartbeat"].write_text(json.dumps({"at": "시각 아님"}), encoding="utf-8")
    p["lock"].write_text("pid 아님", encoding="utf-8")
    lp = log_path(tmp_path, "paper")
    lp.parent.mkdir(parents=True)
    lp.write_bytes(b'{"event": "signal"}\n{"event": "fill"\n' + "{\"event\": \"반".encode("utf-8")[:-1])
    (p["ext"] / "gates").mkdir(parents=True)
    (p["ext"] / "gates" / "x.json").write_text("{", encoding="utf-8")
    (p["ext"] / "owned.json").write_text("[1, 2]", encoding="utf-8")
    (p["ext"] / "review.json").write_text("깨짐", encoding="utf-8")
    s = state.runner_status(tmp_path, "paper")
    assert s["alive"] is False and s["positions"] == {} and s["door"]["open"] is False
    assert state.today_summary(tmp_path, "paper")["events"] == {"signal": 1}
    p["oms"].write_text(json.dumps({"positions": {"005930": {"qty": 3}}}), encoding="utf-8")      # 평균가 없음
    assert state.book(tmp_path, "paper")["positions"] == {"005930": {"qty": 3, "avg": 0.0}}
    assert state.held_codes(tmp_path) == {"005930": ["paper"]}
    assert state.runner_status(tmp_path, "sim")["alive"] is False                                  # 폴더가 없어도


def test_state_hides_order_numbers(tmp_path):
    p = state.runner_paths(tmp_path, "paper")
    p["state"].mkdir(parents=True)
    odno, acct = "0000" + "917355", "4321" + "8765"
    p["oms"].write_text(json.dumps({"positions": {"005930": {"qty": 3, "avg": 61300}}, "orders": [
        {"code": "005930", "side": "sell", "qty": 3, "state": "sent", "odno": odno, "raw": {"CANO": acct}}]}),
        encoding="utf-8")
    lp = log_path(tmp_path, "paper")
    lp.parent.mkdir(parents=True)
    lp.write_text(json.dumps({"event": "stop", "reason": "end", "summary": {
        "orders_done": {"filled": 2}, "realized": -1200, "unrealized": 0, "entries_today": 1,
        "positions": {"005930": {"qty": 3, "avg": 61300, "odno": odno}}}}) + "\n", encoding="utf-8")
    blob = json.dumps([state.runner_status(tmp_path, "paper"), state.today_summary(tmp_path, "paper")], ensure_ascii=False)
    assert odno not in blob and acct not in blob and "raw" not in blob
    assert state.today_summary(tmp_path, "paper")["stop_summary"]["realized"] == -1200


def test_alive_needs_matching_broker(tmp_path):
    fake_running(tmp_path, "paper", broker="dry-run")          # 옛 판 dry-run 이 모의 heartbeat.json 에 쓴 것
    assert not state.alive(tmp_path, "paper")[0] and not state.alive(tmp_path, "dry")[0]
    fake_running(tmp_path, "dry")                              # 지금 판 dry-run 은 heartbeat_dry.json
    assert state.runner_paths(tmp_path, "dry")["heartbeat"].name == "heartbeat_dry.json"
    assert state.alive(tmp_path, "dry")[0] and not state.alive(tmp_path, "paper")[0]
    old = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    state.runner_paths(tmp_path, "dry")["heartbeat"].write_text(json.dumps({"at": old}), encoding="utf-8")
    assert not state.alive(tmp_path, "dry")[0]


# ---------- 끄기 · 켜기 ----------
def test_holiday_table_unknown_and_closed(tmp_path):
    h = tmp_path / "h.yaml"
    assert switch.holiday_status(datetime(2099, 3, 3).date(), h)[0] == "unknown"      # 파일 없음 = 모름
    h.write_text('source: "시험"\nchecked: "2099-01-01"\nyears:\n  "2099": ["2099-03-03"]\n', encoding="utf-8")
    assert switch.holiday_status(datetime(2099, 3, 3).date(), h)[0] == "closed"
    assert switch.holiday_status(datetime(2099, 3, 4).date(), h)[0] == "open"
    assert switch.holiday_status(datetime(2099, 3, 7).date(), h) == ("closed", "주말")
    ok, why, notes = switch.market_check(datetime(2098, 3, 4, 10, 0), "15:20", h)
    assert ok and "모름" in notes[0]                                                 # 표에 없는 해는 막지 않음
    assert not switch.market_check(datetime(2099, 3, 4, 16, 0), "15:20", h)[0]
    real = switch.load_holidays(ROOT / "control" / "holidays.yaml")              # 실제 표: 출처 · 확인 날짜가 있고 주말이 섞이지 않음
    assert real["source"] and real["checked"]
    for days in real["years"].values():
        assert all(datetime.strptime(x, "%Y-%m-%d").weekday() < 5 for x in days)


def test_switch_start_stop_with_fake_launch(tmp_path):
    launched = []
    mon = datetime(2098, 3, 3, 10, 0)                                                # 표에 없는 해의 월요일
    r = switch.start(tmp_path, "paper", now=mon, launcher=lambda a, cwd: launched.append(a))
    assert r["launched"] and launched[0][-1].endswith("run_daily.ps1")
    assert switch.stop(tmp_path, "sim")["ok"] and (tmp_path / "state" / "sim" / "stop").exists()
    (tmp_path / "state" / "sim" / "ext").mkdir(parents=True)
    (tmp_path / "state" / "sim" / "ext" / "owned.json").write_text('{"005930": {}}', encoding="utf-8")
    r = switch.start(tmp_path, "sim", now=mon, launcher=lambda a, cwd: launched.append(a))
    assert launched[1][-2:] == ["-Sim", "-Ext"] and not (tmp_path / "state" / "sim" / "stop").exists()
    fake_running(tmp_path, "paper")
    r = switch.start(tmp_path, "paper", now=mon, launcher=lambda a, cwd: launched.append(a))
    assert r["status"] == "running" and len(launched) == 2
    sat = datetime(2098, 3, 8, 10, 0)
    assert switch.start(tmp_path, "sim", now=sat, launcher=lambda a, cwd: launched.append(a))["status"] == "closed"
    assert len(launched) == 2


@pytest.mark.skipif(os.name != "nt", reason="run_daily.ps1 은 윈도우에서 띄운다")
def test_launcher_runs_ps1(tmp_path):
    """켜기가 쓰는 띄우기 함수로 진짜 powershell 스크립트를 돌려 본다.
    2026-11-26 장중: 콘솔 없는 프로세스(DETACHED_PROCESS)로 띄우면 powershell 이 스크립트를 돌리지 않고
    종료 코드 0 으로 끝나 '띄웠습니다' 뒤에 실행기가 뜨지 않았다 — 가짜 띄우기 함수 시험으로는 보이지 않던 결함."""
    ps1, out = tmp_path / "t.ps1", tmp_path / "ran.txt"
    ps1.write_text(f"'ok' | Out-File -FilePath '{out}' -Encoding ascii\n", encoding="utf-8-sig")   # BOM: PS 5.1 은 BOM 없는 파일을 ANSI 로 읽어 한글 경로가 깨진다
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(ps1)]
    switch.default_launcher(args, tmp_path)
    t0 = time.time()
    while not out.exists() and time.time() - t0 < 30:
        time.sleep(0.3)
    assert out.exists(), "띄운 powershell 이 스크립트를 돌리지 않았다"
