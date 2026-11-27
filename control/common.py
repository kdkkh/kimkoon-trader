"""control 공통 — 실행기별 경로 · 안전한 파일 읽기 · 문구 가림 · 조작 기록(logs/control_YYYYMMDD.jsonl)."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 실행기 = (state 폴더, 기록 폴더, 기록 파일 머리, 장부 파일, 잠금 파일, 연결 이름들)
#   paper = 한투 모의 실행기(strategy_live.py --paper-order) · sim = 실시간 시뮬레이션(sim_live.py)
#   dry = 주문 계획만(strategy_live.py 기본 · --once) — 상태 보기 · 시험용
RUNNERS = {
    "paper": {"state": ("state",), "logs": ("logs",), "prefix": "strategy_live_", "oms": "oms_paper.json",
              "lock": "run_paper.lock", "brokers": ("kis-paper",), "label": "모의"},
    "sim": {"state": ("state", "sim"), "logs": ("logs", "sim"), "prefix": "strategy_live_", "oms": "oms_paper.json",
            "lock": "run_paper.lock", "brokers": ("sim",), "label": "시뮬"},
    "dry": {"state": ("state",), "logs": ("logs",), "prefix": "strategy_live_dry_", "oms": "oms_dry.json",
            "lock": "run_dry.lock", "brokers": ("dry-run",), "label": "dry-run", "heartbeat": "heartbeat_dry.json"},
}
# heartbeat 파일: 기본 heartbeat.json, dry-run 은 heartbeat_dry.json(모의와 같은 state 폴더라 따로 — strategy/live.py 와 같게).
# 문 폴더(ext)는 dry-run 도 state/ext/ 로 모의와 같다 — ext_write 가 모의가 돌 때는 dry-run 문에 쓰지 않는다.
_SIM_TAG = re.compile(r"^sim_[a-z0-9]{1,20}$")
_HOME_RX = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'`]+", re.I)
_ACCOUNT_RX = re.compile(r"(?<!\d)\d{8}-?\d{2}(?!\d)")
_TOKEN_RX = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,}")
_journal_lock = threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def known_target(target: str) -> bool:
    return target in RUNNERS or bool(_SIM_TAG.match(str(target)))


def runner_paths(root: Path, target: str) -> dict:
    """target = paper · sim · dry · sim_<태그>(예: sim_after). 모르는 이름이면 ValueError."""
    root = Path(root)
    if target in RUNNERS:
        spec = RUNNERS[target]
    elif _SIM_TAG.match(str(target)):
        spec = {**RUNNERS["sim"], "state": ("state", target), "logs": ("logs", target), "label": f"시뮬({target[4:]})"}
    else:
        raise ValueError(f"모르는 실행기: {str(target)[:20]}")
    state = root.joinpath(*spec["state"])
    logs = root.joinpath(*spec["logs"])
    return {"target": target, "label": spec["label"], "state": state, "logs": logs, "prefix": spec["prefix"],
            "oms": state / spec["oms"], "lock": state / spec["lock"],
            "heartbeat": state / spec.get("heartbeat", "heartbeat.json"),
            "stop": state / "stop", "ext": state / "ext", "brokers": spec["brokers"]}


def log_path(root: Path, target: str, day: str | None = None) -> Path:
    p = runner_paths(root, target)
    return p["logs"] / f"{p['prefix']}{day or datetime.now().strftime('%Y%m%d')}.jsonl"


def scrub(text) -> str:
    """기록 · 답에 넣기 전에 사용자 폴더 경로 · 계좌번호 꼴 · 봇 토큰 꼴을 가린다."""
    s = str(text)
    home = str(Path.home())
    for h in (home, home.replace("\\", "/")):
        if h and len(h) > 3:
            s = s.replace(h, "~")
    s = _HOME_RX.sub("~", s)
    s = _TOKEN_RX.sub("<토큰>", s)
    return _ACCOUNT_RX.sub("<계좌>", s)


def read_json(p: Path, default=None):
    """깨졌거나 없으면 default(기본 {}). 어떤 예외도 내지 않는다."""
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default
    return d


def read_events(p: Path, names: set | tuple | None = None) -> list[dict]:
    """jsonl 기록 → 사건 목록. 반쯤 쓴 줄 · 깨진 줄 · 반쪽 한글은 건너뛴다(예외 없음)."""
    try:
        raw = Path(p).read_bytes()
    except Exception:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and (names is None or r.get("event") in names):
            out.append(r)
    return out


def journal(root: Path, action: str, **kw) -> dict:
    """조작 시도 한 줄을 logs/control_YYYYMMDD.jsonl 에 남긴다(실패해도 예외를 내지 않음)."""
    rec = {"ts_ms": now_ms(), "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "action": action}
    for k, v in kw.items():
        rec[k] = scrub(v) if isinstance(v, str) else v
    try:
        d = Path(root) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _journal_lock, (d / f"control_{datetime.now():%Y%m%d}.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    return rec


def journal_today(root: Path) -> list[dict]:
    p = Path(root) / "logs" / f"control_{datetime.now():%Y%m%d}.jsonl"
    out = []
    try:
        for line in p.read_bytes().decode("utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except Exception:
        pass
    return out


def replace_retry(tmp: Path, dst: Path, tries: int = 20) -> None:
    """os.replace — 윈도우에서 상대가 대상 파일을 열고 있으면 PermissionError 이므로 짧게 다시 한다."""
    for i in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            time.sleep(min(0.02 * (i + 1), 0.2))
    os.replace(tmp, dst)                                   # 마지막 한 번 — 실패하면 그 예외를 낸다
