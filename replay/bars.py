# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""봉 재생 실행기 — 실시간 실행기(strategy/live.py LiveRunner)를 지난 1분봉 위에서 가상 시계로 그대로 돌린다.

바꾸는 것은 셋뿐이고, 신호 → 위험 점검 → 문(거르기 · 바깥 신호) → 주문 방식 → 장부는 실시간과 같은 코드다.
  시계      봉 마감 + settle 초를 따라 흐르는 가상 시계(벽시계를 쓰지 않음)
  봉        한투 조회 대신 CSV 의 1분봉 — 그 순간까지 닫힌 봉만 보인다(미래 봉을 보지 않음)
  현재가    방금 닫힌 봉의 종가(신호 순간 현재가로 바로 체결 — 간단 시뮬레이션과 같은 규칙)
쓸모
  - 키 없이 전 과정을 돌려 보기(가짜 견본 sample_data/).
  - 기록해 둔 판단(logs/judge_*.jsonl → python -m judge.replay)을 다시 틀기: 같은 판단에 금액 · 설정을 바꾸면 어땠나.
    지난 날 자료로 모델에 새로 묻는 것은 검증이 아니다(결과를 아는 모델) — 이 재생은 기록한 답만 다시 튼다.
한계: 봉 안의 움직임 · 호가 · 체결 지연은 없다. 1분봉 종가 체결이라 실거래 · 모의 결과와 다를 수 있다.
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from oms.simple import InstantBroker, SimpleBook
from strategy import live
from strategy.base import Strategy

KST = "Asia/Seoul"


def read_bars(paths: list[Path], code: str | None = None) -> dict[str, pd.DataFrame]:
    """1분봉 CSV → {종목: 봉}. 열 time, open, high, low, close, volume(+ code 열이 있으면 종목별로 나눔).
    code 열이 없으면 code 인자(없으면 파일 이름)를 종목으로 쓴다. time 에 시간대가 없으면 한국 시간으로 읽는다."""
    out: dict[str, list[pd.DataFrame]] = {}
    for p in paths:
        df = pd.read_csv(p, dtype={"code": str})
        t = pd.to_datetime(df["time"])
        df["time"] = t.dt.tz_localize(KST) if t.dt.tz is None else t.dt.tz_convert(KST)
        if "code" in df.columns:
            for c, g in df.groupby("code"):
                out.setdefault(str(c).zfill(6), []).append(g.drop(columns="code"))
        else:
            out.setdefault(str(code or Path(p).stem), []).append(df)
    cols = ["time", "open", "high", "low", "close", "volume"]
    return {c: pd.concat(v)[cols].drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
            for c, v in out.items()}


