# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""봉 재생(replay/bars.py · scripts/bar_replay.py) · 가짜 견본(sample_data/) 시험 — 네트워크 · 키 없음."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from judge.replay import convert  # noqa: E402
from replay.bars import BarReplayRunner, read_bars  # noqa: E402
from strategy.ported import load_strategy  # noqa: E402
from strategy.validate import sample_bars  # noqa: E402

SD = ROOT / "sample_data"
DAY = "20990105"
CODES = ["999901", "999902"]


def _run(tmp, name, ext=None, **kw):
    r = BarReplayRunner(CODES, load_strategy("supertrend"), read_bars([SD / "fake_minute_bars.csv"]), DAY,
                        out_dir=tmp / name, ext_replay=ext, **kw)
    s = r.run_day()
    return r, s, [json.loads(x) for x in r.log.path.read_text(encoding="utf-8").splitlines()]


def _compare():
    spec = importlib.util.spec_from_file_location("compare_runs_t", ROOT / "scripts" / "compare_runs.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_samples_are_what_readme_says():
    d = pd.read_csv(SD / "fake_daily_bars.csv")
    assert len(d) == 400 and d["close"].round().tolist() == sample_bars(400, seed=7, start="2097-01-02")["close"].tolist()
    mb = pd.read_csv(SD / "fake_minute_bars.csv", dtype={"code": str})
    assert sorted(mb["code"].unique()) == CODES and mb["time"].str[:4].unique().tolist() == ["2099"]


def test_replay_runs_same_twice(tmp_path, monkeypatch):
    r1, s1, e1 = _run(tmp_path, "a")
    r2, s2, e2 = _run(tmp_path, "b")
    assert s1 == s2 and s1["entries_today"] >= 1
    assert any(e["event"] == "fill" for e in e1) and not any(e["event"] == "error" for e in e1)
    m = _compare()
    monkeypatch.setattr(sys, "argv", ["compare_runs.py", str(tmp_path / "a"), str(tmp_path / "b")])
    assert m.main() == 0


def test_replay_never_sees_future_bars(tmp_path):
    r = BarReplayRunner(CODES, load_strategy("supertrend"), read_bars([SD / "fake_minute_bars.csv"]), DAY,
                        out_dir=tmp_path / "f")
    seen = []
    r.run_day(on_bar=lambda rr, close_ms: seen.append(
        max(b["time"].iloc[-1] for b in rr.bars.values() if len(b)) + pd.Timedelta(minutes=1)
        <= pd.Timestamp(rr.t_ms, unit="ms", tz="UTC")))
    assert seen and all(seen)


def test_judge_log_converts_to_sample():
    recs = [json.loads(x) for x in (SD / "fake_judge_log.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    lines, n = convert(recs)
    want = [json.loads(x) for x in (SD / "fake_judge_replay.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines == want and n["gate"] == 1 and n["signal"] == 1


def test_replay_with_recorded_judgments(tmp_path):
    _, s, ev = _run(tmp_path, "x", ext=SD / "fake_judge_replay.jsonl")
    names = [e["event"] for e in ev]
    assert names.count("ext_signal") == 1 and "entry_filtered" in names
    oi = [e for e in ev if e["event"] == "order_intent" and e.get("source") == "ai:sample_signal"]
    assert len(oi) == 1 and oi[0]["code"] == "999902" and oi[0]["qty"] * oi[0]["current"] <= 500_000
    assert any(e["event"] == "ext_rule_exit" for e in ev)                       # 보유 15분 → 바깥 규칙 청산


def test_script_writes_round_trips(tmp_path):
    out = tmp_path / "bt"
    r = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts" / "bar_replay.py"), "--csv",
                        str(SD / "fake_minute_bars.csv"), "--day", DAY, "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    rt = pd.read_csv(out / "round_trips.csv", encoding="utf-8-sig")
    assert list(rt.columns) == ["code", "t_entry_ms", "t_exit_ms", "qty", "entry", "exit", "pnl", "ret_pct"]
    assert list((out / "all").glob("strategy_live_*.jsonl"))
    r2 = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts" / "bar_replay.py"), "--csv",
                         str(SD / "fake_minute_bars.csv"), "--out", str(out)], cwd=ROOT, capture_output=True, text=True,
                        encoding="utf-8")
    assert r2.returncode == 2 and "비어 있지 않습니다" in r2.stdout             # 남은 상태를 이어받지 않게
