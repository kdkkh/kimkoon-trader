# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""가짜 견본으로 전 기능 한 바퀴 — 키 · 네트워크 없이(바깥 API · 텔레그램 · AI 를 부르지 않음).

    python -X utf8 scripts/sample_run.py              임시 작업 폴더에 이 프로그램을 복사해 그 안에서 돌린다(이 폴더에는 쓰지 않음)
    python -X utf8 scripts/sample_run.py --work <빈 폴더> --keep    작업 폴더를 남긴다(뒤에 화면으로 볼 때)
         → 끝에 나오는 명령으로 화면을 띄우면 매매 · 백테스트 · AI 판단 탭이 이 견본 기록을 그린다.
차례
  1 봉 백테스트       scripts/strategy_backtest.py --csv sample_data/fake_daily_bars.csv
  2 대조 검증기       scripts/strategy_validate.py supertrend --csv … --tv-trades sample_data/fake_tv_trades.csv → '통과'
  3 봉 재생(문 꺼짐)  scripts/bar_replay.py --csv sample_data/fake_minute_bars.csv — 진입 시각을 보고 4 의 판단 시각을 고른다
  4 간단 시뮬레이션 하루(가짜 봉 · 가상 시계, 오늘 날짜로) + 문 켬 + 가짜 판단
       시황 판단(market_regime, 가짜 답 risk_off) → 거르기 → 그 다음 전략 진입이 막힘(entry_filtered)
       특정 상황 판단(event_watch 의 주석 예시 신호 쓰임을 켠 것, 가짜 답 82점) → 문에 진입 신호 → 실행기가 위험 점검 뒤 진입
  5 기록한 판단 다시 틀기: logs/judge_*.jsonl → python -m judge.replay → 봉 재생 두 번 → scripts/compare_runs.py 로 0줄 차이
  6 텔레그램 봇(가짜 HTTP · 가짜 띄우개): /켜기 시뮬 → /확인 → /설정 amount → /확인 → /사기 · /팔기 → /확인 → /끄기
판단의 가상 시계: 문 · 판단 기록 · 조작 기록이 '오늘 기록'을 보므로 4 는 견본 봉의 시각을 오늘 날짜로 옮겨 돌린다(값은 그대로 가짜).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CODES = ("999901", "999902")                 # 가짜 종목 코드
FAKE_TOKEN = "1234567890:" + "F" * 35         # 가짜 봇 토큰(텔레그램 서버를 부르지 않음)
OWNER = 777                                  # 가짜 텔레그램 id
COPY_SKIP = {"logs", "state", "data", ".git", "__pycache__", ".pytest_cache", ".venv"}


def say(n, ok, text):
    print(f"  {n} {'통과' if ok else '실패'} — {text}")
    return bool(ok)