class BarReplayRunner(live.LiveRunner):
    def __init__(self, codes: list[str], strategy: Strategy, bars: dict[str, pd.DataFrame], day: str,
                 out_dir: Path | None = None, ext_replay: Path | None = None, state_dir: Path | None = None, **kw):
        """day = 'YYYYMMDD'(그날 봉을 돌림, 앞 날짜 봉은 지표 예열로 보임). out_dir = 기록을 쓸 빈 폴더,
        state_dir = 장부 · 문 폴더(없으면 out_dir/state). ext=True 를 주면 파일 문(state_dir/ext)을 연다 — 견본 실행용."""
        self.t_ms = 0
        self.day = day
        self._all = {c: bars[c] for c in codes if c in bars}
        out = Path(out_dir or tempfile.mkdtemp(prefix=f"bar_replay_{day}_"))
        out.mkdir(parents=True, exist_ok=True)
        self.out_dir = out
        live.LOG_DIR, live.STATE_DIR = out, Path(state_dir or out / "state")   # 이 프로세스 안에서만 — 실시간 기록과 섞이지 않게
        live.STATE_DIR.mkdir(parents=True, exist_ok=True)
        kw.setdefault("printer", lambda *a: None)
        kw.pop("broker", None)
        kw.pop("book", None)
        super().__init__(list(self._all), strategy, broker=InstantBroker(), book=SimpleBook, **kw)
        self.oms.clock = self.clock_ms
        self.oms.today = lambda: day
        self.oms.day = day
        if ext_replay:                                         # 기록해 둔 바깥 판단(신호 · 거르기 · 검토 표시)을 가상 시계대로
            from strategy.ext import ReplayDoor
            self.ext = ReplayDoor(ext_replay, self.log)

    # ---------- 바꿔 끼우는 자리 ----------
    def clock_ms(self) -> int:
        return int(self.t_ms)

    def now_dt(self) -> datetime:
        return pd.Timestamp(self.t_ms, unit="ms", tz="UTC").tz_convert(KST).to_pydatetime()

    def _update_bars(self, code: str):
        b = self._all.get(code)
        now = pd.Timestamp(self.t_ms, unit="ms", tz="UTC").tz_convert(KST)
        # 지금 닫힌 봉만: 봉 시각 + 1분 <= 지금(진행 중인 봉 · 미래 봉은 보이지 않는다)
        vis = b[b["time"] + pd.Timedelta(minutes=1) <= now] if b is not None else live.data.empty_bars()
        self.bars[code] = vis.reset_index(drop=True)
        return self.bars[code], self.t_ms, self.t_ms

    def price_now(self, code: str) -> int:
        b = self.bars.get(code)
        if b is None or b.empty:
            raise RuntimeError("아직 닫힌 봉 없음")
        return int(b["close"].iloc[-1])

    def save_bars(self):                                       # 재생은 봉 캐시(data/bars)를 쓰지 않는다
        return None

    # ---------- 돌리기 ----------
    def run_day(self, start: str = "09:00", until: str = "15:20", on_bar=None) -> dict:
        """그날을 봉마다 돌린다. on_bar(runner, 봉 마감 ms) 를 주면 봉 처리 뒤마다 부른다(견본 실행이 판단을 끼워 넣는 자리)."""
        why = self.amount_refusal()
        if why:
            self.log.write("start_refused", reason="amount over cap", detail=why)
            return self.oms.summary()
        d = pd.Timestamp(datetime.strptime(self.day, "%Y%m%d")).tz_localize(KST)
        t = d + pd.Timedelta(hours=int(start[:2]), minutes=int(start[3:]))
        end = d + pd.Timedelta(hours=int(until[:2]), minutes=int(until[3:]))
        self.log.write("start", codes=self.codes, strategy=type(self.strategy).__name__, params=self.strategy.params,
                       mode="bar_replay", broker=self.broker.name, day=self.day, order_mode=self.settings.mode,
                       amount=self.amount, max_orders=self.max_orders, ext=self.ext is not None)
        settle_ms = int(self.settle * 1000)
        t += pd.Timedelta(minutes=self.tf)
        while t < end:
            close_ms = int(t.timestamp() * 1000)
            self.t_ms = close_ms + settle_ms
            if self.risk.stop_requested():
                break
            self.step(bar_close_ms=close_ms)
            if on_bar is not None:
                on_bar(self, close_ms)
            t += pd.Timedelta(minutes=self.tf)
        self.t_ms = int(end.timestamp() * 1000)
        s = self.oms.summary()
        self.log.write("stop", reason="bar replay end", summary=s)
        return s

    def round_trips(self) -> pd.DataFrame:
        """기록의 체결로 한 번 사고팔기 단위 결과(수수료 · 세금 전) — 화면 백테스트 탭이 읽는 round_trips.csv 꼴."""
        rows, hold = [], {}
        for e in self.log.today_events():
            if e.get("event") != "fill":
                continue
            c, q, px = e["code"], int(e["qty"]), float(e["price"])
            h = hold.setdefault(c, {"q": 0, "cost": 0.0, "t": e["t_seen_ms"], "sell_q": 0, "sell_v": 0.0})
            if e["side"] == "buy":
                if h["q"] == 0:
                    h.update(t=e["t_seen_ms"], cost=0.0, sell_q=0, sell_v=0.0)
                h["q"] += q
                h["cost"] += q * px
            else:
                h["sell_q"] += q
                h["sell_v"] += q * px
                if h["sell_q"] >= h["q"] and h["q"] > 0:
                    buy_avg, sell_avg = h["cost"] / h["q"], h["sell_v"] / h["sell_q"]
                    rows.append({"code": c, "t_entry_ms": h["t"], "t_exit_ms": e["t_seen_ms"], "qty": h["q"],
                                 "entry": buy_avg, "exit": sell_avg, "pnl": (sell_avg - buy_avg) * h["q"],
                                 "ret_pct": (sell_avg / buy_avg - 1) * 100})
                    hold[c] = {"q": 0, "cost": 0.0, "t": 0, "sell_q": 0, "sell_v": 0.0}
        return pd.DataFrame(rows, columns=["code", "t_entry_ms", "t_exit_ms", "qty", "entry", "exit", "pnl", "ret_pct"])
