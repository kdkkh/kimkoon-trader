"""자식 프로세스 — 화면이 기존 스크립트(백테스트 · 검증기 …)를 띄우고 진행 · 끝난 결과 · 실패 문구를 보여 준다.

- 화면 프로세스 안에서 백테스트를 import 해 돌리지 않는다(sim/backtest.py 가 실행기 모듈 전역 경로를 바꾼다).
- 같은 이름의 작업이 돌고 있으면 두 번째는 거부한다.
- 띄우기 실패 · 비정상 종료 · 출력 읽기 실패 어느 것도 서버를 멈추지 않는다(예외 대신 상태로 돌려줌).
- 출력은 뿌리 폴더 아래 logs/desk/ 에 작업마다 파일 하나. 화면에 낼 때는 끝부분만, 경로를 가려서.
- 띄운 프로세스를 강제로 끝내지 않는다(끝날 때까지 둔다).
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from desk.core.mask import scrub

CREATE_NO_WINDOW = 0x08000000
OUT_TAIL = 6000                       # 화면에 보일 출력 끝부분(바이트)


class Jobs:
    def __init__(self, root: Path, out_dir: Path | None = None):
        self.root = Path(root)
        self.out_dir = Path(out_dir) if out_dir else self.root / "logs" / "desk"
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}

    def _show_args(self, args: list[str]) -> str:
        shown = []
        for a in args[1:]:                                   # 파이썬 실행 파일 경로는 보이지 않는다
            shown.append(scrub(a, self.root))
        return "python " + " ".join(shown)

    def start(self, name: str, args: list[str], cwd: Path | None = None, label: str = "", on_done=None) -> dict:
        """{ok, status, message, job}. on_done(job) 은 끝난 뒤 한 번(결과 해석용, 예외는 삼킨다)."""
        with self.lock:
            j = self.jobs.get(name)
            if j and j["state"] == "running" and j["proc"] is not None and j["proc"].poll() is None:
                return {"ok": False, "status": "busy", "message": f"이미 도는 중: {label or name}", "job": self.view(name)}
            job = {"name": name, "label": label or name, "args": self._show_args(args), "state": "running",
                   "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ended": "", "code": None,
                   "proc": None, "out": None, "message": "", "on_done": on_done, "result": None, "t0": time.time()}
            try:
                self.out_dir.mkdir(parents=True, exist_ok=True)
                out = self.out_dir / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.log"
                job["out"] = out
                env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
                with out.open("wb") as fh:
                    job["proc"] = subprocess.Popen(args, cwd=str(cwd or self.root), stdout=fh, stderr=subprocess.STDOUT,
                                                   stdin=subprocess.DEVNULL, env=env,
                                                   creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
                job["message"] = "띄움"
            except Exception as e:                           # 실행 파일 없음 · 권한 · 디스크 …
                job.update(state="error", ended=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           message=f"띄우지 못함: {type(e).__name__} {scrub(str(e), self.root)[:160]}")
            self.jobs[name] = job
        return {"ok": job["state"] == "running", "status": job["state"], "message": job["message"], "job": self.view(name)}

    def _refresh(self, j: dict) -> None:
        p = j.get("proc")
        if j["state"] != "running" or p is None:
            return
        code = p.poll()
        if code is None:
            return
        j.update(code=code, state="done" if code == 0 else "failed", ended=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 message="끝남" if code == 0 else f"실패(종료 코드 {code})")
        if j.get("on_done"):
            try:
                j["result"] = j["on_done"](j)
            except Exception as e:
                j["result"] = {"error": f"결과 해석 실패: {type(e).__name__}"}

    def output(self, j: dict, limit: int = OUT_TAIL) -> str:
        out = j.get("out")
        if not out:
            return ""
        try:
            with Path(out).open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(size - limit, 0))
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        if j.get("state") == "running":
            text = text[: text.rfind("\n") + 1] if "\n" in text else ""
        return scrub(text, self.root)

    def text(self, name: str) -> str:
        """다 읽은 출력(결과 해석용 — 화면에 그대로 내지 않음)."""
        j = self.jobs.get(name)
        if not j or not j.get("out"):
            return ""
        try:
            return Path(j["out"]).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def view(self, name: str) -> dict | None:
        j = self.jobs.get(name)
        if j is None:
            return None
        self._refresh(j)
        return {"name": j["name"], "label": j["label"], "args": j["args"], "state": j["state"], "started": j["started"],
                "ended": j["ended"], "code": j["code"], "message": j["message"], "output": self.output(j),
                "result": j.get("result"), "seconds": round(time.time() - j["t0"], 1)}

    def all(self) -> list[dict]:
        with self.lock:
            names = list(self.jobs)
        return [v for v in (self.view(n) for n in names) if v]

    def running(self) -> list[str]:
        return [v["name"] for v in self.all() if v["state"] == "running"]
