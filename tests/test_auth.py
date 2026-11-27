"""토큰 발급 — 여러 프로세스가 같은 순간 만료를 볼 때(첫 봉에서 동시에 발급하다 EGW00133 으로 거절된 결함) 시험.

실제 한투 서버·실제 캐시 파일은 쓰지 않는다(가짜 설정 + 임시 폴더).
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from kis import auth

FMT = "%Y-%m-%d %H:%M:%S"


def _cfg(tmp_path):
    return SimpleNamespace(mode="real", is_paper=False, token_file=tmp_path / "tk_p.json",
                           rest_url="https://example.invalid", app_key="K", app_secret="S", user_agent="test")


def _write(cfg, tok, minutes):
    exp = datetime.now() + timedelta(minutes=minutes)
    cfg.token_file.write_text(json.dumps({"tok": tok, "exp": exp.strftime(FMT)}), encoding="utf-8")


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    auth._memory.clear()
    monkeypatch.setattr(auth, "_sleep", lambda s: None)
    yield
    auth._memory.clear()


def _ok(tok="NEW"):
    exp = (datetime.now() + timedelta(hours=24)).strftime(FMT)
    return _Resp(200, {"access_token": tok, "access_token_token_expired": exp})


RATE = _Resp(403, {"error_code": "EGW00133", "error_description": "1분당 1회"})


def test_reuse_cache_without_issue(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _write(cfg, "CACHED", 60)
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: pytest.fail("발급하면 안 됨"))
    assert auth.get_token(cfg, verbose=False) == "CACHED"


def test_expired_issues_once_and_unlocks(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _write(cfg, "OLD", -5)
    calls = []
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: calls.append(1) or _ok())
    assert auth.get_token(cfg, verbose=False) == "NEW"
    assert len(calls) == 1
    assert not cfg.token_file.with_suffix(".lock").exists()
    assert json.loads(cfg.token_file.read_text(encoding="utf-8"))["tok"] == "NEW"


def test_waiter_uses_cache_written_later(tmp_path, monkeypatch):
    """처음 읽을 땐 만료였지만, 잠금을 얻고 다시 읽으니 다른 프로세스가 받아 둔 캐시 → 발급하지 않음."""
    cfg = _cfg(tmp_path)
    _write(cfg, "OLD", -5)
    real_read = auth._read_cache
    n = {"i": 0}

    def read(c):
        n["i"] += 1
        if n["i"] == 2:                           # 잠금 안에서 다시 읽기 직전에 다른 프로세스가 써 넣음
            _write(c, "FROM_OTHER", 60 * 24)
        return real_read(c)

    monkeypatch.setattr(auth, "_read_cache", read)
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: pytest.fail("발급하면 안 됨"))
    assert auth.get_token(cfg, verbose=False) == "FROM_OTHER"


def test_rate_limited_waits_for_cache(tmp_path, monkeypatch):
    """EGW00133 → 캐시가 새로 써지기를 기다렸다 그것을 씀(두 번째 발급 없음)."""
    cfg = _cfg(tmp_path)
    _write(cfg, "OLD", -5)
    calls = []

    def post(*a, **k):
        calls.append(1)
        _write(cfg, "ELSEWHERE", 60 * 24)          # 거절되는 그 순간 다른 곳이 받아 캐시에 씀
        return RATE

    monkeypatch.setattr(auth.requests, "post", post)
    assert auth.get_token(cfg, verbose=False) == "ELSEWHERE"
    assert len(calls) == 1


def test_rate_limited_retries_once(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _write(cfg, "OLD", -5)
    seq = [RATE, _ok("RETRY")]
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: seq.pop(0))
    assert auth.get_token(cfg, verbose=False) == "RETRY"
    assert seq == []


def test_other_errors_still_raise(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _write(cfg, "OLD", -5)
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: _Resp(401, {"error_code": "EGW00103"}))
    with pytest.raises(RuntimeError, match="EGW00103"):
        auth.get_token(cfg, verbose=False)
    assert not cfg.token_file.with_suffix(".lock").exists()


def test_stale_lock_is_removed(tmp_path, monkeypatch):
    import os
    import time
    cfg = _cfg(tmp_path)
    _write(cfg, "OLD", -5)
    lock = cfg.token_file.with_suffix(".lock")
    lock.write_text("99999")
    old = time.time() - auth.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: _ok())
    assert auth.get_token(cfg, verbose=False) == "NEW"
    assert not lock.exists()


def test_warm_never_raises(monkeypatch):
    from kis import config
    monkeypatch.setattr(config, "load", lambda mode: (_ for _ in ()).throw(FileNotFoundError("없음")))
    out = []
    assert auth.warm("real", printer=out.append) is False
    assert "미리 받기 실패" in out[0]
