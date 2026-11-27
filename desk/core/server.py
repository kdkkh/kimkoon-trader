"""통합 화면 서버 — 파이썬 내장 ThreadingHTTPServer, 127.0.0.1 만.

주소
  /                       껍데기 화면(static/shell.html) — 세션 토큰을 심어 보낸다
  /static/<파일>  /fonts/<파일>     공통 css · js · 글꼴
  /tabs/<id>/<파일>        탭 폴더의 .html · .js · .css (tab.py 같은 파이썬 파일은 내지 않는다)
  /api/tabs               탭 목록(자료 없음 표시 포함)
  /api/<id>/<이름>         탭의 ROUTES

보호
  - Host 가 127.0.0.1:<포트> · localhost:<포트> 가 아니면 모든 요청 거절(DNS 리바인딩 막기).
  - 쓰기(POST)는 머리 X-Desk-Token 이 서버가 켤 때 만든 값과 같을 때만. Origin 이 있으면 같은 주소만. 본문은 JSON 만.
  - 모든 응답에 X-Frame-Options · CSP frame-ancestors. 껍데기 · API 는 'none'(DENY), 탭 화면만 'self'(껍데기의 iframe).
  - 모든 API 응답은 mask.clean 을 한 번 더 지난다(원응답 · 주문번호 · 계좌 칸 빼기, 경로 가림).
"""
from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from desk.core import mask as maskmod
from desk.core.procs import Jobs
from desk.core.registry import load_tabs

DESK_DIR = Path(__file__).resolve().parents[1]
STATIC = DESK_DIR / "static"
TOKEN_MARK = "__DESK_TOKEN__"
MAX_BODY = 256 * 1024
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "application/javascript; charset=utf-8", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json; charset=utf-8"}
TAB_FILES = {".html", ".js", ".css", ".svg", ".png"}
CSP_BASE = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
DEFAULT_CONFIG = {"port": 8790, "tabs": ["trade", "backtest"], "paths": {}, "fees": {}}


def load_config(path: Path | None) -> dict:
    """설정 파일(yaml) → dict. 없거나 깨졌으면 기본값(그 사실을 _note 에)."""
    cfg = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in DEFAULT_CONFIG.items()}
    if path is None:
        cfg["_note"] = "설정 파일 없음 — 기본값"
        return cfg
    try:
        import yaml
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
    except FileNotFoundError:
        cfg["_note"] = "설정 파일 없음 — 기본값"
        return cfg
    except Exception as e:
        cfg["_note"] = f"설정 파일을 읽지 못함({type(e).__name__}) — 기본값"
        return cfg
    if isinstance(d, dict):
        for k in ("port", "tabs", "paths", "fees"):
            if k in d and d[k] is not None:
                cfg[k] = d[k]
        for k, v in d.items():
            if k not in cfg:
                cfg[k] = v
    cfg["_file"] = Path(path).name
    return cfg


class Ctx:
    """탭 함수가 받는 것. 탭은 이것으로만 뿌리 폴더 · 설정 · 작업 · 띄우개 · 시계를 얻는다."""

    def __init__(self, root: Path, cfg: dict, jobs: Jobs, launcher=None, clock=None, python: str | None = None):
        self.root = Path(root)
        self.cfg = cfg
        self.paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}
        self.jobs = jobs
        self.launcher = launcher                     # control.switch.start 에 넘길 띄우개(None = 실제로 띄움)
        self.clock = clock or datetime.now
        self.python = python or sys.executable
        self.lock = threading.Lock()
        self.store: dict = {}                        # 탭이 서버 수명 동안 들고 있을 것(설정 적용 결과 등)

    def scrub(self, text) -> str:
        return maskmod.scrub(text, self.root)

    def path(self, name: str) -> Path | None:
        v = self.paths.get(name)
        return Path(str(v)).expanduser() if v not in (None, "") else None


class Req:
    def __init__(self, method: str, q: dict, body: dict, mask: bool):
        self.method, self.q, self.body, self.mask = method, q, body, mask

    def get(self, key: str, default=None):
        v = self.body.get(key) if self.method == "POST" and isinstance(self.body, dict) and key in self.body else None
        if v is None:
            v = self.q.get(key)
        return default if v is None else v


class DeskServer:
    def __init__(self, root: Path, cfg: dict | None = None, port: int | None = None, tabs_dir: Path | None = None,
                 launcher=None, clock=None, python: str | None = None, host: str = "127.0.0.1"):
        self.root = Path(root).resolve()
        self.cfg = cfg if cfg is not None else load_config(None)
        self.port = int(port or self.cfg.get("port") or 8790)
        self.host = "127.0.0.1" if host not in ("127.0.0.1",) else host          # 바깥 주소로는 열지 않는다
        self.token = secrets.token_urlsafe(24)
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {f"http://{h}" for h in self.allowed_hosts}
        tabs_cfg = self.cfg.get("tabs") if isinstance(self.cfg.get("tabs"), list) else []
        self.tabs, self.skipped = load_tabs(tabs_cfg, self.cfg.get("paths") or {}, tabs_dir)
        self.tab_by_id = {t.id: t for t in self.tabs}
        self.ctx = Ctx(self.root, self.cfg, Jobs(self.root), launcher, clock, python)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    # ---- 수명 ----
    def start(self) -> "DeskServer":
        self.httpd = ThreadingHTTPServer((self.host, self.port), _make_handler(self))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="desk-http", daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    # ---- 응답 만들기 ----
    def tabs_info(self) -> dict:
        return {"tabs": [t.info() for t in self.tabs], "skipped": self.skipped,
                "config": self.cfg.get("_file") or self.cfg.get("_note") or ""}

    def html(self, path: Path) -> bytes:
        return path.read_text(encoding="utf-8").replace(TOKEN_MARK, self.token).encode("utf-8")


