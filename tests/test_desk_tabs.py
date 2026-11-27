"""통합 화면의 탭(AI 판단 · 공시 · 뉴스) 시험 — 공개판에도 가는 몫(내 화면 전용 조각 시험은 test_desk_mine.py) — 모두 임시 폴더 · 가짜 자료 · 127.0.0.1(포트 8805 이상),
바깥 API · 바깥 사이트 접속 없음. 서버 · 재생기는 시험마다 스스로 끝낸다. 키 폴더(KIS_CONFIG_DIR)는 빈 임시 폴더."""
import http.client
import json
import random
import shutil
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desk.core.server import DeskServer  # noqa: E402

TODAY = datetime.now().strftime("%Y%m%d")
TABS4 = ["ai", "disclosure", "news", "browser"]
FAKE_KEY = "QWERTYfakekey1234567890abcdefXYZ"


# ---------- 도우미 ----------
def free_port(lo=8805, hi=8999) -> int:
    for _ in range(300):
        p = random.randint(lo, hi)
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("빈 포트 없음")


def call(srv, method, path, body=None, token=True, host=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=60)
    c.putrequest(method, quote(path, safe="/?&=%:+,."), skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host or f"127.0.0.1:{srv.port}")
    data = b""
    if body is not None:
        data = json.dumps(body).encode()
        c.putheader("Content-Type", "application/json")
    c.putheader("Content-Length", str(len(data)))
    if token:
        c.putheader("X-Desk-Token", srv.token)
    c.endheaders(data)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        obj = json.loads(raw.decode("utf-8"))
    except ValueError:
        obj = raw.decode("utf-8", "replace")
    return r.status, obj, raw


