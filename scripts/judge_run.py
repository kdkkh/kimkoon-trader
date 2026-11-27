"""AI 판단 프로세스 — 실행기와 따로 돈다(죽어도 실행기는 산다. 거르기는 만료로 저절로 풀린다).
검토 표시(review.json): 정상으로 끝나면(정지 파일 · --until · Ctrl+C) 이 프로세스가 쓴 것을 지운다. 비정상으로 죽으면
  만료된 표시가 남아 실행기가 on_timeout 대로 한다(기본 = 전략 진입을 하지 않음) — 봇 /검토끄기 로 걷는다.
기본 대상('running')은 돌고 있는 모의 · 시뮬레이션 실행기만(dry-run 은 넣지 않는다).

쓰는 법
  python -X utf8 scripts/judge_run.py                       판단 정의(judgments/*.yaml, 견본 제외)를 방아쇠대로 돌린다
  python -X utf8 scripts/judge_run.py --once market_regime  손으로 한 번(쉼표로 여럿)
  python -X utf8 scripts/judge_run.py --list                정의 검사 결과만
깃발
  --fake      가짜 공급자 + 가짜 자료(뉴스 · 공시를 조회하지 않음) + 알림 보내지 않음 — 바깥을 하나도 부르지 않는다
  --dry       내보내지 않고 기록만(신호 · 거르기 · 설정 · 알림 · 검토 표시 모두)
  --root      뿌리 폴더(기본 = 이 프로그램 폴더). 기록 · 상태 · 문 · 설정을 그 아래에서 읽고 쓴다
  --defs      판단 정의 폴더(기본 <root>/judgments) · --examples 견본(*.example.yaml)도 읽음
  --codes     종목(쉼표) — 실행기가 돌지 않을 때 · 손으로 물을 때 · --text 묻는 글(text 입력)
  --until     이 시각(HH:MM)에 끝냄 · --poll 한 바퀴 간격(초)
종료 코드: 0 · 2 정의 · 전역 설정 오류 · 3 이미 다른 판단 프로세스가 돎(state/judge.lock)
멈추기: state/judge.stop 파일을 만든다(강제 종료하지 않는다).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judge import defs as jdefs  # noqa: E402
from judge import limits  # noqa: E402
from judge.engine import Engine  # noqa: E402


def parse(argv=None):
    ap = argparse.ArgumentParser(description="AI 판단 프로세스(모의 전용)")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--defs", type=Path, default=None)
    ap.add_argument("--examples", action="store_true", help="*.example.yaml 견본도 읽음")
    ap.add_argument("--once", default=None, help="판단 id(쉼표로 여럿) — 손으로 한 번")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--codes", default=None)
    ap.add_argument("--text", default=None)
    ap.add_argument("--until", default="20:05")
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--list", action="store_true")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    a = parse(argv)
    root = a.root.resolve()
    folder = a.defs.resolve() if a.defs else root / "judgments"
    try:
        g = limits.load_global(root)
    except limits.GlobalError as e:
        print(f"전역 설정 오류: {e}")
        return 2
    defs, errors = jdefs.load_all(folder, examples=a.examples)
    for f, e in errors:
        print(f"  [정의 거부] {f}: {e}")
    print(f"판단 정의 {len(defs)}개(켜짐 {sum(1 for d in defs if d['enabled'])}) · 거부 {len(errors)} · "
          f"{'가짜(--fake) · ' if a.fake else ''}{'기록만(--dry) · ' if a.dry else ''}전역 설정 {g.get('_file') or '기본값'}")
    if a.list:
        for d in defs:
            print(f"  {d['id']:<20} {'켜짐' if d['enabled'] else '꺼짐'} · {d['model']['provider']} {d['model']['name']} · "
                  f"언제 {[w['_kind'] for w in d['when']]} · 쓰임 {[u['kind'] for u in d['use']]} · {d['_file']}")
        return 2 if errors else 0
    from strategy.live import acquire_lock, release_lock
    lock = root / "state" / "judge.lock"
    other = acquire_lock(lock)
    if other:
        print(f"이미 다른 판단 프로세스가 돌고 있습니다(pid {other}) — 하나만 돈다")
        return 3
    try:
        codes = [c.strip() for c in a.codes.split(",") if c.strip()] if a.codes else None
        eng = Engine(root, defs, g, fake=a.fake, dry=a.dry, codes=codes, def_errors=errors)
        if a.once:
            recs = eng.once([x.strip() for x in a.once.split(",") if x.strip()], codes=codes, text=a.text)
            for r in recs:
                if r.get("event") == "judge":
                    outs = ", ".join(f"{o.get('kind')}:{o.get('status')}" for o in r.get("outputs") or []) or "없음"
                    ans = r.get("answer") or {}
                    shown = ans.get("answer", ans.get("score", "-")) if ans else "버림"
                    print(f"  {r['judge']}: 답 {shown} · 유효 {r['valid']}"
                          f"{' (' + r['invalid_reason'] + ')' if not r['valid'] else ''} · 쓰임 {outs} · 호출 {r['calls']} · "
                          f"비용 어림 {r['cost_est']}")
                else:
                    print(f"  {r.get('judge')}: 묻지 않음 — {r.get('reason')}")
            return 0
        if not any(d["enabled"] for d in defs):
            print("켜진 판단 정의가 없습니다 — 끝냄")
            return 0
        reason = eng.run(until=a.until, poll_s=a.poll)
        print(f"끝냄: {reason}")
        return 0
    finally:
        release_lock(lock)


if __name__ == "__main__":
    sys.exit(main())