def _make_handler(desk: DeskServer):
    class H(BaseHTTPRequestHandler):
        server_version = "desk"
        sys_version = ""

        def log_message(self, *a):                   # 접속 기록을 표준 출력에 찍지 않는다
            pass

        # ---- 보내기 ----
        def _send(self, code: int, body: bytes, ctype: str, frame: str = "none", extra_csp: str = ""):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if frame == "self":
                self.send_header("X-Frame-Options", "SAMEORIGIN")
            else:
                self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", f"{CSP_BASE}; frame-ancestors '{frame}'{extra_csp}")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj, mask: bool = False):
            obj = maskmod.clean(obj, desk.root, mask)
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _file(self, base: Path, rel: str, frame: str = "none", extra_csp: str = "", allowed: set | None = None):
            try:
                p = (base / rel).resolve()
                p.relative_to(base.resolve())
            except (ValueError, OSError):
                return self._json(404, {"ok": False, "error": "없는 주소"})
            if not p.is_file() or (allowed is not None and p.suffix.lower() not in allowed):
                return self._json(404, {"ok": False, "error": "없는 주소"})
            ctype = TYPES.get(p.suffix.lower(), "application/octet-stream")
            body = desk.html(p) if p.suffix.lower() == ".html" else p.read_bytes()
            self._send(200, body, ctype, frame, extra_csp)

        # ---- 검사 ----
        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in desk.allowed_hosts

        def _write_ok(self) -> str | None:
            org = self.headers.get("Origin")
            if org is not None and org not in desk.allowed_origins:
                return "다른 주소에서 온 쓰기 요청"
            tok = self.headers.get("X-Desk-Token") or ""
            if not hmac.compare_digest(tok.encode(), desk.token.encode()):
                return "토큰이 없거나 맞지 않음"
            return None

        # ---- 처리 ----
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._deny()

        def do_DELETE(self):
            self._deny()

        def _deny(self):
            if not self._host_ok():
                return self._json(403, {"ok": False, "error": "허용하지 않는 Host"})
            self._json(405, {"ok": False, "error": "GET · POST 만"})

        def _handle(self, method: str):
            try:
                if not self._host_ok():
                    return self._json(403, {"ok": False, "error": "허용하지 않는 Host"})
                u = urlparse(self.path)
                path = unquote(u.path)
                q = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
                is_mask = q.get("mask", "") not in ("", "0", "false")
                body: dict = {}
                if method == "POST":
                    why = self._write_ok()
                    if why:
                        return self._json(403, {"ok": False, "error": why})
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        return self._json(413, {"ok": False, "error": "본문이 너무 큼"})
                    ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if n and ctype != "application/json":
                        return self._json(415, {"ok": False, "error": "본문은 JSON 만"})
                    raw = self.rfile.read(n) if n else b""
                    try:
                        body = json.loads(raw.decode("utf-8")) if raw else {}
                    except (ValueError, UnicodeDecodeError):
                        return self._json(400, {"ok": False, "error": "JSON 을 읽지 못함"})
                    if not isinstance(body, dict):
                        return self._json(400, {"ok": False, "error": "본문은 JSON 객체"})
                if method == "GET":
                    if path in ("/", "/index.html"):
                        return self._file(STATIC, "shell.html")
                    if path.startswith("/static/"):
                        return self._file(STATIC, path[len("/static/"):], allowed={".css", ".js", ".html", ".svg", ".png"})
                    if path.startswith("/fonts/"):
                        return self._file(STATIC / "fonts", path[len("/fonts/"):], allowed={".woff2", ".txt"})
                    if path.startswith("/tabs/"):
                        parts = path[len("/tabs/"):].split("/", 1)
                        t = desk.tab_by_id.get(parts[0])
                        if t is None or len(parts) < 2 or not parts[1]:
                            return self._json(404, {"ok": False, "error": "없는 탭"})
                        fs = getattr(t, "frame_src", "")
                        return self._file(t.folder, parts[1], frame="self", extra_csp=fs, allowed=TAB_FILES)
                    if path == "/api/tabs":
                        return self._json(200, desk.tabs_info())
                if path.startswith("/api/"):
                    return self._api(method, path[len("/api/"):], q, body, is_mask)
                return self._json(404, {"ok": False, "error": "없는 주소"})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as e:                           # 어떤 요청의 오류도 서버를 멈추지 않는다
                try:
                    self._json(500, {"ok": False, "error": f"서버 오류: {type(e).__name__} {desk.ctx.scrub(str(e))[:160]}"})
                except Exception:
                    pass

        def _api(self, method: str, rest: str, q: dict, body: dict, is_mask: bool):
            tid, _, name = rest.partition("/")
            t = desk.tab_by_id.get(tid)
            if t is None:
                return self._json(404, {"ok": False, "error": "없는 탭"})
            if not t.ok:
                return self._json(200, {"ok": False, "missing": t.missing, "error": "자료 없음: " + ", ".join(t.missing)})
            fn = t.routes.get(f"{method} {name}")
            if fn is None:
                return self._json(404 if method == "GET" else 405, {"ok": False, "error": "없는 주소"})
            out = fn(desk.ctx, Req(method, q, body, is_mask))
            code = 200
            if isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], int):
                code, out = out
            return self._json(code, out, is_mask)

    return H
