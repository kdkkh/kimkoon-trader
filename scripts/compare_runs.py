"""두 재현 백테스트 결과를 사건 단위로 비교한다 — 실행기를 고친 뒤 '동작이 같은가'를 확인하는 회귀 검사.

쓰는 법(이 폴더에서, .venv 파이썬으로):
  python -X utf8 scripts/compare_runs.py <결과 폴더 A> <결과 폴더 B> [--ignore-event risk_block ...] [--show 5]
  결과 폴더 = backtest_ticks.py 의 --out 폴더(all/strategy_live_*.jsonl · round_trips.csv). jsonl 파일을 바로 줘도 된다.
비교에서 빼는 것: 벽시계 칸(ts_ms · at)은 돌릴 때마다 달라서 지운다. 주문 id(uuid 앞 10자)는 돌릴 때마다 새로 만들어지므로
  처음 나온 순서 번호(#0, #1 …)로 바꿔 비교한다. 그 밖의 칸(가상 시계 ms · 가격 · 수량 · 이유 문구)은 모두 그대로 비교한다.
출력: 줄 수 · A 에만 있는 줄 · B 에만 있는 줄(순서를 맞춘 뒤) · 처음 몇 줄 · 사건 종류별 개수 차이 · 거래(round_trips) 같은지.
끝내는 코드: 같으면 0, 다르면 1, 읽지 못하면 2.
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

WALL = {"ts_ms", "at"}
ORDER_ID = re.compile(r"^[0-9a-f]{10}$")          # oms.model.Order.id = uuid4().hex[:10]


def find_log(p: Path) -> Path:
    if p.is_file():
        return p
    found = sorted((p / "all").glob("strategy_live_*.jsonl")) or sorted(p.glob("strategy_live_*.jsonl"))
    if len(found) != 1:
        raise FileNotFoundError(f"기록 파일을 하나로 정할 수 없음: {p.name} 안 {len(found)}개")
    return found[0]


def read_events(p: Path) -> list[dict]:
    return [json.loads(line) for line in find_log(p).read_text(encoding="utf-8").splitlines() if line.strip()]


def read_trips(p: Path) -> list[dict] | None:
    f = (p if p.is_dir() else p.parent.parent) / "round_trips.csv"
    if not f.exists():
        return None
    with f.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def normalize(events: list[dict], ignore: set[str]) -> list[str]:
    ids: dict[str, str] = {}

    def fix(v):
        if isinstance(v, str) and ORDER_ID.match(v):
            return ids.setdefault(v, f"#{len(ids)}")
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v

    out = []
    for r in events:
        if r.get("event") in ignore:
            continue
        out.append(json.dumps(fix({k: v for k, v in r.items() if k not in WALL}), ensure_ascii=False, sort_keys=True))
    return out


def compare(a: Path, b: Path, ignore: set[str], show: int = 5, say=print) -> int:
    ea, eb = read_events(a), read_events(b)
    na, nb = normalize(ea, ignore), normalize(eb, ignore)
    only_a, only_b = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, na, nb, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            only_a += [(i, na[i]) for i in range(i1, i2)]
        if tag in ("replace", "insert"):
            only_b += [(j, nb[j]) for j in range(j1, j2)]
    say(f"A {len(ea)}줄 · B {len(eb)}줄 · 비교 대상 A {len(na)} / B {len(nb)}"
        + (f" (뺀 사건: {', '.join(sorted(ignore))})" if ignore else ""))
    say(f"다른 줄: A 에만 {len(only_a)} · B 에만 {len(only_b)}")
    for name, rows in (("A", only_a), ("B", only_b)):
        for i, line in rows[:show]:
            say(f"  {name}[{i}] {line[:300]}")
    ca = collections.Counter(r.get("event") for r in ea)
    cb = collections.Counter(r.get("event") for r in eb)
    d = {k: (ca[k], cb[k]) for k in sorted(set(ca) | set(cb), key=str) if ca[k] != cb[k]}
    say(f"사건 종류별 개수 차이(A, B): {d or '없음'}")
    ta, tb = read_trips(a), read_trips(b)
    same_trips = True
    if ta is not None and tb is not None:
        same_trips = ta == tb
        sa, sb = sum(float(r["pnl"]) for r in ta), sum(float(r["pnl"]) for r in tb)
        say(f"거래 A {len(ta)}건 {sa:,.0f}원 · B {len(tb)}건 {sb:,.0f}원 · 행 단위 같음 {same_trips}")
    same = not only_a and not only_b and same_trips
    say("결과: 같음" if same else "결과: 다름")
    return 0 if same else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="재현 백테스트 두 결과의 사건 단위 비교")
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--ignore-event", nargs="*", default=[], help="비교에서 뺄 사건 이름(예: risk_block)")
    ap.add_argument("--show", type=int, default=5, help="다른 줄을 몇 줄까지 보여 줄지")
    a = ap.parse_args()
    try:
        return compare(a.a, a.b, set(a.ignore_event), a.show)
    except (OSError, ValueError) as e:
        print(f"읽지 못함: {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
