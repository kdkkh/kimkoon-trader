"""토큰 캐시 쓰기 · 읽기 경합 시험 — 한 곳이 캐시를 쓰는 도중 다른 곳이 읽어도 반쪽 파일을 보지 않아야 한다.

막힌 자리: 캐시를 그 자리에서 덮어쓰면(파일을 비운 뒤 씀) 그 사이에 읽는 쪽이 빈 파일 · 반쪽 JSON 을 읽고
'토큰 캐시를 읽을 수 없습니다'로 멈춘다(실행기 · 판단 · 봇처럼 같은 캐시를 쓰는 프로세스가 늘수록 잦아짐).
그래서 쓰기는 임시 파일 → os.replace, 읽기는 깨진 파일을 만나면 짧게 다시 읽는다.
실제 한투 서버 · 실제 캐시 파일은 쓰지 않는다(가짜 설정 + 가짜 HTTP + 임시 폴더).
"""
import json
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from kis import auth

FMT = "%Y-%m-%d %H:%M:%S"


def _cfg(tmp_path):
    return SimpleNamespace(mode="real", is_paper=False, token_file=tmp_path / "tk_p.json",
                           rest_url="https://example.invalid", app_key="K", app_secret="S", user_agent="test")


class _Resp:
    status_code = 200

    def __init__(self, tok, minutes):
        self._b = {"access_token": tok,
                   "access_token_token_expired": (datetime.now() + timedelta(minutes=minutes)).strftime(FMT)}

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    auth._memory.clear()
    monkeypatch.setattr(auth, "_sleep", lambda s: None)
    yield
    auth._memory.clear()


def test_half_written_cache_is_read_again(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    full = json.dumps({"tok": "T", "exp": (datetime.now() + timedelta(hours=1)).strftime(FMT)})
    cfg.token_file.write_text(full[:9], encoding="utf-8")                  # 다른 곳이 쓰는 도중의 반쪽

    def other_finishes(_s):
        cfg.token_file.write_text(full, encoding="utf-8")
    monkeypatch.setattr(auth, "_sleep", other_finishes)
    tok, exp = auth._read_cache(cfg)
    assert tok == "T"


def test_broken_cache_stops_no_path(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.token_file.write_text("{깨짐", encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        auth._read_cache(cfg)
    assert "tk_p.json" in str(e.value) and str(tmp_path) not in str(e.value)   # 사용자 폴더 경로를 내지 않음


def test_issue_replaces_file_no_tmp_left(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.token_file.write_text(json.dumps({"tok": "OLD", "exp": "2000-01-01 00:00:00"}), encoding="utf-8")
    seen = []
    real_replace = auth.os.replace
    monkeypatch.setattr(auth.os, "replace", lambda a, b: seen.append((a, b)) or real_replace(a, b))
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: _Resp("NEW", 60))
    tok, _ = auth._issue(cfg)
    assert tok == "NEW" and json.loads(cfg.token_file.read_text(encoding="utf-8"))["tok"] == "NEW"
    assert len(seen) == 1 and str(seen[0][1]) == str(cfg.token_file)
    assert not list(tmp_path.glob("*.tmp"))


def test_writer_and_readers_together(tmp_path, monkeypatch):
    """한 곳이 캐시를 계속 새로 쓰는 동안 셋이 계속 읽는다 — 읽기 오류가 한 번도 나면 안 된다."""
    cfg = _cfg(tmp_path)
    cfg.token_file.write_text(json.dumps({"tok": "T0", "exp": "2000-01-01 00:00:00"}), encoding="utf-8")
    n = {"i": 0}

    def post(*a, **k):
        n["i"] += 1
        return _Resp("T" + "x" * (n["i"] % 50), 60 + n["i"])              # 길이가 매번 달라 반쪽이면 JSON 이 깨진다
    monkeypatch.setattr(auth.requests, "post", post)
    done = threading.Event()
    errors, reads = [], [0]

    def reader():
        while not done.is_set():
            try:
                got = auth._read_cache(cfg)
                assert got and got[0].startswith("T")
                reads[0] += 1
            except Exception as e:                                         # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
            time.sleep(0.001)                                              # 실제 읽는 쪽도 쉬지 않고 붙잡지는 않는다

    ts = [threading.Thread(target=reader) for _ in range(3)]
    for t in ts:
        t.start()
    try:
        for _ in range(60):
            auth._issue(cfg)
    finally:
        done.set()
        for t in ts:
            t.join()
    assert not errors, errors[:3]
    assert reads[0] > 0 and not list(tmp_path.glob("*.tmp"))