def jl(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def csv_write(p: Path, header: list[str], rows: list[list], bom=False):
    import csv
    import io
    p.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    p.write_text(("﻿" if bom else "") + buf.getvalue(), encoding="utf-8")


def make_root(tmp: Path) -> Path:
    root = tmp / "trader"
    (root / "judgments").mkdir(parents=True)
    from control.settings_edit import set_keys                 # 예시 설정의 종목은 판마다 다르다 — 시험은 종목을 정해 둔다
    text = set_keys((ROOT / "config.example.yaml").read_text(encoding="utf-8"), {"codes": ["005930"]})
    (root / "config.yaml").write_text(text, encoding="utf-8")
    for f in (ROOT / "judgments").glob("*.example.yaml"):
        shutil.copy(f, root / "judgments" / f.name)
    return root


@pytest.fixture
def env(tmp_path, monkeypatch):
    keys = tmp_path / "keys"
    keys.mkdir()
    monkeypatch.setenv("KIS_CONFIG_DIR", str(keys))
    made = []

    def start(paths=None, tabs=None, tabs_dir=None, **cfg):
        root = make_root(tmp_path) if not (tmp_path / "trader").exists() else tmp_path / "trader"
        conf = {"port": 0, "tabs": tabs or (["trade"] + TABS4), "paths": paths or {}, "fees": {}, **cfg}
        s = DeskServer(root, conf, port=free_port(), tabs_dir=tabs_dir, python=sys.executable).start()
        made.append(s)
        return s
    yield start, tmp_path, keys
    for name, m in list(sys.modules.items()):              # 재생기가 떠 있으면 끈다
        if name.startswith("desk_") and name.endswith("mine_replay") and getattr(m, "PLAYER", None) is not None:
            m.PLAYER.stop()
    for s in made:
        s.stop()


def wait_job(srv, path, sec=60):
    end = time.time() + sec
    while time.time() < end:
        _, obj, _ = call(srv, "GET", path)
        j = obj.get("job") if isinstance(obj, dict) else None
        if j and j["state"] != "running":
            return j
        time.sleep(0.3)
    raise AssertionError("작업이 끝나지 않음")


def need(*files):
    """내 화면 전용 파일이 없는 판(공개판 조립본)에서는 그 시험을 건너뛴다."""
    miss = [f for f in files if not (ROOT / "desk" / "tabs" / f).is_file()]
    if miss:
        pytest.skip("내 화면 전용 파일 없음(공개판): " + ", ".join(miss))


ALL_MINE = ("ai/mine_ep09.py", "disclosure/mine_replay.py", "disclosure/mine_builder.py", "disclosure/mine_dd02.py",
            "disclosure/mine_dash.py", "news/mine_compare.py", "browser/tab.py")


# ---------- 시험 ----------
def test_no_paths_no_mine_sections(env):
    start, _, _ = env
    srv = start()
    for path in ("/api/ai/overview", "/api/disclosure/info", "/api/news/info"):
        st, obj, _ = call(srv, "GET", path)
        assert st == 200 and obj["ok"] and obj["mine"] == {}
    for path in ("/api/ai/ep09", "/api/news/compare", "/api/disclosure/dash", "/api/disclosure/replay",
                 "/api/disclosure/builder", "/api/disclosure/dd02"):
        st, obj, _ = call(srv, "GET", path)
        assert st == 404 and obj["ok"] is False
    st, obj, _ = call(srv, "GET", "/api/tabs")
    b = next((t for t in obj["tabs"] if t["id"] == "browser"), None)
    if (ROOT / "desk" / "tabs" / "browser" / "tab.py").is_file():
        assert b["ok"] is False and b["message"] == "자료 없음: browser_root"
    else:                                                    # 공개판: 브라우저 탭 폴더가 없다
        assert b is None and any(s["id"] == "browser" for s in obj["skipped"])


def test_key_missing_messages(env):
    start, tmp, _ = env
    root = make_root(tmp)
    txt = (root / "judgments" / "market_regime.example.yaml").read_text(encoding="utf-8")
    (root / "judgments" / "regime_real.yaml").write_text(
        txt.replace("id: market_regime", "id: regime_real").replace("provider: fake", "provider: anthropic"), encoding="utf-8")
    srv = start()
    st, obj, _ = call(srv, "POST", "/api/disclosure/query", {"code": "005930", "confirm": True})
    assert obj["ok"] is False and obj["key_missing"] and obj["message"] == "키 없음: opendart.yaml 을 설정 폴더에"
    st, obj, _ = call(srv, "POST", "/api/news/query", {"code": "005930", "n": 5, "confirm": True})
    assert obj["ok"] is False and obj["key_missing"] and "키 없음" in obj["message"]
    st, obj, _ = call(srv, "POST", "/api/ai/ask", {"judge": "regime_real", "mode": "real", "confirm": True})
    assert st == 400 and obj["key_missing"] and obj["message"] == "키 없음: anthropic.yaml 을 설정 폴더에"
    st, obj, _ = call(srv, "POST", "/api/ai/ask", {"judge": "market_regime", "mode": "real", "confirm": True})
    assert st == 400 and "실제 호출이 아닙니다" in obj["message"]
    _, ov, _ = call(srv, "GET", "/api/ai/overview")
    assert ov["keys"]["anthropic"] is False
    _, di, _ = call(srv, "GET", "/api/disclosure/info")
    _, ni, _ = call(srv, "GET", "/api/news/info")
    assert di["key"] is False and ni["key"] is False and di["default_code"] == "005930"


def test_query_uses_injected_fetch(env):
    start, _, _ = env
    srv = start()
    seen = []

    def fake_disc(codes, cap):
        seen.append((codes, cap))
        return [{"rcept_no": "20261123000001", "rcept_dt": "20261123", "corp_name": "가나회사", "stock_code": "005930",
                 "report_nm": "주요사항보고서(자기주식취득결정)", "flr_nm": "가나회사", "rm": "유",
                 "viewer_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261123000001", "source": "x"}]

    def fake_news(code, n):
        return [{"srno": "1", "date": "20261123", "time": "090102", "provider_code": "2", "provider": "가짜통신",
                 "codes": [code], "title": "가짜 제목 하나"}]
    srv.ctx.store["disclosure_fetch"] = fake_disc
    srv.ctx.store["news_fetch"] = fake_news
    st, obj, _ = call(srv, "POST", "/api/disclosure/query", {"code": "005930", "confirm": True})
    assert st == 200 and obj["ok"] and obj["rows"][0]["report_nm"].startswith("주요사항") and obj["rows"][0]["url"]
    assert obj["source"] == "금융감독원 전자공시(OpenDART)" and seen == [(["005930"], 500)]
    assert obj["cap"] == 500 and obj["calls_today"] == 0
    st, obj, _ = call(srv, "POST", "/api/news/query", {"code": "005930", "n": 3, "confirm": True})
    assert obj["ok"] and obj["rows"][0]["title"] == "가짜 제목 하나" and obj["rows"][0]["at"] == "2026-11-23 09:01:02"
    st, obj, raw = call(srv, "POST", "/api/news/query?mask=1", {"code": "005930", "n": 3, "confirm": True})
    assert "가짜 제목 하나".encode() not in raw and "가짜통신".encode() not in raw
    st, obj, raw = call(srv, "POST", "/api/disclosure/query?mask=1", {"code": "005930", "confirm": True})
    assert "가나회사".encode() not in raw and obj["rows"][0]["corp_name"] == "회사 1"


def test_query_only_by_post_with_token(env):
    start, _, _ = env
    srv = start()
    srv.ctx.store["disclosure_fetch"] = lambda codes, cap: []
    srv.ctx.store["news_fetch"] = lambda code, n: []
    for tab in ("disclosure", "news"):
        assert call(srv, "GET", f"/api/{tab}/query")[0] == 404
        assert call(srv, "POST", f"/api/{tab}/query", {"code": "005930", "confirm": True}, token=False)[0] == 403
        st, obj, _ = call(srv, "POST", f"/api/{tab}/query", {"code": "005930"})
        assert st == 400 and "확인 창" in obj["message"]
        st, obj, _ = call(srv, "POST", f"/api/{tab}/query", {"code": "../x", "confirm": True})
        assert st == 400
    assert call(srv, "POST", "/api/ai/ask", {"judge": "market_regime", "mode": "fake", "confirm": True}, token=False)[0] == 403


def test_ai_overview_lists_switches(env):
    start, tmp, _ = env
    root = make_root(tmp)
    (root / "judgments" / "broken.yaml").write_text("id: broken\nmode: real\n", encoding="utf-8")
    srv = start()
    st, obj, _ = call(srv, "GET", "/api/ai/overview")
    assert st == 200 and obj["mark"] == "AI 판단"
    ids = {d["id"]: d for d in obj["defs"]}
    assert set(ids) == {"market_regime", "event_watch", "disclosure_check"} and all(d["example"] for d in ids.values())
    assert ids["market_regime"]["calls_today"] == 0 and ids["market_regime"]["last"] is None
    assert any(e["file"] == "broken.yaml" for e in obj["def_errors"])
    sw = {s["key"]: s["on"] for s in obj["switches"]}
    assert sw == {"bars_raw": False, "news_titles": False, "disclosures": True, "signals": True, "positions": True,
                  "text": True, "browser": False, "record_titles": False}
    g = obj["global"]
    assert g["file"] == "" and "judge.yaml 없음" in g["file_note"] and g["ai_enabled"] is False and g["ack"] is False
    assert obj["process"]["running"] is False and [d["target"] for d in obj["doors"]] == ["paper", "sim"]
    assert ids["market_regime"]["when"] == ["08:55 한 번", "30분마다(09:00~15:00)"]
    text = json.dumps(obj, ensure_ascii=False)
    for word in ("매수하세요", "매도하세요", "사세요", "파세요"):
        assert word not in text


def test_ai_records_tail_and_detail(env):
    start, tmp, _ = env
    root = make_root(tmp)
    t = int(time.time() * 1000)
    title = "가짜 뉴스 제목 원문 하나"
    rec = {"ts_ms": t, "at": "2026-11-23 09:00:00.000", "event": "judge", "ref": "market_regime-1-ab", "judge": "market_regime",
           "def_hash": "x", "trigger": {"kind": "every_min", "codes": []},
           "inputs": [{"kind": "news_titles", "n": 1, "items": [{"srno": "77", "time": "x", "hash": "h", "title": title}]}],
           "notes": [], "system": "시스템", "user": f"자료 {title} 질문", "raw": f'{{"answer": "risk_off", "reason": "{title}"}}',
           "answer": {"answer": "risk_off", "reason": f"{title} 때문"}, "valid": True, "invalid_reason": "",
           "outputs": [{"kind": "gate", "status": "dry"}], "provider": "fake", "model": {"provider": "fake", "name": "m"},
           "calls": 1, "cost_est": 0.0, "fake": True, "dry": True}
    p = root / "logs" / f"judge_{TODAY}.jsonl"
    jl(p, [rec, {"ts_ms": t + 1, "at": "2026-11-23 09:00:01.000", "event": "skip", "judge": "event_watch",
                 "trigger": {"kind": "new_news"}, "reason": "news_titles: AI 로 보내기 스위치 꺼짐"}])
    srv = start()
    st, obj, _ = call(srv, "GET", f"/api/ai/records?day={TODAY}&pos=0")
    assert [r["event"] for r in obj["rows"]] == ["judge", "skip"] and obj["rows"][0]["answer"] == "risk_off"
    pos = obj["pos"]
    jl(p, [dict(rec, ref="market_regime-2-cd", ts_ms=t + 2)])
    st, obj, _ = call(srv, "GET", f"/api/ai/records?day={TODAY}&pos={pos}&n0=2")
    assert len(obj["rows"]) == 1 and obj["rows"][0]["ref"] == "market_regime-2-cd" and obj["rows"][0]["n"] == 2
    st, obj, raw = call(srv, "GET", f"/api/ai/record?day={TODAY}&ref=market_regime-1-ab")
    d = obj["record"]
    assert d["answer"]["answer"] == "risk_off" and d["outputs"][0]["status"] == "dry" and d["question"].startswith("위 자료")
    assert title in d["prompt"] and "원문이 있음" in d["titles_note"]
    st, obj, raw = call(srv, "GET", f"/api/ai/record?day={TODAY}&ref=market_regime-1-ab&mask=1")
    assert title.encode() not in raw and "#77" in obj["record"]["raw_answer"] and "가림 모드" in obj["record"]["prompt"]
    st, ov, _ = call(srv, "GET", "/api/ai/overview")
    mr = next(x for x in ov["defs"] if x["id"] == "market_regime")
    assert mr["calls_today"] == 2 and mr["last"]["answer"] == "risk_off"


def test_ai_ask_fake_runs_child(env):
    start, tmp, _ = env
    srv = start()
    st, obj, _ = call(srv, "POST", "/api/ai/ask", {"judge": "disclosure_check", "mode": "fake", "codes": ["005930"]})
    assert st == 400                                             # 확인 창 없이는 안 됨
    st, obj, _ = call(srv, "POST", "/api/ai/ask", {"judge": "disclosure_check", "mode": "fake", "codes": ["005930"], "confirm": True})
    assert st == 200 and obj["ok"], obj
    j = wait_job(srv, "/api/ai/jobs", 90)
    assert j["state"] == "done", j
    recs = [json.loads(x) for x in (tmp / "trader" / "logs" / f"judge_{TODAY}.jsonl").read_text(encoding="utf-8").splitlines() if x]
    jr = [r for r in recs if r.get("event") == "judge" and r.get("judge") == "disclosure_check"]
    assert jr and jr[-1]["fake"] is True and jr[-1]["dry"] is True
    assert not (tmp / "trader" / "state" / "ext").exists()        # --dry: 문에 아무것도 쓰지 않음
    _, obj, _ = call(srv, "GET", f"/api/ai/records?day={TODAY}")
    assert any(r["judge"] == "disclosure_check" and r["event"] == "judge" for r in obj["rows"])


