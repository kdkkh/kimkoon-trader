"""공시 탭 — 자기 OpenDART 키로 오늘 공시 목록 조회(단추를 눌렀을 때만).

조회: dart.opendart.today_for_codes(<뿌리>/state, [종목], daily_cap=…) — 화면 프로세스가 부른다(한투가 아니라 OpenDART).
  하루 몫(daily_cap)은 judge.yaml 의 opendart.daily_cap(없으면 기본값)과 같은 값 · 같은 호출 수 파일을 쓴다.
  자동 새로 고침은 없다. 받은 목록은 화면에만 보이고 저장하지 않는다(호출 수 파일만 늘어난다).
키 없음 → '키 없음: opendart.yaml 을 설정 폴더에'. 오늘 몫을 다 쓰면 그 사실만.
출처 표시: 금융감독원 전자공시(OpenDART).
내 화면 전용 조각(mine_*.py — 공개판에는 없음): 받아 둔 공시 자료 보기 — paths.autotrade_root 가 있을 때만.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from desk.core import mine

ID = "disclosure"
NAME = "공시"
PUBLIC = True
NEEDS = ()

MINE = mine.load(__file__)
FRAME_SRC = mine.frames(MINE)

SOURCE = "금융감독원 전자공시(OpenDART)"
CODE_RX = re.compile(r"^[0-9A-Z]{6}$")
DEFAULT_CAP = 500


def _cap(root: Path) -> int:
    try:
        from judge import limits
        return int(limits.load_global(root)["opendart"]["daily_cap"])
    except Exception:
        return DEFAULT_CAP


def _key_present() -> bool:
    """opendart.yaml 이 있는지만 본다(열지 않는다)."""
    d = os.environ.get("KIS_CONFIG_DIR")
    folder = Path(d) if d else Path.home() / "KIS" / "config"
    try:
        return (folder / "opendart.yaml").is_file()
    except OSError:
        return False


def _runner_codes(root: Path) -> list[str]:
    try:
        from control import settings_edit
        v = settings_edit.view(root)
        return [str(c) for c in (v["settings"].get("codes") or [])] if v.get("ok") else []
    except Exception:
        return []


def _calls_today(root: Path, cap: int) -> int:
    try:
        from dart import opendart
        return opendart.CallCounter(root / "state", cap).used()
    except Exception:
        return 0


def get_info(ctx, req):
    cap = _cap(ctx.root)
    codes = _runner_codes(ctx.root)
    return {"ok": True, "key": _key_present(), "codes": codes, "default_code": codes[0] if codes else "",
            "calls_today": _calls_today(ctx.root, cap), "cap": cap, "source": SOURCE,
            "note": "단추를 눌렀을 때만 조회합니다(자동 새로 고침 없음). OpenDART 목록에는 접수 시각이 없고 접수일만 있습니다.",
            "mine": mine.infos(ctx, MINE)}


def _fetch(ctx, codes: list[str], cap: int) -> list[dict]:
    fake = ctx.store.get("disclosure_fetch")          # 시험이 끼우는 가짜 조회(바깥 호출 없음)
    if callable(fake):
        return fake(codes, cap)
    from dart import opendart
    return opendart.today_for_codes(ctx.root / "state", codes, daily_cap=cap)


def post_query(ctx, req):
    b = req.body
    if b.get("confirm") is not True:
        return 400, {"ok": False, "message": "확인 창을 거쳐야 합니다(OpenDART 호출)"}
    raw = str(b.get("code") or "").strip().upper()
    codes = [c for c in re.split(r"[,\s]+", raw) if c][:10]
    if not codes or any(not CODE_RX.match(c) for c in codes):
        return 400, {"ok": False, "message": "종목은 6자리(숫자 · 영문 대문자), 쉼표로 열 개까지"}
    cap = _cap(ctx.root)
    if not callable(ctx.store.get("disclosure_fetch")) and not _key_present():
        return {"ok": False, "key_missing": True, "message": "키 없음: opendart.yaml 을 설정 폴더에"}
    try:
        from dart import opendart
        rows = _fetch(ctx, codes, cap)
    except opendart.KeyMissing:
        return {"ok": False, "key_missing": True, "message": "키 없음: opendart.yaml 을 설정 폴더에"}
    except opendart.DailyCapReached as e:
        return {"ok": False, "cap_reached": True, "message": ctx.scrub(str(e))[:200]}
    except opendart.OpenDartError as e:
        return {"ok": False, "message": ctx.scrub(str(e))[:200]}
    except ValueError as e:
        return 400, {"ok": False, "message": ctx.scrub(str(e))[:200]}
    out = []
    for r in rows:
        out.append({"rcept_no": r.get("rcept_no", ""), "rcept_dt": r.get("rcept_dt", ""), "corp_name": r.get("corp_name", ""),
                    "stock_code": r.get("stock_code", ""), "report_nm": r.get("report_nm", ""),
                    "filer": "제출인 가림" if req.mask else r.get("flr_nm", ""), "rm": r.get("rm", ""),
                    "url": r.get("viewer_url", "") if str(r.get("viewer_url", "")).startswith("https://dart.fss.or.kr/") else ""})
    out.sort(key=lambda x: x["rcept_no"], reverse=True)
    return {"ok": True, "codes": codes, "rows": out, "source": SOURCE, "calls_today": _calls_today(ctx.root, cap), "cap": cap}


ROUTES = {"GET info": get_info, "POST query": post_query}
ROUTES.update(mine.routes(MINE))
