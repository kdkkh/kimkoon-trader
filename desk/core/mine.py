"""'내 화면 전용' 조각 불러오기 — 탭 폴더 안 mine_*.py 를 찾아 붙인다.

공개판은 수집한 자료를 싣지 않는다. 그래서 탭 안 기능을 파일 이름으로 가른다.
  mine_<이름>.py · mine_<이름>.js · mine_<이름>.css   내 화면에만 있는 조각(공개판 조립이 이름으로 뺀다)
  그 밖의 파일                                         공개판에도 가는 것
mine_*.py 의 꼴
  KEY = "ep09"                    조각 이름(화면이 이 이름으로 칸을 만든다)
  NEED = "ep09_data"              설정 paths 의 경로 이름 — 비었거나 없는 경로면 이 조각은 없는 것처럼 군다
  ROUTES = {"GET ep09": fn}       탭 ROUTES 에 더해진다. 경로가 없으면 404(응답에 그 칸이 아예 없음)
  FRAMES = ("http://127.0.0.1:8780",)   (선택) 탭 화면이 iframe 으로 붙일 이 PC 의 다른 화면
  def info(ctx) -> dict           (선택) 탭의 첫 응답에 붙일 짧은 정보
파일이 없거나 불러오기에 실패하면 그 조각만 빠지고 탭은 그대로 돈다.
"""
from __future__ import annotations

import atexit
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

PREFIX = "mine_"
LOAD_ERRORS: list[dict] = []


def load(tab_file: str) -> list:
    """tab.py 옆 mine_*.py 를 이름 순으로 불러온다. [(모듈)] — 실패한 파일은 건너뛰고 까닭을 LOAD_ERRORS 에 남긴다."""
    folder = Path(tab_file).resolve().parent
    out = []
    for f in sorted(folder.glob(PREFIX + "*.py")):
        name = f"desk_{folder.name}_{f.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, f)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:                       # 조각 하나의 오류가 탭을 멈추지 않게
            sys.modules.pop(name, None)
            LOAD_ERRORS.append({"file": f.name, "why": f"불러오기 실패: {type(e).__name__}"})
            continue
        if not isinstance(getattr(mod, "KEY", None), str) or not isinstance(getattr(mod, "NEED", None), str):
            LOAD_ERRORS.append({"file": f.name, "why": "KEY · NEED 칸이 없음"})
            continue
        out.append(mod)
    out.sort(key=lambda m: (getattr(m, "ORDER", 50), m.__name__))      # (선택) ORDER = 화면 갈래 순서
    return out


def has_path(ctx, need: str) -> bool:
    p = ctx.path(need)
    try:
        return p is not None and p.exists()
    except OSError:
        return False


def present(ctx, mods: list) -> list:
    """설정에 경로가 있어 켜진 조각들."""
    return [m for m in mods if has_path(ctx, m.NEED)]


def routes(mods: list) -> dict:
    """조각들의 ROUTES 를 하나로. 부를 때마다 경로를 다시 본다(없으면 404 — 칸 자체가 없는 것처럼)."""
    out = {}
    for m in mods:
        for k, fn in (getattr(m, "ROUTES", {}) or {}).items():
            out[k] = _guard(m.NEED, fn)
    return out


def _guard(need: str, fn):
    def call(ctx, req):
        if not has_path(ctx, need):
            return 404, {"ok": False, "error": "없는 주소"}
        return fn(ctx, req)
    call.__name__ = getattr(fn, "__name__", "mine_route")
    return call


def frames(mods: list) -> tuple:
    out = []
    for m in mods:
        for s in getattr(m, "FRAMES", ()) or ():
            if s not in out:
                out.append(s)
    return tuple(out)


def system_python(ctx) -> str | None:
    """받아 둔 자료 쪽 스크립트(이 폴더 밖의 다른 프로그램)를 돌릴 파이썬. 설정 system_python 이 먼저,
    없으면 PATH 의 python. 화면 프로세스의 가상환경 파이썬과 다르다(그쪽 패키지 · playwright 가 깔린 쪽)."""
    v = ctx.cfg.get("system_python")
    if v:
        p = Path(str(v)).expanduser()
        return str(p) if p.is_file() else None
    for name in ("python", "py"):
        w = shutil.which(name)
        if w and Path(w).resolve() != Path(sys.executable).resolve():
            return w
    return None


