"""판단 기록 — logs/judge_YYYYMMDD.jsonl (한 줄 = 사건 하나, 칸 event 로 가름).

event
  judge   물어본 판단 하나: ts_ms · ref · judge · def_hash · trigger · inputs(요약) · prompt_sha · system · user · schema ·
          model · usage · cost_est · raw · answer · valid · invalid_reason · outputs · sec · provider (+ calls · attempts · dry · fake)
  skip    묻지 않은 판단: judge · trigger · reason(스위치 꺼짐 · 상한 · 입력 없음 · 키 없음 …)
  limit   전역 상한에 닿아 그날 멈춤(하루 한 번)
  notice  고지를 아직 확인하지 않아 부르지 않음
  start · stop · error · settings_done
기록에 쓰지 않는 것: 키 · 토큰 · 계좌 · 사용자 폴더 경로(문구는 control.common.scrub 으로 한 번 더 가린다).
뉴스 제목 원문은 judge.yaml record_titles: true 일 때만 — 아니면 일련번호 · 시각 · 해시만(프롬프트 전문도 그 자리만 가린다).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from control.common import scrub

_lock = threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def path(root: Path, day: str | None = None) -> Path:
    return Path(root) / "logs" / f"judge_{day or datetime.now().strftime('%Y%m%d')}.jsonl"


def prompt_sha(system: str, user: str) -> str:
    """시스템 + 사용자 글의 sha256 앞 16자(같은 프롬프트인지 견줄 때)."""
    return hashlib.sha256((system + "\n\x00\n" + user).encode("utf-8")).hexdigest()[:16]


def _clean(v):
    if isinstance(v, str):
        return scrub(v)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def append(root: Path, event: str, ts_ms: int | None = None, **kw) -> dict:
    """한 줄 쓰기. 실패해도 예외를 내지 않고 기록하려던 dict 를 돌려준다."""
    ts = int(ts_ms if ts_ms is not None else now_ms())
    rec = {"ts_ms": ts, "at": datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "event": event}
    rec.update(_clean(kw))
    try:
        p = path(root, datetime.fromtimestamp(ts / 1000).strftime("%Y%m%d"))
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock, p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    return rec


def read_day(root: Path, day: str | None = None) -> list[dict]:
    """그날 기록(반쯤 쓴 줄 · 깨진 줄은 건너뜀)."""
    return read_file(path(root, day))


def read_file(p: Path) -> list[dict]:
    try:
        raw = Path(p).read_bytes()
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def title_hash(title: str) -> str:
    return hashlib.sha1(str(title).encode("utf-8")).hexdigest()[:10]


def mask_titles(text: str, titles: list[tuple[str, str]]) -> str:
    """원답 · 근거 글에 제목이 그대로 들어간 곳을 가린다(titles = [(제목, 바꿀 글)]). 긴 제목부터."""
    s = str(text)
    for t, rep in sorted(titles, key=lambda x: -len(x[0])):
        if t and len(t) >= 6:
            s = s.replace(t, rep)
    return s
