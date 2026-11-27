"""통합 화면 띄우기 — http://127.0.0.1:8790/ (이 PC 에서만 열림).

실행(이 폴더에서, .venv 파이썬으로):
  python -X utf8 scripts/desk.py                         # 설정 desk.yaml(없으면 desk.example.yaml), 포트 8790
  python -X utf8 scripts/desk.py --port 8796 --quit-after 120   # 120초 뒤 스스로 끝냄(확인용)
  python -X utf8 scripts/desk.py --root <다른 trader 폴더>   # 그 폴더의 기록 · 상태 · 설정을 읽음
주소 뒤에 ?rec=1(녹화 틀, 폭 1920) · ?mask=1(종목 이름 · 제목 가림).
화면은 증권사 API 를 부르지 않는다. 끝내기는 Ctrl+C(띄운 자식 작업은 그대로 끝까지 돈다).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

from desk.core.server import DeskServer, load_config  # noqa: E402


def pick_config(root: Path, given: str | None) -> Path | None:
    if given:
        p = Path(given)
        return p if p.is_absolute() else (Path.cwd() / p)
    for name in ("desk.yaml", "desk.example.yaml"):
        if (root / name).is_file():
            return root / name
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="통합 화면(127.0.0.1 만)")
    ap.add_argument("--port", type=int, help="포트(기본: 설정의 port, 없으면 8790)")
    ap.add_argument("--root", help="읽을 trader 폴더(기본: 이 스크립트가 있는 폴더)")
    ap.add_argument("--config", help="설정 파일(기본: 뿌리의 desk.yaml → desk.example.yaml)")
    ap.add_argument("--quit-after", type=float, default=0, help="이 초가 지나면 스스로 끝냄(0 = 끝내지 않음)")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else CODE_ROOT
    cfg_path = pick_config(root, a.config)
    cfg = load_config(cfg_path)
    srv = DeskServer(root, cfg, port=a.port)
    try:
        srv.start()
    except OSError as e:
        print(f"[화면] 포트 {srv.port} 를 열지 못함({type(e).__name__}) — 다른 프로그램이 쓰는지 확인하세요")
        return 1
    print(f"[화면] {srv.url}  (설정 {cfg.get('_file') or cfg.get('_note')})")
    print("[화면] 탭: " + (", ".join(f"{t.name}{'' if t.ok else '(자료 없음)'}" for t in srv.tabs) or "없음"))
    for s in srv.skipped:
        print(f"[화면] 빠진 탭 {s['id']}: {s['why']}")
    end = time.time() + a.quit_after if a.quit_after > 0 else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    srv.stop()
    print("[화면] 끝")
    return 0


if __name__ == "__main__":
    sys.exit(main())
