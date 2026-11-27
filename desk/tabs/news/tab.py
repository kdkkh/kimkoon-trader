"""뉴스 탭 — 자기 한투 키로 뉴스 제목 조회(단추를 눌렀을 때만).

한투 뉴스 제목 API 는 공식 문서상 모의투자 미지원이라 실전 키(조회만)가 필요하다.
화면 프로세스는 한투를 직접 부르지 않는다 — 조회는 자식 프로세스(fetch_titles.py)가 하고, 실행기와 같은 토큰 캐시
· 발급 잠금(kis.auth)을 쓴다. 제목은 화면에만 보이고 저장하지 않는다(자식의 표준 출력을 메모리로만 받는다).
?mask=1 이면 제목 · 공급사를 가린다.
내 화면 전용 조각(mine_*.py — 공개판에는 없음): 받아 둔 뉴스 기록 보기 — paths.news_data 가 있을 때만.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from desk.core import mine

ID = "news"
NAME = "뉴스"
PUBLIC = True
NEEDS = ()

MINE = mine.load(__file__)
FRAME_SRC = mine.frames(MINE)

CODE_RX = re.compile(r"^[0-9A-Z]{6}$")
CHILD = Path(__file__).resolve().parent / "fetch_titles.py"
TIMEOUT_S = 90
SOURCE = "한국투자증권 오픈 API 뉴스 제목(공급사는 표의 칸)"


def _key_present() -> bool:
    """한투 설정 파일(kis_devlp.yaml)이 있는지만 본다(열지 않는다). 실전 키 칸이 채워졌는지는 조회할 때 자식이 본다."""
    d = os.environ.get("KIS_CONFIG_DIR")
    folder = Path(d) if d else Path.home() / "KIS" / "config"
    try:
        return (folder / "kis_devlp.yaml").is_file()
    except OSError:
        return False


def _runner_codes(root: Path) -> list[str]:
    try:
        from control import settings_edit
        v = settings_edit.view(root)
        return [str(c) for c in (v["settings"].get("codes") or [])] if v.get("ok") else []
    except Exception:
        return []


def _running(root: Path) -> list[str]:
    try:
        from control import state
        return state.running_targets(root)
    except Exception:
        return []


def get_info(ctx, req):
    codes = _runner_codes(ctx.root)
    return {"ok": True, "key": _key_present(), "codes": codes, "default_code": codes[0] if codes else "",
            "running": _running(ctx.root), "source": SOURCE,
            "note": "한투 뉴스 제목 API 는 공식 문서상 모의투자 미지원 — 실전 키(조회만, 주문 없음)가 필요합니다. "
                    "조회는 따로 띄운 프로세스가 실행기와 같은 토큰 캐시로 하고, 제목은 화면에만 보이고 저장하지 않습니다.",
            "mine": mine.infos(ctx, MINE)}


def _child(ctx, code: str, limit: int) -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        p = subprocess.run([ctx.python, "-X", "utf8", str(CHILD), "--code", code, "--limit", str(limit)],
                           cwd=str(ctx.root), capture_output=True, timeout=TIMEOUT_S, env=env, stdin=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        return {"ok": False, "kind": "error", "message": f"{TIMEOUT_S}초 안에 끝나지 않음"}
    except OSError as e:
        return {"ok": False, "kind": "error", "message": f"띄우지 못함: {type(e).__name__}"}
    lines = [ln for ln in p.stdout.decode("utf-8", errors="replace").splitlines() if ln.strip().startswith("{")]
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError):
        return {"ok": False, "kind": "error", "message": f"결과를 읽지 못함(종료 코드 {p.returncode})"}


def post_query(ctx, req):
    b = req.body
    if b.get("confirm") is not True:
        return 400, {"ok": False, "message": "확인 창을 거쳐야 합니다(한투 조회)"}
    code = str(b.get("code") or "").strip().upper()
    if code and not CODE_RX.match(code):
        return 400, {"ok": False, "message": "종목은 6자리(숫자 · 영문 대문자) — 비우면 전체"}
    try:
        limit = int(b.get("n") or 40)
    except (TypeError, ValueError):
        limit = 0
    if not 1 <= limit <= 400:
        return 400, {"ok": False, "message": "건수는 1~400"}
    fake = ctx.store.get("news_fetch")                 # 시험이 끼우는 가짜 조회(바깥 호출 없음)
    if callable(fake):
        try:
            res = {"ok": True, "rows": fake(code, limit)}
        except Exception as e:
            res = {"ok": False, "kind": "key_missing" if type(e).__name__ == "KeyMissing" else "error",
                   "message": f"{type(e).__name__}: {ctx.scrub(str(e))[:200]}"}
    elif not _key_present():
        res = {"ok": False, "kind": "key_missing"}
    else:
        res = _child(ctx, code, limit)
    if not res.get("ok"):
        if res.get("kind") == "key_missing":
            return {"ok": False, "key_missing": True, "message": "키 없음: kis_devlp.yaml(실전 키)을 설정 폴더에"}
        return {"ok": False, "message": ctx.scrub(str(res.get("message") or "조회 실패"))[:240]}
    rows = []
    for r in res.get("rows") or []:
        if not isinstance(r, dict):
            continue
        d, t = str(r.get("date") or ""), str(r.get("time") or "")
        rows.append({"at": (f"{d[:4]}-{d[4:6]}-{d[6:]} " if len(d) == 8 else d) + (f"{t[:2]}:{t[2:4]}:{t[4:]}" if len(t) == 6 else t),
                     "provider": str(r.get("provider") or ""), "codes": [str(c) for c in r.get("codes") or []][:10],
                     "title": "제목 가림" if req.mask else str(r.get("title") or "")})
    return {"ok": True, "code": code, "n": len(rows), "rows": rows, "source": SOURCE,
            "note": "제목은 화면에만 보입니다(저장하지 않음)."}


ROUTES = {"GET info": get_info, "POST query": post_query}
ROUTES.update(mine.routes(MINE))
