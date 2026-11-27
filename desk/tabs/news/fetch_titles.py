"""뉴스 탭의 조회 자식 프로세스 — kis.news.recent_titles 를 한 번 부르고 결과를 표준 출력에 JSON 한 줄로 낸다.

화면 프로세스는 한투를 직접 부르지 않는다. 이 프로세스가 실행기와 같은 토큰 캐시 · 발급 잠금(kis.auth)을 쓴다.
받은 제목은 표준 출력으로만 돌려주고 어디에도 저장하지 않는다(부르는 쪽도 파일에 쓰지 않는다).
실행: python -X utf8 desk/tabs/news/fetch_titles.py --code <종목코드> --limit 40
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="뉴스 제목 한 번 조회(실전 키 · 조회만)")
    ap.add_argument("--code", default="")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args(argv)
    from control.common import scrub
    try:
        from kis import news
        rows = news.recent_titles(a.code, a.limit)
        out = {"ok": True, "rows": rows}
    except Exception as e:                            # 키 없음 · 한투 오류 · 연결 오류 — 문구는 가려서
        kind = "key_missing" if type(e).__name__ == "KeyMissing" else "error"
        out = {"ok": False, "kind": kind, "message": f"{type(e).__name__}: {scrub(str(e))[:200]}"}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
