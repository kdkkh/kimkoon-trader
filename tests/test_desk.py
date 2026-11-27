"""통합 화면(desk/) 시험 — 모두 임시 폴더 · 이 PC 안 주소(127.0.0.1, 포트 8795 이상)에서, 바깥 API 없이. 서버는 시험마다 스스로 끝낸다."""
import http.client
import json
import random
import re
import shutil
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from control.common import log_path  # noqa: E402
from desk.core import tail  # noqa: E402
from desk.core.server import DeskServer  # noqa: E402

TODAY = datetime.now().strftime("%Y%m%d")
HOME_HINT = str(Path.home())


# ---------- 도우미 ----------
def free_port() -> int:
    for _ in range(200):
        p = random.randint(8795, 8999)
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("빈 포트 없음")


def cfg(**kw) -> dict:
    c = {"port": 0, "tabs": ["trade", "backtest"], "paths": {}, "fees": {}}
    c.update(kw)
    return c


@pytest.fixture
def desk(tmp_path):
    made = []

    def make(root=None, conf=None, **kw):
        root = root or tmp_path
        (root / "config.yaml").exists() or (root / "config.yaml").write_text(
            (ROOT / "config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        s = DeskServer(root, conf or cfg(), port=free_port(), **kw).start()
        made.append(s)
        return s
    yield make
    for s in made:
        s.stop()


def call(srv, method, path, body=None, token=True, host=None, origin=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=20)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host or f"127.0.0.1:{srv.port}")
    data = b""
    if body is not None:
        data = json.dumps(body).encode()
        c.putheader("Content-Type", "application/json")
    c.putheader("Content-Length", str(len(data)))
    if token is True:
        c.putheader("X-Desk-Token", srv.token)
    elif token:
        c.putheader("X-Desk-Token", token)
    if origin:
        c.putheader("Origin", origin)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    c.endheaders(data)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        obj = json.loads(raw.decode("utf-8"))
    except ValueError:
        obj = raw.decode("utf-8", "replace")
    return r.status, dict(r.getheaders()), obj, raw


def jl(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def sample_log(root: Path, target="paper", day=TODAY, sell=990.0):
    """견본 하루: 신호 2 · 주문 2 · 체결 사건 2(매수 · 매도) · 체결 끝 2 · 취소 1 — 주문번호 · raw · 사용자 폴더 경로가 섞여 있다."""
    t = 1_800_000_000_000
    at = f"{day[:4]}-{day[4:6]}-{day[6:]} 09:01:00.000"
    bt = f"{day[:4]}-{day[4:6]}-{day[6:]}T09:00:00+09:00"
    rows = [
        {"ts_ms": t, "at": at, "event": "state_restored", "found": True, "positions": {}, "realized": 0.0},
        {"ts_ms": t + 1, "at": at, "event": "start", "codes": ["005930", "000660"], "broker": "kis-paper", "mode": "paper-order",
         "amount": 10000.0, "order_mode": "limit"},
        {"ts_ms": t + 2, "at": at, "event": "bar", "code": "005930", "bar_time": bt, "close": 1000.0},
        {"ts_ms": t + 3, "at": at, "event": "signal", "code": "005930", "bar_time": bt, "kind": "long_entry", "t_signal_ms": t + 3},
        {"ts_ms": t + 4, "at": at, "event": "order_intent", "code": "005930", "side": "buy", "qty": 10, "mode": "limit",
         "price": 1000, "t_signal_ms": t + 3, "parent": ""},
        {"ts_ms": t + 5, "at": at, "event": "order_sent", "order_id": "a1", "odno": "0000002093", "orgno": "00950",
         "code": "005930", "side": "buy", "qty": 10, "t_signal_ms": t + 3, "msg": "모의투자 매수주문이 완료 되었습니다."},
        {"ts_ms": t + 6, "at": at, "event": "fill", "order_id": "a1", "odno": "0000002093", "code": "005930", "side": "buy",
         "qty": 10, "price": 1000.0, "cum_filled": 10},
        {"ts_ms": t + 7, "at": at, "event": "order_done", "order_id": "a1", "odno": "0000002093", "code": "005930",
         "side": "buy", "state": "filled", "qty": 10, "filled": 10, "avg": 1000.0,
         "raw": {"odno": "0000002093", "ord_gno_brno": "00950", "cano": "12345678"}},
        {"ts_ms": t + 8, "at": at, "event": "signal", "code": "000660", "bar_time": bt, "kind": "long_entry", "t_signal_ms": t + 8},
        {"ts_ms": t + 9, "at": at, "event": "order_intent", "code": "000660", "side": "buy", "qty": 1, "mode": "limit",
         "price": 5000, "t_signal_ms": t + 8, "parent": ""},
        {"ts_ms": t + 10, "at": at, "event": "order_sent", "order_id": "b2", "odno": "0000002094", "code": "000660",
         "side": "buy", "qty": 1, "t_signal_ms": t + 8},
        {"ts_ms": t + 11, "at": at, "event": "cancel_sent", "order_id": "b2", "odno": "0000002094", "cancel_odno": "0000002583",
         "code": "000660", "why": "기다림 상한", "remaining": 1},
        {"ts_ms": t + 12, "at": at, "event": "order_done", "order_id": "b2", "odno": "0000002094", "code": "000660",
         "side": "buy", "state": "cancelled", "qty": 1, "filled": 0, "avg": 0, "raw": {"odno": "0000002094"}},
        {"ts_ms": t + 13, "at": at, "event": "error", "error": f"FileNotFoundError: {HOME_HINT}\\secret\\x.yaml 없음"},
        {"ts_ms": t + 14, "at": at, "event": "fill", "order_id": "c3", "odno": "0000002095", "code": "005930", "side": "sell",
         "qty": 10, "price": sell, "cum_filled": 10},
        {"ts_ms": t + 15, "at": at, "event": "order_done", "order_id": "c3", "odno": "0000002095", "code": "005930",
         "side": "sell", "state": "filled", "qty": 10, "filled": 10, "avg": sell, "raw": {"odno": "0000002095"}},
        {"ts_ms": t + 16, "at": at, "event": "stop", "reason": "end",
         "summary": {"orders_done": {"filled": 2, "cancelled": 1}, "active": 0, "entries_today": 2,
                     "realized": (sell - 1000.0) * 10, "unrealized": 0, "positions": {}}},
    ]
    jl(log_path(root, target, day), rows)


def wait_job(srv, path, key, want, sec=30):
    end = time.time() + sec
    while time.time() < end:
        st, _, obj, _ = call(srv, "GET", path)
        if want(obj):
            return obj
        time.sleep(0.2)
    raise AssertionError(f"기다림 끝: {key}")


# ---------- 보호 ----------
def test_post_without_token_403(desk, tmp_path):
    s = desk()
    st, _, obj, _ = call(s, "POST", "/api/trade/stop", {"target": "paper"}, token=None)
    assert st == 403 and "토큰" in obj["error"]
    st, _, _, _ = call(s, "POST", "/api/trade/stop", {"target": "paper"}, token="x" * 32)
    assert st == 403
    assert not (tmp_path / "state" / "stop").exists()


def test_wrong_host_403(desk, tmp_path):
    s = desk()
    for m, body in (("GET", None), ("POST", {"target": "paper"})):
        st, _, obj, _ = call(s, m, "/api/trade/stop" if m == "POST" else "/api/tabs", body, host=f"evil.example:{s.port}")
        assert st == 403 and "Host" in obj["error"]
    st, _, _, _ = call(s, "GET", "/", host="127.0.0.1:1")
    assert st == 403
    st, _, _, _ = call(s, "GET", "/api/tabs", host=f"localhost:{s.port}")
    assert st == 200
    assert not (tmp_path / "state" / "stop").exists()


def test_other_origin_403(desk, tmp_path):
    s = desk()
    st, _, obj, _ = call(s, "POST", "/api/trade/stop", {"target": "paper"}, origin="http://evil.example")
    assert st == 403 and "다른 주소" in obj["error"]
    st, _, _, _ = call(s, "POST", "/api/trade/stop", {"target": "paper"}, origin=f"http://127.0.0.1:{s.port + 1}")
    assert st == 403
    st, _, obj, _ = call(s, "POST", "/api/trade/stop", {"target": "paper"}, origin=f"http://127.0.0.1:{s.port}")
    assert st == 200 and obj["ok"]


def test_security_headers(desk):
    s = desk()
    for path in ("/", "/api/tabs", "/static/common.js"):
        st, h, _, _ = call(s, "GET", path)
        assert st == 200 and h["X-Frame-Options"] == "DENY", path
        assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["X-Content-Type-Options"] == "nosniff"
    st, h, _, raw = call(s, "GET", "/tabs/trade/view.html")
    assert st == 200 and h["X-Frame-Options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in h["Content-Security-Policy"]
    assert s.token.encode() in raw and b"__DESK_TOKEN__" not in raw
    st, _, _, _ = call(s, "GET", "/tabs/trade/tab.py")                       # 파이썬 파일은 내지 않는다
    assert st == 404
    st, _, _, _ = call(s, "GET", "/static/../core/server.py")
    assert st == 404


def test_post_body_must_be_json(desk):
    s = desk()
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
    c.request("POST", "/api/trade/stop", body="target=paper",
              headers={"X-Desk-Token": s.token, "Content-Type": "application/x-www-form-urlencoded"})
    assert c.getresponse().status == 415
    c.close()


# ---------- 새 줄만 읽기 ----------
def test_tail_reads_only_new_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    jl(p, [{"event": "a"}, {"event": "b"}])
    t = tail.Tail(p)
    assert [e["event"] for e in t.read_new()] == ["a", "b"]
    assert t.read_new() == []
    half = json.dumps({"event": "한글줄"}, ensure_ascii=False).encode("utf-8")
    with p.open("ab") as f:                                                  # 한 줄 + 한글 가운데서 끊긴 반쪽 줄
        f.write(json.dumps({"event": "c"}).encode() + b"\n" + half[:9])
    assert [e["event"] for e in t.read_new()] == ["c"]
    with p.open("ab") as f:
        f.write(half[9:] + b"\n")
    assert [e["event"] for e in t.read_new()] == ["한글줄"]
    p.write_text(json.dumps({"event": "z"}) + "\n", encoding="utf-8")         # 잘림(작아짐) → 처음부터
    assert [e["event"] for e in t.read_new()] == ["z"]
    r = tail.read_from(p, 10_000)
    assert r["reset"] and [e["event"] for e in r["lines"]] == ["z"]
    assert tail.read_from(tmp_path / "없음.jsonl", 5)["exists"] is False


def test_events_api_cursor(desk, tmp_path):
    s = desk()
    sample_log(tmp_path)
    st, _, a, _ = call(s, "GET", f"/api/trade/events?target=paper&day={TODAY}")
    assert st == 200 and a["lines"] and all(x["event"] != "bar" for x in a["lines"])
    st, _, b, _ = call(s, "GET", f"/api/trade/events?target=paper&day={TODAY}&pos={a['pos']}")
    assert b["lines"] == [] and b["pos"] == a["pos"]
    jl(log_path(tmp_path, "paper", TODAY), [{"ts_ms": 1, "at": "x", "event": "risk_block", "code": "005930", "kind": "cap",
                                              "reason": "진입 상한"}])
    st, _, c, _ = call(s, "GET", f"/api/trade/events?target=paper&day={TODAY}&pos={a['pos']}")
    assert [x["cat"] for x in c["lines"]] == ["risk"]


# ---------- 탭 등록 ----------
def test_removed_tab_folder_rest_runs(desk, tmp_path):
    tabs = tmp_path / "tabs"
    shutil.copytree(ROOT / "desk" / "tabs", tabs, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(tabs / "trade")
    s = desk(conf=cfg(tabs=["trade", "backtest"]), tabs_dir=tabs)
    st, _, obj, _ = call(s, "GET", "/api/tabs")
    assert [t["id"] for t in obj["tabs"]] == ["backtest"]
    assert obj["skipped"] == [{"id": "trade", "why": "탭 폴더 없음"}]
    assert call(s, "GET", "/api/backtest/list")[2]["ok"] is True
    assert call(s, "GET", "/api/trade/status")[0] == 404
    assert call(s, "GET", "/tabs/backtest/view.html")[0] == 200


def test_missing_path_shows_no_data(desk, tmp_path):
    tabs = tmp_path / "tabs"
    (tabs / "fake").mkdir(parents=True)
    (tabs / "fake" / "tab.py").write_text(
        'ID = "fake"\nNAME = "가짜"\nPUBLIC = False\nNEEDS = ("autotrade_root",)\n'
        'def ping(ctx, req):\n    return {"ok": True}\nROUTES = {"GET ping": ping}\n', encoding="utf-8")
    (tabs / "broken").mkdir()
    (tabs / "broken" / "tab.py").write_text("raise RuntimeError('x')\n", encoding="utf-8")
    s = desk(conf=cfg(tabs=["fake", "broken"], paths={"autotrade_root": str(tmp_path / "없는폴더")}), tabs_dir=tabs)
    st, _, obj, _ = call(s, "GET", "/api/tabs")
    assert obj["tabs"][0]["ok"] is False and obj["tabs"][0]["message"] == "자료 없음: autotrade_root"
    assert obj["skipped"][0]["id"] == "broken" and "불러오기 실패" in obj["skipped"][0]["why"]
    st, _, obj, _ = call(s, "GET", "/api/fake/ping")
    assert st == 200 and obj["ok"] is False and obj["error"] == "자료 없음: autotrade_root"
    (tmp_path / "자료").mkdir()
    s2 = desk(conf=cfg(tabs=["fake"], paths={"autotrade_root": str(tmp_path / "자료")}), tabs_dir=tabs)
    assert call(s2, "GET", "/api/fake/ping")[2] == {"ok": True}


# ---------- 매매 탭 ----------
def test_summary_numbers_from_sample(desk, tmp_path):
    sample_log(tmp_path, "paper")
    sample_log(tmp_path, "sim", sell=1010.0)
    s = desk(conf=cfg(fees={"commission_pct": 0.015, "sell_tax_pct": 0.15}))
    st, _, obj, _ = call(s, "GET", f"/api/trade/summary?day={TODAY}")
    p, m = obj["runners"][0]["summary"], obj["runners"][1]["summary"]
    assert (p["signals"], p["intents"], p["sent"], p["fills"], p["done_filled"], p["cancelled"]) == (2, 2, 2, 2, 2, 1)
    assert p["realized"] == -100 and p["gross"] == -100 and p["holdings"] == []
    assert p["net"]["ok"] and p["net"]["value"] == round(-100 - 19_900 * 0.015 / 100 - 9_900 * 0.15 / 100)
    assert m["realized"] == 100
    s2 = desk(conf=cfg(fees={}))
    p2 = call(s2, "GET", f"/api/trade/summary?day={TODAY}")[2]["runners"][0]["summary"]
    assert p2["net"] == {"ok": False, "text": "율을 설정하면 계산", "source": ""}
    c = call(s, "GET", f"/api/trade/compare?day={TODAY}")[2]
    assert c["paired"] == 2 and c["both_filled"] == 1
    days = call(s, "GET", "/api/trade/days")[2]["days"]
    assert days[0] == {"day": TODAY, "label": f"{TODAY[:4]}-{TODAY[4:6]}-{TODAY[6:]}", "paper": True, "sim": True}


def test_settings_go_through_control(desk, tmp_path):
    s = desk()
    st, _, obj, _ = call(s, "POST", "/api/trade/settings/preview", {"changes": {"strategy": "strategy/ported/x.py"}})
    assert st == 200 and obj["ok"] is False and "등록된 이름만" in obj["message"]
    st, _, obj, _ = call(s, "POST", "/api/trade/settings/apply", {"changes": {"strategy": "strategy/ported/x.py"}})
    res = wait_job(s, f"/api/trade/settings/job?id={obj['job']}", "job", lambda o: o.get("state") == "done")["result"]
    assert res["status"] == "refused" and "등록된 이름만" in res["message"]
    before = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "strategy/ported/x.py" not in before
    obj = call(s, "POST", "/api/trade/settings/preview", {"changes": {"amount": "500,000"}})[2]
    assert obj["ok"] and obj["diff"] == {"amount": [1_000_000, 500_000]}
    obj = call(s, "POST", "/api/trade/settings/apply", {"changes": {"amount": "500,000"}})[2]
    res = wait_job(s, f"/api/trade/settings/job?id={obj['job']}", "job", lambda o: o.get("state") == "done")["result"]
    assert res["status"] == "saved" and res["text"] == "실행기가 꺼져 있어 다음 시작 때 반영"
    assert "amount: 500000" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    jr = [json.loads(x) for x in (tmp_path / "logs" / f"control_{TODAY}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["actor"] for r in jr if r["action"] == "settings_edit"] == ["desk", "desk"]
    view = call(s, "GET", "/api/trade/settings")[2]
    assert view["settings"]["amount"] == 500_000 and "amount" in view["editable"] and "tf" in view["locked"]
    assert view["recent"][0]["status"] == "saved"


def test_stop_creates_stop_file(desk, tmp_path):
    s = desk()
    st, _, obj, _ = call(s, "POST", "/api/trade/stop", {"target": "sim"})
    assert st == 200 and obj["ok"] and (tmp_path / "state" / "sim" / "stop").exists()
    assert call(s, "POST", "/api/trade/stop", {"target": "dry"})[0] == 400


def test_start_uses_injected_launcher(desk, tmp_path):
    seen = []
    s = desk(launcher=lambda args, cwd: seen.append(args), clock=lambda: datetime(2098, 11, 3, 10, 0))   # 휴장일 표에 없는 해의 월요일
    assert call(s, "POST", "/api/trade/start", {"target": "sim"})[0] == 400                          # 확인 없이 켜지 않음
    chk = call(s, "GET", "/api/trade/start_check?target=sim")[2]
    assert chk["status"] == "ready" and "시뮬" in chk["confirm"]
    st, _, obj, _ = call(s, "POST", "/api/trade/start", {"target": "sim", "confirm": True})
    assert st == 200 and obj["status"] == "launched" and len(seen) == 1 and "-Sim" in seen[0]
    s2 = desk(launcher=lambda args, cwd: seen.append(args), clock=lambda: datetime(2098, 11, 3, 7, 0))
    obj = call(s2, "POST", "/api/trade/start", {"target": "paper", "confirm": True})[2]
    assert obj["status"] == "closed" and len(seen) == 1


def test_no_raw_order_no_or_home_path(desk, tmp_path):
    sample_log(tmp_path, "paper")
    sample_log(tmp_path, "sim")
    s = desk()
    paths = ["/api/trade/status", "/api/trade/days", f"/api/trade/summary?day={TODAY}",
             f"/api/trade/events?target=paper&day={TODAY}&bars=1", f"/api/trade/events?target=sim&day={TODAY}",
             f"/api/trade/compare?day={TODAY}", "/api/trade/settings", "/api/backtest/list", "/api/backtest/options",
             f"/api/backtest/detail?kind=sim&id=logs/sim/strategy_live_{TODAY}.jsonl", "/api/tabs", "/"]
    seen_error = False
    for p in paths:
        st, _, obj, raw = call(s, "GET", p)
        txt = raw.decode("utf-8")
        assert st == 200, p
        for bad in ('"raw"', "odno", "orgno", "0000002093", "0000002583", "12345678", HOME_HINT,
                    HOME_HINT.replace("\\", "\\\\"), str(tmp_path).replace("\\", "\\\\")):
            assert bad not in txt, (p, bad)
        seen_error |= "FileNotFoundError: ~" in txt
    assert seen_error                                                         # 오류 글은 남되 경로는 '~' 로


def test_round_trips_csv_with_bom(desk, tmp_path):
    d = tmp_path / "data" / "bt_test"
    (d / "all").mkdir(parents=True)
    (d / "round_trips.csv").write_bytes("﻿code,t_entry_ms,t_exit_ms,qty,entry,exit,pnl,ret_pct\n"
                                        "005380,1794355273115,1794355990885,10,277500.0,274000.0,-35000.0,-1.26\n"
                                        "005930,1794358148070,1794359348304,15,197950.0,198900.0,14250.0,0.48\n".encode("utf-8"))
    jl(d / "all" / "strategy_live_20261112.jsonl",
       [{"ts_ms": 1, "at": "2026-11-12 08:27:36.624", "event": "start", "day": "20261111", "mode": "backtest"},
        {"ts_ms": 2, "at": "2026-11-12 08:27:36.640", "event": "bar", "code": "005930", "bar_time": "2026-11-11T09:00:00+09:00"}])
    s = desk()
    lst = call(s, "GET", "/api/backtest/list")[2]
    it = [x for x in lst["fill"] if x["id"] == "data/bt_test"][0]
    assert (it["trades"], it["pnl"], it["trade_day"], it["run_day"]) == (2, -20750, "2026-11-11", "2026-11-12")
    det = call(s, "GET", "/api/backtest/detail?kind=fill&id=data/bt_test")[2]
    assert det["totals"]["trades"] == 2 and det["totals"]["pnl"] == -20750 and det["trades"][0]["code"] == "005380"
    assert det["trade_days"] == ["2026-11-11"] and det["run_day"] == "2026-11-12"
    assert call(s, "GET", "/api/backtest/detail?kind=fill&id=../../x")[0] == 404


def test_child_failure_keeps_server(desk, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "strategy_validate.py").write_text(
        "import sys\nprint('검증 시작', __file__)\nraise SystemExit(3)\n", encoding="utf-8")
    s = desk()
    assert call(s, "POST", "/api/backtest/run", {"kind": "validate", "strategy": "strategy/ported/x.py"})[0] == 400
    st, _, obj, _ = call(s, "POST", "/api/backtest/run", {"kind": "validate", "strategy": "supertrend"})
    assert st == 200 and obj["status"] == "running"
    jobs = wait_job(s, "/api/backtest/jobs", "jobs", lambda o: o["jobs"] and o["jobs"][0]["state"] != "running")["jobs"]
    assert jobs[0]["state"] == "failed" and jobs[0]["code"] == 3 and "검증 시작" in jobs[0]["output"]
    assert str(tmp_path) not in jobs[0]["output"] and HOME_HINT not in jobs[0]["output"]
    assert call(s, "GET", "/api/tabs")[0] == 200
    s2 = desk(python=str(tmp_path / "없는_파이썬.exe"))
    st, _, obj, _ = call(s2, "POST", "/api/backtest/run", {"kind": "validate", "strategy": "supertrend"})
    assert st == 500 and obj["status"] == "error" and "띄우지 못함" in obj["message"]
    assert call(s2, "GET", "/api/backtest/jobs")[2]["jobs"][0]["state"] == "error"
    assert call(s2, "GET", "/api/trade/status")[0] == 200


def test_fetch_run_needs_code(desk, tmp_path):
    """한투 조회 백테스트: 종목 칸이 비었거나 6자리 숫자가 아니면 자식 프로세스를 띄우지 않고 까닭을 답한다(기본 종목 없음)."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "strategy_backtest.py").write_text("print('ok')\n", encoding="utf-8")
    s = desk()
    for code in ("", "12345", "abcdef"):
        st, _, obj, _ = call(s, "POST", "/api/backtest/run",
                             {"kind": "bar_fetch", "strategy": "supertrend", "code": code, "days": 30, "confirm": True})
        assert st == 400 and "종목 코드를 넣어 주세요" in obj["message"]
    assert call(s, "GET", "/api/backtest/jobs")[2]["jobs"] == []
    html = (ROOT / "desk" / "tabs" / "backtest" / "view.html").read_text(encoding="utf-8")
    m = re.search(r'<input[^>]*id="r-code"[^>]*>', html)
    assert m and 'value=""' in m.group(0) and "placeholder" in m.group(0)                 # 입력칸에 기본 종목을 두지 않는다


def test_fetch_run_blocked_while_running(desk, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "strategy_backtest.py").write_text("print('ok')\n", encoding="utf-8")
    st_dir = tmp_path / "state" / "sim"
    st_dir.mkdir(parents=True)
    (st_dir / "heartbeat.json").write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "broker": "sim"}),
                                           encoding="utf-8")
    s = desk()
    body = {"kind": "bar_fetch", "strategy": "supertrend", "code": "005930", "days": 30}
    assert call(s, "POST", "/api/backtest/run", body)[0] == 400                                       # 확인 창 없이
    st, _, obj, _ = call(s, "POST", "/api/backtest/run", {**body, "confirm": True})
    assert st == 409 and "실행기가 돌고 있어" in obj["message"]
    assert call(s, "POST", "/api/backtest/run", {"kind": "bar_csv", "strategy": "supertrend", "csv": "../x.csv"})[0] == 400
