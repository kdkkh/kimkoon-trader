"""언제 묻나 — 시각(at) · 구간(every_min) · 새 뉴스 제목(on: new_news) · 전략 신호(on: signal) · 검토 요청(on: review) · 손으로 한 번.

같은 방아쇠(판단 · 방아쇠 번호 · 종목)에는 min_gap_s 최소 간격을 둔다. 마지막으로 물은 시각은 그날 판단 기록에서 되살린다
(판단 프로세스를 다시 띄워도 at 이 두 번 돌지 않게).
at 은 그 시각부터 AT_WINDOW_MIN 분 안에만 돈다(늦게 띄운 날 아침 판단이 한낮에 도는 것을 막음).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

AT_WINDOW_MIN = 15


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def key_of(d: dict, i: int, code: str | None = None) -> str:
    return f"{d['id']}#{i}" + (f"#{code}" if code else "")


class Schedule:
    def __init__(self):
        self.last: dict[str, int] = {}

    def restore(self, records: list[dict]) -> None:
        for r in records:
            k = (r.get("trigger") or {}).get("key") if isinstance(r.get("trigger"), dict) else None
            if k and r.get("event") in ("judge", "skip") and r.get("asked", True) is not False:
                self.last[k] = max(self.last.get(k, 0), int(r.get("ts_ms") or 0))

    def gap_ok(self, key: str, now_ms: int, gap_s: float) -> bool:
        t = self.last.get(key)
        return t is None or now_ms - t >= gap_s * 1000

    def mark(self, key: str, now_ms: int) -> None:
        self.last[key] = now_ms

    def clock_due(self, d: dict, now_ms: int) -> list[dict]:
        """시각 · 구간 방아쇠 중 지금 돌 것. 돌린다고 정하면 그 자리에서 표시한다."""
        now = datetime.fromtimestamp(now_ms / 1000)
        mins = now.hour * 60 + now.minute
        today = now.strftime("%Y%m%d")
        out = []
        for i, w in enumerate(d["when"]):
            k = key_of(d, i)
            if w["_kind"] == "at":
                t = _hm(w["at"])
                last = self.last.get(k)
                done = last is not None and datetime.fromtimestamp(last / 1000).strftime("%Y%m%d") == today
                if not done and t <= mins < t + AT_WINDOW_MIN:
                    out.append({"kind": "at", "key": k, "detail": w["at"]})
            elif w["_kind"] == "every_min":
                if _hm(w["from"]) <= mins <= _hm(w["until"]) and self.gap_ok(k, now_ms, max(w["every_min"] * 60, w["min_gap_s"]) - 1):
                    out.append({"kind": "every_min", "key": k, "detail": f"{w['every_min']}분마다"})
        for t in out:
            self.mark(t["key"], now_ms)
        return out


class NewsWatcher:
    """종목마다 처음 본 제목 묶음을 기준으로 두고(첫 회는 방아쇠 없음), 그 뒤 새로 보인 제목만 돌려준다."""

    def __init__(self):
        self.seen: dict[str, set] = {}

    def poll(self, sources, codes: list[str], limit: int) -> dict[str, list[dict]]:
        out = {}
        for c in codes:
            rows = sources.news(c, limit)
            keys = {(r.get("srno"), r.get("provider_code"), r.get("title")) for r in rows}
            if c not in self.seen:
                self.seen[c] = keys
                continue
            new = [r for r in rows if (r.get("srno"), r.get("provider_code"), r.get("title")) not in self.seen[c]]
            self.seen[c] |= keys
            if new:
                out[c] = new
        return out


class SignalWatcher:
    """실행기 기록의 signal 사건 중 판단 프로세스가 뜬 뒤 새로 생긴 것."""

    def __init__(self, since_ms: int):
        self.since = since_ms
        self.seen: set = set()

    def poll(self, root: Path, targets: list[str]) -> list[dict]:
        from control.common import log_path, read_events
        out = []
        for t in targets:
            for e in read_events(log_path(root, t), {"signal"}):
                ts = int(e.get("ts_ms") or 0)
                k = (t, ts, e.get("code"), e.get("kind"))
                if ts < self.since or k in self.seen:
                    continue
                self.seen.add(k)
                out.append(dict(e, _target=t))
        return out


def review_requests(root: Path, target: str) -> list[dict]:
    """실행기가 낸 검토 요청(state/…/ext/review_requests/*.json). 깨진 파일은 건너뛴다."""
    from control.common import runner_paths
    try:
        d = runner_paths(root, target)["ext"] / "review_requests"
        files = sorted(d.glob("*.json"))
    except Exception:
        return []
    out = []
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and r.get("id") and r.get("code"):
            out.append(r)
    return out
