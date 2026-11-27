"""다시 틀기 — 판단 기록(logs/judge_*.jsonl)을 체결 백테스트의 문 입력(scripts/backtest_ticks.py --ext <파일>)으로 바꾼다.

모델에 새로 묻지 않는다. 그때 기록한 답이 내보낸(또는 dry 로 내보냈을) 신호 · 거르기만 옮긴다.
쓸모: 같은 판단에 금액 · 청산 규칙 · 주문 방식을 바꾸면 어땠나. 지난 날 자료로 모델에 새로 묻는 것은 검증이 아니다(결과를 아는 모델).
한 줄 꼴(strategy/ext.py ReplayDoor):
  {"type": "signal", "at_ms": 문에 닿은 시각, "signal": {id, source, code, action, expires_ms, amount?, exit_by?, max_hold_min?, stop_pct?, ref, note}}
  {"type": "gate", "at_ms": …, "name": …, "gate": {block_entries: true, expires_ms, reason, codes?, ref}}
옮기지 않는 것: 검토 답(검토 id 가 그날 실행기의 시각에 묶여 재현에서 맞지 않음) · 설정 바꾸기 · 알림 — 개수만 센다.

쓰는 법: python -X utf8 -m judge.replay logs/judge_20261111.jsonl --out 판단재현_20261111.jsonl [--judge id] [--written-only]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from judge.record import read_file  # noqa: E402

USED = ("written", "dry")


def convert(records: list[dict], judge: str | None = None, written_only: bool = False) -> tuple[list[dict], dict]:
    """판단 기록 줄들 → (재현 입력 줄들, 센 것)."""
    out, n = [], {"judge_lines": 0, "signal": 0, "gate": 0, "skipped": 0, "review_not_replayed": 0,
                  "settings_not_replayed": 0, "notify_not_replayed": 0}
    ok = ("written",) if written_only else USED + ("refused",)
    for r in records:
        if r.get("event") != "judge" or not r.get("valid"):
            continue
        if judge and r.get("judge") != judge:
            continue
        n["judge_lines"] += 1
        for o in r.get("outputs") or []:
            k, st, obj = o.get("kind"), o.get("status"), o.get("obj") or {}
            if k in ("review", "settings", "notify"):
                n[f"{k}_not_replayed"] += 1
                continue
            if k not in ("signal", "gate") or st not in ok or not obj:
                n["skipped"] += 1
                continue
            at = int(o.get("at_ms") or r.get("ts_ms") or 0)
            if k == "signal":
                sig = {"id": obj.get("id") or f"{r.get('ref')}-{obj.get('code')}", "source": obj["source"],
                       "code": obj["code"], "action": obj["action"], "expires_ms": at + int(float(obj["ttl_s"]) * 1000)}
                for key in ("amount", "exit_by", "max_hold_min", "stop_pct", "ref", "note"):
                    if obj.get(key) is not None:
                        sig[key] = obj[key]
                out.append({"type": "signal", "at_ms": at, "signal": sig})
            else:
                g = {"block_entries": True, "expires_ms": at + int(float(obj["ttl_s"]) * 1000),
                     "reason": obj.get("reason", ""), "ref": obj.get("ref")}
                if obj.get("codes"):
                    g["codes"] = list(obj["codes"])
                out.append({"type": "gate", "at_ms": at, "name": obj["name"], "gate": g})
            n[k] += 1
    out.sort(key=lambda x: (x["at_ms"], x["type"]))
    return out, n


def write(lines: list[dict], path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="판단 기록 → 체결 백테스트 재현 입력(--ext)")
    ap.add_argument("logs", nargs="+", type=Path, help="logs/judge_YYYYMMDD.jsonl (여럿 가능)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--judge", default=None, help="이 판단만")
    ap.add_argument("--written-only", action="store_true", help="실제로 문에 쓴 것만(dry · 거부된 것 빼기)")
    a = ap.parse_args(argv)
    recs = []
    for p in a.logs:
        recs += read_file(p)
    lines, n = convert(recs, a.judge, a.written_only)
    write(lines, a.out)
    print(f"재현 입력 {len(lines)}줄 → {a.out.name}  (신호 {n['signal']} · 거르기 {n['gate']} · 옮기지 않음: "
          f"검토 답 {n['review_not_replayed']} · 설정 {n['settings_not_replayed']} · 알림 {n['notify_not_replayed']})")
    print("주의: 기록한 답만 다시 튼다 — 지난 날 자료로 모델에 새로 묻는 것은 검증이 아니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
