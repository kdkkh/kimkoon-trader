"""가짜 견본 만들기 — 이 폴더의 견본 파일을 고정 시드로 다시 만든다(네트워크 · 키 없음). 저장소 맨 위에서:
    python -X utf8 sample_data/make_samples.py
여기서 만드는 값은 모두 가짜다: 종목 코드 999901 · 999902 는 실제 종목이 아니고, 날짜는 2097 · 2099 년, 가격은 랜덤워크.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from strategy import live  # noqa: E402
from strategy.backtest import run_backtest  # noqa: E402
from strategy.ported import load_strategy  # noqa: E402
from strategy.validate import sample_bars  # noqa: E402

KST = "Asia/Seoul"
CODES = ("999901", "999902")                 # 가짜 종목 코드(실제 종목 아님)
DAYS = ("2099-01-02", "2099-01-05")          # 가짜 날짜(금 · 월). 둘째 날을 재생하고 첫날은 지표 예열
REPLAY_DAY = "20990105"


def minute_bars(seed: int, start_px: float) -> pd.DataFrame:
    """09:00~15:19 1분봉(하루 380개) 이틀치 — 30분마다 오름 · 내림이 바뀌는 랜덤워크."""
    rng = np.random.default_rng(seed)
    out, px = [], start_px
    for d in DAYS:
        t = pd.date_range(f"{d} 09:00", f"{d} 15:19", freq="min", tz=KST)
        drift = np.repeat(rng.choice([-0.0009, 0.0009], size=len(t) // 30 + 1), 30)[:len(t)]
        ret = drift + rng.normal(0, 0.0012, len(t))
        close = px * np.exp(np.cumsum(ret))
        open_ = np.r_[px, close[:-1]]
        hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0006, len(t))))
        lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0006, len(t))))
        out.append(pd.DataFrame({"time": t, "open": np.round(open_), "high": np.round(hi), "low": np.round(lo),
                                 "close": np.round(close), "volume": rng.integers(50, 5000, len(t)).astype(float)}))
        px = float(close[-1])
    return pd.concat(out, ignore_index=True)


def tv_trades(bars: pd.DataFrame, n: int = 4) -> pd.DataFrame:
    """이 프로그램의 봉 백테스트 결과 마지막 n 거래를 트레이딩뷰 거래 목록(List of trades) 열 이름 · 순서로 적는다.
    (트레이딩뷰에서 내보낸 것이 아니다 — 대조 검증기를 키 없이 돌려 보는 형식 견본)."""
    st = load_strategy("supertrend")
    res = run_backtest(bars, st.run(bars), amount=1_000_000)
    tr = res.trades[res.trades["상태"] == "닫힘"].tail(n)
    recs, cums, cum = tr.to_dict("records"), [], 0.0
    for t in recs:
        cum += t["손익"]
        cums.append(cum)
    rows = []
    for t, cum in reversed(list(zip(recs, cums))):          # 최근 거래가 위, 거래 안에서 청산 줄이 위
        common = {"Trade #": int(t["거래번호"]), "Position size (qty)": int(t["수량"]),
                  "Position size (value)": round(t["진입가"] * t["수량"]), "Net P&L KRW": round(t["손익"]),
                  "Net P&L %": round(t["수익률%"], 2), "Cumulative P&L KRW": round(cum)}
        rows.append({**common, "Type": "Exit long", "Date/Time": t["청산시각"], "Signal": "Short", "Price KRW": t["청산가"]})
        rows.append({**common, "Type": "Entry long", "Date/Time": t["진입시각"], "Signal": "Long", "Price KRW": t["진입가"]})
    cols = ["Trade #", "Type", "Date/Time", "Signal", "Price KRW", "Position size (qty)", "Position size (value)",
            "Net P&L KRW", "Net P&L %", "Cumulative P&L KRW"]
    return pd.DataFrame(rows, columns=cols)


def judge_samples() -> tuple[list[dict], list[dict]]:
    """가짜 판단 기록(logs/judge_*.jsonl 꼴) 두 줄 + 그것을 재생 입력으로 바꾼 것(judge.replay 와 같은 결과)."""
    def ms(hm: str) -> int:
        return int(pd.Timestamp(f"{DAYS[1]} {hm}", tz=KST).timestamp() * 1000)
    g_at, s_at = ms("10:00:05"), ms("11:45:05")
    recs = [
        {"ts_ms": g_at, "at": f"{DAYS[1]} 10:00:05.000", "event": "judge", "ref": "market_regime-sample-1",
         "judge": "market_regime", "trigger": {"kind": "every_min", "key": "10:00", "codes": list(CODES)},
         "model": {"provider": "fake", "name": "fake"}, "answer": {"answer": "risk_off", "reason": "가짜 응답(견본)"},
         "valid": True, "fake": True, "dry": False, "provider": "fake",
         "outputs": [{"kind": "gate", "status": "written", "at_ms": g_at, "targets": ["sim"],
                      "obj": {"name": "market_regime", "ttl_s": 1800, "reason": "AI 판단 risk_off(가짜 견본)",
                              "ref": "market_regime-sample-1"}}]},
        {"ts_ms": s_at, "at": f"{DAYS[1]} 11:45:05.000", "event": "judge", "ref": "sample_signal-sample-1",
         "judge": "sample_signal", "trigger": {"kind": "once", "key": "sample", "codes": [CODES[1]]},
         "model": {"provider": "fake", "name": "fake"}, "answer": {"score": 80, "reason": "가짜 응답(견본)"},
         "valid": True, "fake": True, "dry": False, "provider": "fake",
         "outputs": [{"kind": "signal", "status": "written", "at_ms": s_at, "targets": ["sim"],
                      "obj": {"id": "sample-signal-1", "source": "ai:sample_signal", "code": CODES[1], "action": "entry",
                              "ttl_s": 120, "amount": 500_000, "exit_by": "signal", "max_hold_min": 15,
                              "ref": "sample_signal-sample-1", "note": "가짜 견본 신호"}}]},
    ]
    from judge.replay import convert
    lines, _ = convert(recs)
    return recs, lines


def fake_run_log(bars: dict[str, pd.DataFrame], replay_path: Path, out: Path) -> Path:
    """봉 재생으로 만든 가짜 실행 기록(기록의 시각 칸도 가상 시계 = 가짜 날짜로)."""
    from replay.bars import BarReplayRunner

    holder = {}

    class VClock(datetime):
        @classmethod
        def now(cls, tz=None):
            r = holder.get("r")
            t = r.t_ms if r is not None and r.t_ms else int(pd.Timestamp(f"{DAYS[1]} 08:59", tz=KST).timestamp() * 1000)
            return datetime.fromtimestamp(t / 1000)
    t0 = int(pd.Timestamp(f"{DAYS[1]} 08:59", tz=KST).timestamp() * 1000)
    live.now_ms = lambda: (holder["r"].t_ms or t0) if holder.get("r") is not None else t0
    live.datetime = VClock
    r = BarReplayRunner(list(CODES), load_strategy("supertrend"), bars, REPLAY_DAY, out_dir=out, ext_replay=replay_path,
                        amount=1_000_000, max_orders=5, daily_loss_limit=30_000)
    holder["r"] = r
    r.run_day()
    return r.log.path


def main() -> int:
    daily = sample_bars(400, seed=7, start="2097-01-02")
    daily.to_csv(HERE / "fake_daily_bars.csv", index=False)
    tv_trades(daily).to_csv(HERE / "fake_tv_trades.csv", index=False)
    mb = {c: minute_bars(11 + i, 20_000 + 30_000 * i) for i, c in enumerate(CODES)}
    pd.concat([b.assign(code=c) for c, b in mb.items()], ignore_index=True)[
        ["code", "time", "open", "high", "low", "close", "volume"]].to_csv(HERE / "fake_minute_bars.csv", index=False)
    recs, lines = judge_samples()
    (HERE / "fake_judge_log.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in recs),
                                               encoding="utf-8")
    rp = HERE / "fake_judge_replay.jsonl"
    rp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="sample_run_log_"))
    log = fake_run_log(mb, rp, tmp)
    keep = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    keep = [e for e in keep if e.get("event") != "bar"]          # 봉 사건은 빼고(크기)
    (HERE / "fake_run_log.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in keep),
                                             encoding="utf-8")
    print("만듦:", ", ".join(p.name for p in sorted(HERE.glob("fake_*"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