def port_busy(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        try:
            s.connect((host, int(port)))
            return True
        except OSError:
            return False


def out_dir() -> Path:
    """화면이 띄운 보기 서버의 출력 — 임시 폴더(뿌리 폴더의 logs/ 를 건드리지 않게)."""
    d = Path(tempfile.gettempdir()) / "kimkoon-desk"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Viewer:
    """화면이 띄우는 보기 서버 하나(자식 프로세스). 우리가 띄운 것만 끈다 — 이미 그 포트를 다른 프로그램이 쓰면 띄우지 않는다."""

    def __init__(self, name: str, port: int):
        self.name, self.port = name, int(port)
        self.proc: subprocess.Popen | None = None
        self.out: Path | None = None
        self.started = ""
        self.note = ""
        self.lock = threading.Lock()
        _VIEWERS.append(self)

    def state(self) -> dict:
        with self.lock:
            p = self.proc
            if p is not None and p.poll() is None:
                st = "running"
            elif p is not None:
                st = "ended"
            else:
                st = "busy" if port_busy(self.port) else "off"
            return {"name": self.name, "port": self.port, "state": st, "started": self.started,
                    "code": None if p is None else p.poll(), "note": self.note, "tail": self.tail()}

    def tail(self, limit: int = 1500) -> str:
        if not self.out:
            return ""
        try:
            b = self.out.read_bytes()[-limit:]
        except OSError:
            return ""
        from desk.core.mask import scrub
        return scrub(b.decode("utf-8", errors="replace"))

    def start(self, args: list[str], cwd: Path, wait_s: float = 8.0) -> dict:
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return {"ok": True, "status": "running", "message": "이미 띄움"}
            if port_busy(self.port):
                self.note = f"포트 {self.port} 를 이미 다른 프로그램이 씀 — 띄우지 않음"
                return {"ok": False, "status": "busy", "message": self.note}
            self.out = out_dir() / f"{self.name}_{datetime.now():%Y%m%d_%H%M%S}.log"
            # 받아 둔 자료 폴더에 .pyc 캐시를 남기지 않는다(그 폴더는 읽기만)
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
            try:
                with self.out.open("wb") as fh:
                    self.proc = subprocess.Popen(args, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT,
                                                 stdin=subprocess.DEVNULL, env=env,
                                                 creationflags=0x08000000 if os.name == "nt" else 0)
            except Exception as e:
                self.proc = None
                from desk.core.mask import scrub
                self.note = f"띄우지 못함: {type(e).__name__} {scrub(str(e))[:120]}"
                return {"ok": False, "status": "error", "message": self.note}
            self.started = datetime.now().strftime("%H:%M:%S")
            self.note = ""
        end = time.time() + wait_s
        while time.time() < end:
            if self.proc.poll() is not None:
                return {"ok": False, "status": "ended", "message": f"바로 끝남(종료 코드 {self.proc.poll()})"}
            if port_busy(self.port):
                return {"ok": True, "status": "running", "message": "띄움"}
            time.sleep(0.2)
        return {"ok": True, "status": "running", "message": "띄움(포트 응답 확인 전)"}

    def stop(self) -> dict:
        with self.lock:
            p = self.proc
            if p is None or p.poll() is not None:
                return {"ok": True, "status": "off", "message": "우리가 띄운 것이 돌고 있지 않음"}
            p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
        return {"ok": True, "status": "off", "message": "끔"}


_VIEWERS: list[Viewer] = []


@atexit.register
def _stop_all():                                     # 화면이 끝나면 화면이 띄운 보기 서버도 끈다
    for v in _VIEWERS:
        try:
            v.stop()
        except Exception:
            pass


def infos(ctx, mods: list) -> dict:
    """켜진 조각의 {KEY: info}. 꺼진 조각은 이름도 싣지 않는다."""
    out = {}
    for m in present(ctx, mods):
        fn = getattr(m, "info", None)
        try:
            v = fn(ctx) if callable(fn) else {}
            v = dict(v) if isinstance(v, dict) else {}
        except Exception as e:
            v = {"error": f"{type(e).__name__}: {ctx.scrub(str(e))[:120]}"}
        # 화면 조각(mine_<KEY>.js)이 있을 때만 화면이 불러온다(없는 파일을 부르면 콘솔 오류가 난다)
        v["js"] = (Path(m.__file__).parent / f"{PREFIX}{m.KEY}.js").is_file()
        out[m.KEY] = v
    return out
