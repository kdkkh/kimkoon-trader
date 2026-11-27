"""정리 단계 시험 — 판단 종료 때 검토 표시 걷기 · /검토끄기 · 판단 기본 대상(dry-run 뺌) · dry-run heartbeat 따로 ·
알림 몰림 줄이기 · 설정 미리 검사가 프로젝트에 쓰지 않음 · 문 폴더 정리 · 다시 띄울 때 남은 검토 요청 버림.
모두 임시 폴더 · 가짜 응답. 바깥 API 를 부르지 않는다."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from control import ext_write, state  # noqa: E402
from control import settings_edit as se  # noqa: E402
from judge import record  # noqa: E402
from notify import telegram  # noqa: E402
from strategy.ext import FileDoor  # noqa: E402
from tests.test_control import fake_running  # noqa: E402
from tests.test_judge import REVIEW_DEF, bar, eng, make_live, mkdef  # noqa: E402

spec = importlib.util.spec_from_file_location("bot_script_cleanup_t", ROOT / "scripts" / "bot.py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


def _review(root: Path, sub: tuple, ref: str = "judge:rv") -> Path:
    p = root.joinpath("state", *sub, "ext", "review.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"expires_ms": 1, "on_timeout": "skip", "ref": ref}), encoding="utf-8")
    return p


def _age(p: Path, days: float) -> None:
    t = time.time() - days * 86400
    os.utime(p, (t, t))


# ---------- 1. 검토 표시 ----------
def test_judge_stop_clears_own_review(tmp_path, monkeypatch):
    r, st = make_live(tmp_path, monkeypatch)
    d = mkdef(tmp_path, REVIEW_DEF.format(ans="pass"))
    bar(r, st)
    rv = tmp_path / "state" / "ext" / "review.json"

    def sleep(s):
        assert rv.exists()                                     # 돌고 있는 동안은 표시가 있다
        (tmp_path / "state" / "judge.stop").write_text("", encoding="utf-8")
    assert eng(tmp_path, [d], sleep=sleep).run(until=None, poll_s=0) == "stop file"
    assert not rv.exists()                                     # 정상으로 끝나면 자기가 쓴 표시를 걷는다
    stop = [x for x in record.read_day(tmp_path) if x.get("event") == "stop"][-1]
    assert stop["review_cleared"] == ["dry"]


def test_clear_review_keeps_other_ref(tmp_path):
    p = _review(tmp_path, (), ref="judge:other")
    r = ext_write.clear_review(tmp_path, "paper", ref="judge:rv")
    assert r["ok"] and p.exists() and "남김" in r["results"][0]["why"]
    assert ext_write.clear_review(tmp_path, "paper")["results"][0]["file"] == "review.json" and not p.exists()
    assert ext_write.clear_review(tmp_path, "paper")["results"][0]["why"] == "이미 없음"


def test_bot_review_off_no_confirm(tmp_path):
    a, b = _review(tmp_path, ()), _review(tmp_path, ("sim",))
    bt = bot.Bot(tmp_path, run_async=False)
    out = bt.handle("/검토끄기")
    assert not a.exists() and not b.exists() and bt.pending is None and "걷었습니다" in out
    assert "검토 표시 없음" in bt.handle("/검토끄기 모의")


def test_unavailable_text_says_how_to_off():
    s = telegram.format_event({"event": "review_unavailable", "on_timeout": "skip"})
    assert "진입하지 않음" in s and "/검토끄기" in s


# ---------- 2. 판단 기본 대상 · dry-run ----------
def test_judge_default_skips_dry(tmp_path, monkeypatch):
    make_live(tmp_path, monkeypatch)                           # 문을 켠 dry-run 실행기
    assert eng(tmp_path, [], dry_runner=False).open_targets() == []
    fake_running(tmp_path, "sim")
    assert eng(tmp_path, [], dry_runner=False).open_targets() == ["sim"]


def test_dry_door_refused_when_paper_up(tmp_path):
    fake_running(tmp_path, "dry")
    assert ext_write.write_gate(tmp_path, "dry", name="g", reason="시험", expires_s=60)["ok"]
    fake_running(tmp_path, "paper")                            # 모의가 같은 문(state/ext)을 쓰는 중
    r = ext_write.write_gate(tmp_path, "dry", name="g2", reason="시험", expires_s=60)
    assert not r["ok"] and "모의 실행기가 같은 문" in r["results"][0]["why"]


def test_dry_heartbeat_own_file(tmp_path, monkeypatch):
    r, st = make_live(tmp_path, monkeypatch)
    sd = tmp_path / "state"
    assert (sd / "heartbeat_dry.json").exists() and not (sd / "heartbeat.json").exists()
    (sd / "heartbeat.json").write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                                   "broker": "kis-paper"}), encoding="utf-8")
    r.finish("end")
    assert (sd / "heartbeat.json").exists() and not (sd / "heartbeat_dry.json").exists()


# ---------- 3. 알림 몰림 ----------
class _TG:
    def send(self, t):
        pass


def test_notifier_once_per_day_merged():
    n = telegram.Notifier(_TG(), start=False)
    at = "2026-11-24 10:00:00.000"
    for _ in range(3):
        for c in ("005930", "000660"):
            n({"event": "entry_filtered", "code": c, "gate": "market_regime: 위험 회피", "at": at})
    n({"event": "entry_filtered", "code": "005930", "gate": "market_regime: 이유 글이 바뀜", "at": at})
    for _ in range(2):
        n({"event": "review_expired", "code": "005930", "on_timeout": "skip", "at": at})
        n({"event": "ext_rejected", "reason": "만료됨", "source": "ai:event_watch", "at": at})
        n({"event": "ext_rejected", "reason": "만료됨", "source": "telegram", "at": at})
    msgs = n.drain_batch()
    filt = [m for m in msgs if "[거르기]" in m]
    assert len(filt) == 1 and filt[0].count("[거르기]") == 1 and "005930, 000660" in filt[0]
    assert sum(m.count("[검토 답 마감]") for m in msgs) == 1
    assert sum(m.count("[바깥 신호 버림]") for m in msgs) == 3       # ai 한 번 + 사람(telegram) 두 번
    assert n.suppressed == 7                                          # 거르기 5 · 검토 마감 1 · ai 버림 1
    n({"event": "entry_filtered", "code": "005930", "gate": "market_regime: x", "at": "2026-11-25 09:01:00.000"})
    assert len(n.drain_batch()) == 1                                  # 날이 바뀌면 다시 보낸다


# ---------- 4. 설정 미리 검사 ----------
def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob("*")}


def test_preview_writes_nothing_in_root(tmp_path):
    shutil.copy(ROOT / "config.example.yaml", tmp_path / "config.yaml")
    before = _tree(tmp_path)
    tmp_dir = Path(tempfile.gettempdir())
    left = set(tmp_dir.glob("kimkoon-config-preview-*"))
    r = se.preview({"amount": 700_000}, actor="desk", root=tmp_path)
    assert r["ok"] and r["diff"]["amount"][1] == 700_000, r
    assert se.preview({"amount": 9_000_000}, actor="desk", root=tmp_path)["ok"] is False
    assert _tree(tmp_path) == before                                  # 프로젝트(뿌리) 폴더에 아무것도 쓰지 않음
    assert set(tmp_dir.glob("kimkoon-config-preview-*")) == left       # 임시 파일도 남기지 않음


# ---------- 5. 문 폴더 정리 ----------
def test_cleanup_keeps_today_and_live(tmp_path):
    ext = tmp_path / "state" / "ext"
    for sub in ("inbox_done", "gates", "review_requests", "inbox"):
        (ext / sub).mkdir(parents=True)
    now_ms = int(time.time() * 1000)
    files = {
        "done_old": (ext / "inbox_done" / "a.json", "{}", 10),
        "done_recent": (ext / "inbox_done" / "b.json", "{}", 3),
        "done_today": (ext / "inbox_done" / "c.json", "{}", 0),
        "gate_old_dead": (ext / "gates" / "g1.json", json.dumps({"block_entries": True, "expires_ms": 1}), 2),
        "gate_old_live": (ext / "gates" / "g2.json", json.dumps({"block_entries": True, "expires_ms": now_ms + 3_600_000}), 1),
        "gate_today_dead": (ext / "gates" / "g3.json", json.dumps({"block_entries": True, "expires_ms": 1}), 0),
        "gate_old_broken": (ext / "gates" / "g4.json", "{", 2),
        "req_old": (ext / "review_requests" / "rv-1.json", "{}", 1),
        "req_today": (ext / "review_requests" / "rv-2.json", "{}", 0),
        "inbox_old": (ext / "inbox" / "s.json", "{}", 5),
        "review": (ext / "review.json", "{}", 5),
        "owned": (ext / "owned.json", "{}", 5),
    }
    for p, text, days in files.values():
        p.write_text(text, encoding="utf-8")
        if days:
            _age(p, days)
    r = ext_write.cleanup(tmp_path, "paper", keep_days=7)
    assert r["ok"] and r["results"][0]["removed"] == {"inbox_done": 1, "gates": 2, "review_requests": 1}
    gone = {k for k, (p, _, _) in files.items() if not p.exists()}
    assert gone == {"done_old", "gate_old_dead", "gate_old_broken", "req_old"}
    assert ext_write.main(["cleanup", "paper", "--root", str(tmp_path), "--keep-days", "1"]) == 0
    assert not files["done_recent"][0].exists() and files["done_today"][0].exists()


# ---------- 8. 다시 띄울 때 남은 검토 요청 ----------
class _Log:
    def __init__(self):
        self.recs = []

    def write(self, event, **kw):
        self.recs.append({"event": event, **kw})


def test_restart_discards_review_requests(tmp_path):
    req = tmp_path / "ext" / "review_requests" / "rv-005930-1.json"
    req.parent.mkdir(parents=True)
    req.write_text(json.dumps({"id": "rv-005930-1", "code": "005930"}), encoding="utf-8")
    log = _Log()
    FileDoor(tmp_path / "ext", log)
    assert not req.exists()
    assert [(x["event"], x["id"]) for x in log.recs] == [("review_cancelled", "rv-005930-1")]


# ---------- 9. 바깥이 산 것에 장부 수량 · 평균가 ----------
def test_door_state_owned_has_book_qty(tmp_path):
    sd = tmp_path / "state"
    (sd / "ext").mkdir(parents=True)
    (sd / "ext" / "owned.json").write_text(json.dumps({"005930": {"source": "telegram", "exit_by": "signal"}}),
                                           encoding="utf-8")
    (sd / "oms_paper.json").write_text(json.dumps({"positions": {"005930": {"qty": 7, "avg": 61300.4}}}),
                                       encoding="utf-8")
    o = state.door_state(tmp_path, "paper")["owned"]["005930"]
    assert o["qty"] == 7 and o["avg"] == 61300.4 and o["source"] == "telegram"
