"""탭 등록표 — 설정의 tabs 목록 순서대로 desk/tabs/<id>/tab.py 를 불러온다.

탭 하나 = 폴더 하나 + 설정 한 줄. 폴더가 없거나 불러오기에 실패하면 그 탭만 빠지고(사유를 남김) 나머지는 그대로 돈다.
탭이 필요한 경로(NEEDS — 설정 paths 의 이름)가 비었거나 없으면 탭은 등록되지만 화면에는 "자료 없음: <이름>"만 보인다.

tab.py 의 꼴
  ID = "trade"                    폴더 이름과 같아야 한다(영문 소문자 · 숫자 · _)
  NAME = "매매"                    탭 줄에 보일 이름
  PUBLIC = True                   공개판에 들어가는 탭인가(조립 스크립트가 본다 — False 면 '내 것만' 묶음)
  NEEDS = ("autotrade_root",)     설정 paths 에서 꼭 있어야 하는 경로 이름(없으면 빈 묶음)
  ROUTES = {"GET summary": fn, "POST stop": fn}   주소 /api/<ID>/<이름> → fn(ctx, req)
  FRAME_SRC = ("http://127.0.0.1:8780",)          (선택) 탭 화면이 iframe 으로 붙일 이 PC 의 다른 화면
"""
from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TABS_DIR = Path(__file__).resolve().parents[1] / "tabs"
_ID_RX = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_ROUTE_RX = re.compile(r"^(GET|POST) [a-z0-9_/]{1,60}$")
_FRAME_RX = re.compile(r"^http://(127\.0\.0\.1|localhost):\d{2,5}$")        # 탭이 iframe 으로 붙일 수 있는 것은 이 PC 의 화면만


@dataclass
class Tab:
    id: str
    name: str
    folder: Path
    public: bool
    needs: tuple
    routes: dict
    missing: list = field(default_factory=list)     # 비어 있지 않으면 '자료 없음'
    frame_src: str = ""                              # 탭 화면이 iframe 으로 붙일 다른 로컬 화면(CSP 에 덧붙임)

    @property
    def ok(self) -> bool:
        return not self.missing

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "public": self.public, "ok": self.ok,
                "missing": list(self.missing), "message": ("자료 없음: " + ", ".join(self.missing)) if self.missing else ""}


def path_present(paths: dict, name: str) -> bool:
    v = (paths or {}).get(name)
    if v in (None, ""):
        return False
    try:
        return Path(str(v)).expanduser().exists()
    except (OSError, ValueError):
        return False


def _load_module(tab_id: str, file: Path):
    mod_name = f"desk_tab_{tab_id}"
    spec = importlib.util.spec_from_file_location(mod_name, file)
    if spec is None or spec.loader is None:
        raise ImportError("tab.py 를 불러올 수 없음")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(mod_name, None)
        raise
    return mod


def load_tabs(tab_ids, paths: dict | None = None, tabs_dir: Path | None = None) -> tuple[list[Tab], list[dict]]:
    """(등록된 탭들, 빠진 탭 [{id, why}]). 어떤 탭의 실패도 예외로 올리지 않는다."""
    base = Path(tabs_dir) if tabs_dir else TABS_DIR
    tabs, skipped, seen = [], [], set()
    for raw in tab_ids or []:
        tid = str(raw).strip()
        if not _ID_RX.match(tid):
            skipped.append({"id": tid[:30], "why": "탭 이름 꼴이 아님(영문 소문자 · 숫자 · _)"})
            continue
        if tid in seen:
            skipped.append({"id": tid, "why": "설정에 두 번 있음"})
            continue
        seen.add(tid)
        f = base / tid / "tab.py"
        if not f.is_file():
            skipped.append({"id": tid, "why": "탭 폴더 없음"})
            continue
        try:
            mod = _load_module(tid, f)
        except BaseException as e:                       # 탭 하나의 오류가 화면 전체를 멈추지 않게
            if isinstance(e, KeyboardInterrupt):
                raise
            skipped.append({"id": tid, "why": f"불러오기 실패: {type(e).__name__}"})
            continue
        if getattr(mod, "ID", None) != tid:
            skipped.append({"id": tid, "why": "tab.py 의 ID 가 폴더 이름과 다름"})
            continue
        routes = getattr(mod, "ROUTES", {}) or {}
        bad = [k for k in routes if not isinstance(k, str) or not _ROUTE_RX.match(k) or not callable(routes[k])]
        if bad:
            skipped.append({"id": tid, "why": "ROUTES 꼴이 아님('GET 이름': 함수)"})
            continue
        needs = tuple(getattr(mod, "NEEDS", ()) or ())
        frames = [s for s in (getattr(mod, "FRAME_SRC", ()) or ()) if isinstance(s, str) and _FRAME_RX.match(s)]
        t = Tab(id=tid, name=str(getattr(mod, "NAME", tid))[:20], folder=f.parent, public=bool(getattr(mod, "PUBLIC", False)),
                needs=needs, routes=dict(routes), missing=[n for n in needs if not path_present(paths or {}, n)],
                frame_src=("; frame-src 'self' " + " ".join(frames)) if frames else "")
        tabs.append(t)
    return tabs, skipped
