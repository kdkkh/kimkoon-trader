"""접근 토큰 — 캐시 재사용이 원칙.

한국투자 안내: "접근 토큰은 1일 1회 발급 원칙, 유효기간(24시간) 안에 잦은 발급 시 이용 제한".
- 캐시(~/KIS/config/tk_v.json·tk_p.json, 형식 {"tok", "exp"})가 있고 만료 전이면 그대로 쓴다.
- 만료 시각이 지났을 때만 새로 발급한다. 캐시 파일이 깨져 있으면 덮어쓰지 않고 멈춘다.
- 토큰 값은 화면·로그에 출력하지 않는다.
- 같은 실행 안에서도 만료 시각이 지나면 캐시 파일을 다시 읽고(다른 프로세스가 먼저 새로 받았을 수 있음),
  그래도 만료면 새로 발급한다(하루 종일 도는 프로그램이 아침에 받은 토큰을 만료 뒤에도 들고 있던 결함).
- 여러 프로세스가 같은 순간 만료를 보면 둘 다 발급을 시도해 늦은 쪽이 'EGW00133 1분당 1회'로 거절됐다
  (모의 · 시뮬레이션 실행기가 첫 봉에서 동시에). 그래서
  ① 발급은 잠금 파일(캐시 옆 *.lock) 안에서만 하고, 잠금을 얻은 뒤 캐시를 한 번 더 읽는다(먼저 받은 쪽 것을 씀).
  ② 그래도 EGW00133 이면 다른 곳(잠금 밖 프로그램)이 막 받은 것이므로 캐시가 새로 써지기를 최대 65초 기다리고,
     없으면 한 번만 다시 발급한다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime

import requests

from .config import KisConfig

_FMT = "%Y-%m-%d %H:%M:%S"
_memory: dict[str, tuple[str, datetime]] = {}   # mode → (토큰, 만료시각). 만료 전까지는 파일도 다시 읽지 않는다
_sleep = time.sleep                              # 시험에서 바꿔 끼운다
LOCK_WAIT_S = 30.0                               # 잠금을 이만큼 기다려도 못 얻으면 잠금 없이 진행(발급은 몇 초면 끝남)
LOCK_STALE_S = 90.0                              # 이보다 오래된 잠금 파일은 죽은 프로세스가 남긴 것으로 보고 치운다
RATE_WAIT_S = 65.0                               # EGW00133(1분당 1회) 뒤 캐시를 기다리는 최대 시간


READ_RETRY = 3                                   # 캐시를 못 읽으면 이만큼 더 읽어 본다(다른 프로세스가 바꿔 넣는 순간일 수 있음)
READ_RETRY_GAP_S = 0.2


def _read_cache(cfg: KisConfig) -> tuple[str, datetime] | None:
    f = cfg.token_file
    for attempt in range(READ_RETRY + 1):
        if not f.exists():
            return None
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            return d["tok"], datetime.strptime(d["exp"], _FMT)
        except Exception:
            if attempt < READ_RETRY:
                _sleep(READ_RETRY_GAP_S)
                continue
    # 짧게 다시 읽어도 깨져 있으면 진짜 깨진 것 — 함부로 지우고 새로 받으면 '잦은 발급'이 될 수 있다 → 사람이 확인
    raise RuntimeError(f"토큰 캐시를 읽을 수 없습니다: {f.name} (자동 재발급하지 않음)")


def _write_cache(cfg: KisConfig, tok: str, exp: datetime) -> None:
    """임시 파일에 다 쓴 뒤 os.replace 로 바꿔 넣는다 — 다른 프로세스가 쓰는 도중의 반쪽 파일을 읽지 않게.
    윈도우에서는 상대가 캐시를 열어 둔 순간 바꿔 넣기가 PermissionError 로 실패하므로 잠깐씩 다시 한다."""
    f = cfg.token_file
    tmp = f.with_name(f"{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps({"tok": tok, "exp": exp.strftime(_FMT)}), encoding="utf-8")
    for attempt in range(40):
        try:
            os.replace(tmp, f)
            return
        except PermissionError:
            if attempt == 39:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _valid(cached: tuple[str, datetime] | None) -> bool:
    return bool(cached) and datetime.now() < cached[1]


def _issue(cfg: KisConfig) -> tuple[str, datetime]:
    """새 토큰 발급 — 캐시가 없거나 만료됐을 때만 호출된다."""
    r = requests.post(
        f"{cfg.rest_url}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": cfg.app_key, "appsecret": cfg.app_secret},
        headers={"content-type": "application/json", "User-Agent": cfg.user_agent},
        timeout=10,
    )
    d = r.json()
    if r.status_code != 200 or "access_token" not in d:
        # 응답에 비밀값은 없지만, 안내 문구만 보여 준다
        raise RuntimeError(f"토큰 발급 실패: HTTP {r.status_code} {d.get('error_code', '')} {d.get('error_description', '')}")
    tok, exp = d["access_token"], datetime.strptime(d["access_token_token_expired"], _FMT)
    _write_cache(cfg, tok, exp)
    return tok, exp


@contextmanager
def _issue_lock(cfg: KisConfig):
    """프로세스 사이 잠금 — 캐시 파일 옆 *.lock 을 O_EXCL 로 만든다. 못 얻으면 잠금 없이 진행(멈추지 않음)."""
    path = cfg.token_file.with_suffix(".lock")
    got = False
    deadline = time.time() + LOCK_WAIT_S
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            got = True
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > LOCK_STALE_S:
                    path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= deadline:
                break
            _sleep(0.2)
    try:
        yield got
    finally:
        if got:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _issue_or_wait(cfg: KisConfig, label: str, verbose: bool) -> tuple[str, datetime, str]:
    """발급. EGW00133(1분당 1회)이면 다른 곳이 막 받은 캐시를 기다렸다 쓰고, 없으면 한 번만 다시 발급."""
    try:
        tok, exp = _issue(cfg)
        return tok, exp, "발급 완료"
    except RuntimeError as e:
        if "EGW00133" not in str(e):
            raise
    if verbose:
        print(f"[토큰] {label}: 1분당 1회 제한(EGW00133) — 다른 곳이 받은 캐시를 최대 {RATE_WAIT_S:.0f}초 기다림")
    waited = 0.0
    while waited < RATE_WAIT_S:
        _sleep(2.0)
        waited += 2.0
        cached = _read_cache(cfg)
        if _valid(cached):
            return cached[0], cached[1], f"다른 곳이 받은 캐시 사용({waited:.0f}초 기다림)"
    tok, exp = _issue(cfg)                        # 1분이 지났으므로 한 번만 다시
    return tok, exp, "다시 발급 완료"


def get_token(cfg: KisConfig, verbose: bool = True) -> str:
    now = datetime.now()
    held = _memory.get(cfg.mode)
    if held and now < held[1]:
        return held[0]
    label = "모의" if cfg.is_paper else "실전"
    cached = _read_cache(cfg)
    if _valid(cached):
        tok, exp = cached
        if verbose:
            print(f"[토큰] {label}: 캐시 재사용 (만료 {exp:%m-%d %H:%M}, 남은 {_left(exp)})")
    else:
        why = "캐시 없음" if not cached else f"만료됨({cached[1]:%m-%d %H:%M})"
        with _issue_lock(cfg) as locked:
            again = _read_cache(cfg)               # 잠금을 기다리는 동안 다른 프로세스가 먼저 받았을 수 있다
            if _valid(again):
                tok, exp = again
                if verbose:
                    print(f"[토큰] {label}: {why} → 다른 실행기가 먼저 받은 캐시 사용 (만료 {exp:%m-%d %H:%M})")
            else:
                if verbose:
                    print(f"[토큰] {label}: {why} → 새로 발급" + ("" if locked else " (잠금 못 얻음 — 그대로 진행)"))
                tok, exp, how = _issue_or_wait(cfg, label, verbose)
                if verbose:
                    print(f"[토큰] {label}: {how} (만료 {exp:%m-%d %H:%M})"
                          + (" — 한국투자 카톡 안내가 옵니다" if "발급" in how else ""))
    _memory[cfg.mode] = (tok, exp)
    return tok


def warm(mode: str, printer=print) -> bool:
    """실행기 시작 때 토큰을 미리 받아 둔다(첫 봉에서 여러 프로세스가 한꺼번에 발급하지 않게). 실패해도 멈추지 않는다."""
    from . import config
    try:
        get_token(config.load(mode))
        return True
    except Exception as e:                         # 첫 봉 조회 때 다시 시도된다
        printer(f"[토큰] 시작 때 미리 받기 실패({mode}) — 첫 조회 때 다시 시도: {e}")
        return False


def _left(exp: datetime) -> str:
    s = int((exp - datetime.now()).total_seconds())
    return f"{s // 3600}시간 {s % 3600 // 60}분"