def run(args, cwd):
    r = subprocess.run([sys.executable, "-X", "utf8", "-B", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8")
    return r.returncode, (r.stdout + r.stderr)


def copy_tree(dst: Path) -> None:
    def ign(d, names):
        return [n for n in names if n in COPY_SKIP or n.endswith(".pyc")]
    shutil.copytree(HERE, dst, ignore=ign, dirs_exist_ok=True)


# ---------------- 4 · 5 · 6 은 작업 폴더 안에서(--inside) ----------------
def inside(root: Path) -> int:
    sys.path.insert(0, str(root))
    import pandas as pd

    from control import ext_write, state
    from control.common import log_path
    from judge import defs as jdefs
    from judge import limits, replay as jreplay
    from judge.engine import Engine
    from judge.inputs import FakeSources
    from replay.bars import BarReplayRunner, read_bars
    from strategy import live
    from strategy.ported import load_strategy

    oks = []
    today = datetime.now().strftime("%Y%m%d")
    bars = read_bars([root / "sample_data" / "fake_minute_bars.csv"])
    last = max(b["time"].iloc[-1] for b in bars.values()).normalize()
    shift = pd.Timestamp(today, tz="Asia/Seoul") - last                     # 견본 마지막 날 → 오늘
    bars = {c: b.assign(time=b["time"] + shift) for c, b in bars.items()}
    (root / "config.yaml").write_text(
        (root / "config.example.yaml").read_text(encoding="utf-8").replace("codes: []", f'codes: ["{CODES[0]}", "{CODES[1]}"]', 1),
        encoding="utf-8")

    # 3 에서 고른 판단 시각(첫 전략 진입 한 봉 앞)
    plan = json.loads((root / "sample_plan.json").read_text(encoding="utf-8"))
    gate_at = pd.Timestamp(f"{today} {plan['gate_hm']}", tz="Asia/Seoul")
    sig_at = pd.Timestamp(f"{today} {plan['signal_hm']}", tz="Asia/Seoul")

    # ---- 4 간단 시뮬레이션 하루 + 문 + 가짜 판단 ----
    defs = [jdefs.load_file(root / "judgments" / "market_regime.example.yaml")]
    ev_text = (root / "judgments" / "event_watch.example.yaml").read_text(encoding="utf-8").replace(
        "  # - {kind: signal", "  - {kind: signal", 1).replace("amount: 1000000", "amount: 500000", 1)
    p = root / "state" / "sample_defs" / "event_watch_signal.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ev_text, encoding="utf-8")
    defs.append(jdefs.load_file(p))
    g = limits.load_global(root)
    g["send"]["news_titles"] = True                                       # 가짜 뉴스(바깥 조회 없음)로 특정 상황 판단을 부르려고
    holder = {}
    clock = lambda: holder["r"].t_ms                                       # noqa: E731
    ext_write.now_ms = clock                                               # 문에 쓰는 만료 시각도 가상 시계로
    fired = {"gate": None, "signal": None}

    def on_bar(r, close_ms):
        r.beat(force=True)                                                 # 살아 있음 표시(문 쓰기 조건)
        now = pd.Timestamp(r.t_ms, unit="ms", tz="UTC").tz_convert("Asia/Seoul")
        if fired["gate"] is None and now >= gate_at:
            fired["gate"] = eng.once(["market_regime"])[0]
        if fired["signal"] is None and now >= sig_at and not r.oms.held(CODES[1]) and not r.oms.active_for(CODES[1]):
            fired["signal"] = eng.ask(defs[1], {"kind": "new_news", "key": "sample", "codes": [CODES[1]]})

    r = BarReplayRunner(list(CODES), load_strategy("supertrend"), bars, today, out_dir=root / "logs" / "sim",
                        state_dir=root / "state" / "sim", ext=True, amount=1_000_000, max_amount=1_000_000,
                        max_orders=50, daily_loss_limit=1_000_000)          # 견본이라 상한을 넉넉히(판단 신호가 상한에 막히지 않게)
    holder["r"] = r
    eng = Engine(root, defs, g, sources=FakeSources(today), notify=None, run_async=False, clock=clock,
                 say=lambda *a: None)
    s = r.run_day(on_bar=on_bar)
    (root / "state" / "sim" / "heartbeat.json").unlink(missing_ok=True)     # 하루 끝 — 실행기 꺼짐
    ev = [json.loads(x) for x in log_path(root, "sim").read_text(encoding="utf-8").splitlines() if x.strip()]
    cnt = {k: sum(1 for e in ev if e["event"] == k) for k in ("signal", "order_plan", "fill", "entry_filtered",
                                                              "ext_signal", "risk_block")}
    gw = next((o for o in (fired["gate"] or {}).get("outputs", []) if o["kind"] == "gate"), {})
    sw = next((o for o in (fired["signal"] or {}).get("outputs", []) if o["kind"] == "signal"), {})
    ext_orders = [e for e in ev if e["event"] == "order_intent" and str(e.get("source", "")).startswith("ai:")]
    oks.append(say(4, gw.get("status") == "written" and sw.get("status") == "written" and cnt["entry_filtered"] >= 1
                   and cnt["ext_signal"] == 1 and len(ext_orders) == 1,
                   f"간단 시뮬레이션 하루: 신호 {cnt['signal']} · 체결 {cnt['fill']} · 실현 {s['realized']:,.0f}원 · "
                   f"거르기 {gw.get('status')} → 막힌 진입 {cnt['entry_filtered']} · 판단 신호 {sw.get('status')} → "
                   f"받은 신호 {cnt['ext_signal']} · 판단 출처 주문 {len(ext_orders)}"))

    # ---- 5 기록한 판단 다시 틀기 두 번 ----
    jl = root / "logs" / f"judge_{today}.jsonl"
    rp = root / "state" / "sample_replay.jsonl"
    rc, out = run(["-m", "judge.replay", str(jl), "--out", str(rp)], root)
    outs = []
    for k in ("a", "b"):
        o = root / "data" / f"bt_replay_sample_{k}"
        rr = BarReplayRunner(list(CODES), load_strategy("supertrend"), bars, today, out_dir=o / "all", ext_replay=rp,
                             amount=1_000_000, max_amount=1_000_000, max_orders=5, daily_loss_limit=30_000)
        rr.run_day()
        rr.round_trips().to_csv(o / "round_trips.csv", index=False, encoding="utf-8-sig")
        outs.append(o)
    rc2, cmp = run(["scripts/compare_runs.py", str(outs[0]), str(outs[1])], root)
    n_lines = sum(1 for _ in rp.open(encoding="utf-8")) if rp.exists() else 0
    oks.append(say(5, rc == 0 and rc2 == 0 and n_lines >= 2,
                   f"판단 기록 → 재생 입력 {n_lines}줄 → 봉 재생 두 번 비교 = {cmp.strip().splitlines()[-1] if cmp.strip() else '?'}"))

    # ---- 6 텔레그램 봇(가짜 HTTP) ----
    import importlib.util

    from notify import telegram
    spec = importlib.util.spec_from_file_location("bot_script", root / "scripts" / "bot.py")
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)

    class Resp:
        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    class FakeHTTP:
        def __init__(self):
            self.updates, self.sent = [], []

        def post(self, url, json=None, timeout=None):
            if url.endswith("/getUpdates"):
                return Resp({"ok": True, "result": self.updates.pop(0) if self.updates else []})
            self.sent.append(json["text"])
            return Resp({"ok": True, "result": {}})

    http = FakeHTTP()
    launched = []
    mon10 = datetime(2098, 3, 3, 10, 0)                                    # 휴장일 표에 없는 해의 월요일 10:00(장 중으로 봄)
    b = bot.Bot(root, {"trade_enabled": True, "trade_target": "sim"}, launcher=lambda a, cwd: launched.append(a),
                market_now=lambda: mon10, code_fn=lambda: "123456", run_async=False)
    loop = bot.BotLoop(telegram.Telegram(FAKE_TOKEN, OWNER, session=http, min_interval=0, sleep=lambda s: None),
                       b, bot.BotLog(), sleep=lambda s: None, started=0)
    uid = [0]

    def send(text):
        uid[0] += 1
        http.updates.append([{"update_id": uid[0], "message": {"from": {"id": OWNER}, "chat": {"type": "private"},
                                                                 "text": text, "date": int(time.time()) + 5}}])
        n0 = len(http.sent)
        loop.tick(wait_s=0)
        return " / ".join(http.sent[n0:])

    a1 = send("/켜기 시뮬")
    a2 = send("/확인 123456")
    a3 = send("/설정 amount 500000")
    a4 = send("/확인 123456")
    cfg_amount = "amount: 500000" in (root / "config.yaml").read_text(encoding="utf-8")
    # 문 쓰기는 '문을 켜고 돌고 있는 실행기'가 있을 때만 → 시뮬레이션 실행기가 막 뜬 흔적(heartbeat + 오늘 start 사건)
    hb = state.runner_paths(root, "sim")["heartbeat"]
    hb.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "broker": "sim", "codes": list(CODES)}),
                  encoding="utf-8")
    with log_path(root, "sim").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts_ms": int(time.time() * 1000), "event": "start", "codes": list(CODES), "ext": True,
                            "max_amount": 1_000_000, "tf": 1, "settle": 3, "mode": "sample"}) + "\n")
    a5 = send(f"/사기 {CODES[0]} 300000")
    a6 = send("/확인 123456")
    a7 = send(f"/팔기 {CODES[0]}")
    a8 = send("/확인 123456")
    inbox = sorted((state.runner_paths(root, "sim")["ext"] / "inbox").glob("*.json"))
    acts = sorted(json.loads(x.read_text(encoding="utf-8"))["action"] for x in inbox)
    a9 = send("/끄기 시뮬")
    stop = state.runner_paths(root, "sim")["stop"].exists()
    hb.unlink(missing_ok=True)
    leak = [t for t in http.sent if FAKE_TOKEN in t or str(root) in t]
    oks.append(say(6, len(launched) == 1 and cfg_amount and acts == ["entry", "exit"] and stop and not leak,
                   f"봇: 켜기 → 띄우개 {len(launched)}번 · 설정 → config.yaml amount 500000 {'바뀜' if cfg_amount else '안 바뀜'} · "
                   f"사고팔기 → 문에 {acts} · 끄기 → 정지 파일 {'있음' if stop else '없음'} · 보낸 글 {len(http.sent)}건"
                   f"(토큰 · 경로 {len(leak)}건)"))
    (root / "state" / "sim" / "stop").unlink(missing_ok=True)
    for t in (a1, a2, a3, a4, a5, a6, a7, a8, a9):
        print("      봇 답:", t[:150].replace("\n", " "))
    return 0 if all(oks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="가짜 견본으로 전 기능 한 바퀴(키 · 네트워크 없음)")
    ap.add_argument("--work", type=Path, default=None, help="작업 폴더(빈 폴더 · 없으면 만듦). 비우면 임시 폴더")
    ap.add_argument("--keep", action="store_true", help="끝난 뒤 작업 폴더를 남김")
    ap.add_argument("--inside", type=Path, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):                 # 출력을 파일 · 파이프로 받아도 단계 순서대로 나오게(줄마다 내보냄 —
        if hasattr(s, "reconfigure"):                  #   4~6단계는 자식 프로세스가 같은 출력에 바로 쓴다)
            s.reconfigure(line_buffering=True)
    if a.inside:
        return inside(a.inside.resolve())
    work = (a.work or Path(tempfile.mkdtemp(prefix="kimkoon_sample_"))).resolve()
    if work.exists() and any(work.iterdir()):
        print(f"작업 폴더가 비어 있지 않습니다: {work.name}")
        return 2
    copy_tree(work)
    print(f"[견본 한 바퀴] 작업 폴더에 복사함 · 바깥 호출 없음")
    oks = []
    rc, out = run(["scripts/strategy_backtest.py", "--csv", "sample_data/fake_daily_bars.csv", "--strategy", "supertrend"], work)
    line = next((x for x in out.splitlines() if "총 손익" in x), out.strip().splitlines()[-1] if out.strip() else "")
    oks.append(say(1, rc == 0, f"봉 백테스트: {line.strip()[:110]}"))
    rc, out = run(["scripts/strategy_validate.py", "supertrend", "--csv", "sample_data/fake_daily_bars.csv",
                   "--tv-trades", "sample_data/fake_tv_trades.csv"], work)
    verdict = next((x for x in out.splitlines() if "판정" in x), "")
    oks.append(say(2, rc == 0 and "통과" in verdict, f"대조 검증기: {verdict.strip()[:110]}"))
    rc, out = run(["scripts/bar_replay.py", "--csv", "sample_data/fake_minute_bars.csv", "--out", "data/bt_replay_sample_plain"],
                  work)
    first = None
    lg = sorted((work / "data" / "bt_replay_sample_plain" / "all").glob("strategy_live_*.jsonl"))
    if lg:
        for x in lg[0].read_text(encoding="utf-8").splitlines():
            e = json.loads(x)
            if e["event"] == "signal" and e["kind"] == "long_entry" and e["bar_time"][11:16] >= "10:00":
                first = e["bar_time"][11:16]
                break
    oks.append(say(3, rc == 0 and first, f"봉 재생(문 꺼짐): {out.strip().splitlines()[-2].strip() if rc == 0 else out[-200:]} · "
                                          f"10시 뒤 첫 전략 진입 신호 봉 {first}"))
    if first:
        t = datetime.strptime(first, "%H:%M")
        gate_hm = f"{t.hour:02d}:{max(t.minute - 3, 0):02d}" if t.minute >= 3 else f"{t.hour - 1:02d}:{t.minute + 57:02d}"
        sig_hm = "13:30"
        (work / "sample_plan.json").write_text(json.dumps({"gate_hm": gate_hm, "signal_hm": sig_hm}), encoding="utf-8")
        rc = subprocess.run([sys.executable, "-X", "utf8", "-B", str(work / "scripts" / "sample_run.py"), "--inside", str(work)],
                            cwd=work).returncode
        oks.append(rc == 0)
    print("  결과:", "모두 통과" if all(oks) else "실패 있음")
    if a.keep:
        print(f"  작업 폴더를 남김 — 화면: python -X utf8 scripts/desk.py --root <작업 폴더>  (작업 폴더 이름 {work.name})")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if all(oks) else 1


if __name__ == "__main__":
    sys.exit(main())
