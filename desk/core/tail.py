"""새 줄만 읽기 — 바이트 위치를 기억해 그 뒤만 읽는다(하루 540KB 기록을 2초마다 통째로 읽지 않게).

- 마지막 줄바꿈까지만 처리한다. 실행기가 반쯤 쓴 줄(한글이 걸친 줄 포함)은 다음 번에 온전히 읽힌다.
- 파일이 위치보다 작아지면(잘리거나 새로 만들어짐) 처음부터 다시 읽고 reset 을 알린다.
- 어떤 예외도 내지 않는다(파일이 없으면 빈 결과).
화면은 위치(pos)를 들고 다니므로 서버는 상태를 갖지 않아도 된다(read_from). 서버 쪽에서 위치를 들 때는 Tail.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

MAX_BYTES = 4 * 1024 * 1024          # 한 번에 읽는 최대 바이트(넘으면 다음 번에 이어서)


def read_from(path: Path, pos: int = 0, max_bytes: int = MAX_BYTES) -> dict:
    """{lines: [dict], pos: 새 위치, reset: 처음부터 다시 읽었나, size, more: 아직 남았나, exists}."""
    p = Path(path)
    try:
        pos = max(int(pos or 0), 0)
    except (TypeError, ValueError):
        pos = 0
    try:
        size = p.stat().st_size
    except OSError:
        return {"lines": [], "pos": 0, "reset": pos > 0, "size": 0, "more": False, "exists": False}
    reset = False
    if size < pos:
        pos, reset = 0, True
    try:
        with p.open("rb") as f:
            f.seek(pos)
            chunk = f.read(max_bytes)
    except OSError:
        return {"lines": [], "pos": pos, "reset": reset, "size": size, "more": False, "exists": True}
    end = chunk.rfind(b"\n")
    if end < 0:
        return {"lines": [], "pos": pos, "reset": reset, "size": size, "more": False, "exists": True}
    out = []
    for raw in chunk[:end].split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            r = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(r, dict):
            out.append(r)
    new_pos = pos + end + 1
    return {"lines": out, "pos": new_pos, "reset": reset, "size": size, "more": new_pos < size, "exists": True}


def read_all(path: Path) -> list[dict]:
    """처음부터 끝(마지막 줄바꿈)까지."""
    out, pos = [], 0
    while True:
        r = read_from(path, pos)
        out.extend(r["lines"])
        if not r["more"] or r["pos"] == pos:
            return out
        pos = r["pos"]


class Tail:
    """위치를 들고 있는 읽개(스레드 안전)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.pos = 0
        self.lock = threading.Lock()

    def read_new(self) -> list[dict]:
        with self.lock:
            out = []
            while True:
                r = read_from(self.path, self.pos)
                if r["reset"]:
                    out = []
                out.extend(r["lines"])
                moved = r["pos"] != self.pos
                self.pos = r["pos"]
                if not r["more"] or not moved:
                    return out
