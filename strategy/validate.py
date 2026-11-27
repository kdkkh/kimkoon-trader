"""이식 전략 검증기(로직) — scripts/strategy_validate.py 가 부른다. 어떤 AI 가 옮긴 파일이든 같은 기준으로 본다.

단계
  ① 규격: import 가능 · Strategy 상속 · name(str)·params(dict) · signals 출력(열·길이·bool·short False)
  ② 금지: 외부 지표 라이브러리·네트워크·주문·프로세스 실행 import 금지(실패), 허용 목록 밖 import·대응표 밖 ta 함수·
          eval/exec/open 사용(경고)
  ③ 견본 봉 실행: 합성 봉(고정 시드) 또는 실제 봉으로 예외 없이 도는지, 신호 수
  ④ 트레이딩뷰 거래 목록 대조(선택): 거래 수, 진입·청산 시각(같은 봉이면 일치)·가격(상대오차 0.1% 안) 일치율,
          처음 갈라진 거래와 그 앞뒤 봉 값
  ⑤ 판정: 통과 / 경고 / 실패
"""
from __future__ import annotations

import ast
import inspect
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import indicators
from strategy.backtest import run_backtest
from strategy.base import KST, SIGNAL_COLS, Strategy, validate_bars, validate_signals

# 대응표(strategy/PORTING_PROMPT.md)의 우리 함수 — 이 밖의 ta.xxx 는 경고
ALLOWED_TA = {n for n, f in vars(indicators).items() if inspect.isfunction(f) and not n.startswith("_")
              and f.__module__ == indicators.__name__}
ALLOWED_IMPORTS = {"__future__", "pandas", "numpy", "math", "typing", "dataclasses",
                   "strategy", "strategy.base", "strategy.indicators"}
FORBIDDEN_IMPORTS = {  # 지표 라이브러리 · 네트워크 · 주문 · 프로세스
    "pandas_ta", "talib", "ta", "finta", "tulipy", "btalib", "pandas_ta_classic", "stockstats", "vectorbt", "backtrader",
    "requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "socket", "websockets", "websocket", "ssl",
    "kis", "subprocess", "multiprocessing", "ctypes", "smtplib", "ftplib", "telegram", "yfinance", "FinanceDataReader",
    "pykrx", "ccxt",
}
SUSPICIOUS_CALLS = {"eval", "exec", "open", "__import__", "compile", "input"}
PRICE_TOL = 0.001          # 상대오차 0.1%

TV_COL_CANDIDATES = {
    "trade": ["trade #", "trade number", "trade", "거래 #", "거래번호", "거래 번호"],
    "type": ["type", "유형", "타입", "종류"],
    "time": ["date/time", "date and time", "date", "time", "날짜/시간", "날짜/시각", "시각", "날짜", "시간"],
    "price": ["price", "가격"],
    "qty": ["position size (qty)", "contracts", "quantity", "qty", "수량", "포지션 크기 (수량)", "포지션 크기"],
}


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def ok(self, msg): self.lines.append(f"  [통과] {msg}")
    def warn(self, msg): self.warns.append(msg); self.lines.append(f"  [경고] {msg}")
    def fail(self, msg): self.fails.append(msg); self.lines.append(f"  [실패] {msg}")
    def info(self, msg): self.lines.append(f"         {msg}")
    def head(self, msg): self.lines.append(msg)

    @property
    def verdict(self) -> str:
        return "실패" if self.fails else "경고" if self.warns else "통과"


# ---------- 견본 봉 ----------
def sample_bars(n: int = 400, seed: int = 7, start: str = "2024-01-02") -> pd.DataFrame:
    """합성 일봉(고정 시드 랜덤워크 + 추세 바뀜) — 네트워크 없이 같은 결과. 가격은 원 단위 정수."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-0.004, 0.004], size=n // 40 + 1), 40)[:n]
    ret = drift + rng.normal(0, 0.018, n)
    close = np.round(50_000 * np.exp(np.cumsum(ret)))
    open_ = np.round(np.r_[50_000, close[:-1]] * (1 + rng.normal(0, 0.004, n)))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n)))
    times = pd.bdate_range(start, periods=n, tz=KST)
    return pd.DataFrame({"time": times, "open": open_, "high": np.round(high), "low": np.round(low),
                         "close": close, "volume": rng.integers(100_000, 1_000_000, n).astype(float)})


# ---------- ② 정적 검사 ----------
def static_check(path: Path, rep: Report):
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        rep.fail(f"문법 오류: {e}")
        return
    ta_alias = set()
    bad, odd = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in FORBIDDEN_IMPORTS:
                    bad.append(a.name)
                elif a.name not in ALLOWED_IMPORTS and top not in ("pandas", "numpy"):
                    odd.append(a.name)
                if a.name == "strategy.indicators":
                    ta_alias.add(a.asname or "strategy.indicators")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            if top in FORBIDDEN_IMPORTS:
                bad.append(mod)
            elif mod not in ALLOWED_IMPORTS and top not in ("pandas", "numpy"):
                odd.append(mod)
            for a in node.names:
                if mod == "strategy" and a.name == "indicators":
                    ta_alias.add(a.asname or "indicators")
                if mod == "strategy.indicators" and a.name != "*" and a.name not in ALLOWED_TA:
                    odd.append(f"strategy.indicators.{a.name}(대응표에 없음)")
    if bad:
        rep.fail(f"금지된 import: {', '.join(sorted(set(bad)))} (외부 지표 라이브러리·네트워크·주문 코드는 쓰지 않는다)")
    else:
        rep.ok("금지된 import 없음(지표 라이브러리·네트워크·주문)")
    if odd:
        rep.warn(f"허용 목록 밖 import: {', '.join(sorted(set(odd)))}")
    unknown_ta, sus = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ta_alias:
            if node.attr not in ALLOWED_TA:
                unknown_ta.add(node.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SUSPICIOUS_CALLS:
            sus.add(node.func.id)
    if unknown_ta:
        rep.warn(f"대응표 밖 지표 함수: {', '.join(sorted(unknown_ta))} → 파일 끝 '미지원' 표에 적고 직접 구현했는지 확인")
    else:
        rep.ok(f"지표 함수는 모두 대응표 안({', '.join(sorted(ALLOWED_TA))})" if ta_alias else "indicators 를 쓰지 않음")
    if sus:
        rep.warn(f"의심스러운 호출: {', '.join(sorted(sus))}")
    if "자기 점검 표" not in src:
        rep.warn("파일 끝 '자기 점검 표' 주석이 없음(PORTING_PROMPT 출력 규격)")
    doc = ast.get_docstring(tree) or ""
    for need in ("Pine 원본", "이식 날짜", "바꾼 점"):
        if need not in doc:
            rep.warn(f"머리말에 '{need}' 항목이 없음")


# ---------- ①·③ ----------
def load_class(target: str, rep: Report):
    from strategy.ported import strategy_class
    try:
        cls = strategy_class(target)
    except Exception as e:
        rep.fail(f"불러오기 실패: {type(e).__name__}: {e}")
        return None
    rep.ok(f"import 됨 → {cls.__name__} (Strategy 상속)")
    if not isinstance(getattr(cls, "name", None), str) or cls.name == "base":
        rep.fail("name 이 없거나 'base' 그대로")
    if not isinstance(cls.params, dict):
        rep.fail("params 가 dict 가 아님")
    else:
        rep.ok(f"params = {cls.params}")
    if cls.signals is Strategy.signals:
        rep.fail("signals() 를 구현하지 않음")
    return cls


def run_on(cls, bars: pd.DataFrame, params: dict, rep: Report, label: str):
    try:
        strat = cls(**params)
        b = validate_bars(bars)
        sig = strat.signals(b)
        validate_signals(sig, b)
    except Exception as e:
        rep.fail(f"{label} 실행 실패: {type(e).__name__}: {e}")
        rep.info(traceback.format_exc(limit=3).strip().splitlines()[-1])
        return None, None
    ne, nx = int(sig["long_entry"].sum()), int(sig["long_exit"].sum())
    rep.ok(f"{label} {len(b)}봉 실행 — 신호 열·길이·bool·short=False 확인, 진입 {ne}번 · 청산 {nx}번")
    if ne == 0:
        rep.warn(f"{label}: 진입 신호가 한 번도 없음(조건이 너무 좁거나 잘못 옮겼을 수 있음)")
    if (sig["long_entry"] & sig["long_exit"]).any():
        rep.warn(f"{label}: 같은 봉에 진입·청산이 함께 있는 봉 {(sig['long_entry'] & sig['long_exit']).sum()}개")
    return b, sig


# ---------- ④ 트레이딩뷰 거래 목록 ----------
def parse_tv_cols(spec: str | None) -> dict:
    out = {}
    for part in (spec or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def read_tv_trades(path: str | Path, cols: dict | None = None) -> pd.DataFrame:
    """트레이딩뷰 거래 목록 CSV → 롱 거래 1건 1줄(trade, entry_time, entry_price, exit_time, exit_price, qty, open).
    열 이름은 'Trade #, Type, Date/Time, Signal, Price <통화>, Position size (qty) …'(도움말 화면 열 이름 기준,
    내보내기 파일의 정확한 머리글은 미확인) 을 앞부분 일치로 찾는다. 다르면 cols={"trade":…, "type":…, "time":…, "price":…}."""
    raw = pd.read_csv(path, encoding="utf-8-sig")
    lower = {c.lower().strip(): c for c in raw.columns}
    m = {}
    for key, cands in TV_COL_CANDIDATES.items():
        if cols and key in cols:
            if cols[key] not in raw.columns:
                raise KeyError(f"--tv-cols 의 {key}={cols[key]} 열이 CSV 에 없음 (있는 열: {list(raw.columns)})")
            m[key] = cols[key]
            continue
        for cand in cands:
            hit = [orig for low, orig in lower.items() if low == cand] or \
                  [orig for low, orig in lower.items() if low.startswith(cand)]
            if hit:
                m[key] = hit[0]
                break
    need = [k for k in ("trade", "type", "time", "price") if k not in m]
    if need:
        raise KeyError(f"트레이딩뷰 CSV 에서 열을 못 찾음: {need} (있는 열: {list(raw.columns)}) → --tv-cols 로 지정")
    t = raw.rename(columns={v: k for k, v in m.items()})
    typ = t["type"].astype(str).str.lower()
    is_long = typ.str.contains("long") | typ.str.contains("롱") | typ.str.contains("매수")
    is_entry = typ.str.contains("entry") | typ.str.contains("진입")
    is_exit = typ.str.contains("exit") | typ.str.contains("청산")
    t = t[is_long]
    times = pd.to_datetime(t["time"], errors="coerce")
    if times.dt.tz is None:
        times = times.dt.tz_localize(KST)
    t = t.assign(time=times, price=pd.to_numeric(t["price"].astype(str).str.replace(",", ""), errors="coerce"))
    rows = []
    for tid, g in t.groupby("trade", sort=True):
        e = g[is_entry.loc[g.index]]
        x = g[is_exit.loc[g.index]]
        if e.empty:
            continue
        xt = x["time"].iloc[0] if not x.empty else pd.NaT
        rows.append({"trade": tid, "entry_time": e["time"].iloc[0], "entry_price": float(e["price"].iloc[0]),
                     "exit_time": xt, "exit_price": float(x["price"].iloc[0]) if not x.empty else np.nan,
                     "qty": e["qty"].iloc[0] if "qty" in e else np.nan, "open": pd.isna(xt)})
    return pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)


def _bar_key(ts: pd.Timestamp, daily: bool, tf_minutes: int):
    if pd.isna(ts):
        return None
    if daily:
        return ts.strftime("%Y-%m-%d")
    return ts.floor(f"{tf_minutes}min")


def _price_match(ours: float, tv: float) -> bool:
    """가격 일치 판정. ±0.1% 안이거나, 우리 가격을 트레이딩뷰 호가 단위로 반올림한 값이 같으면 일치.
    실측(한 종목 일봉): 트레이딩뷰 거래 목록의 체결가는 전부 500원 단위였고, 한투 시가(100원 단위)를
    500원 단위로 반올림하면 10건 모두 정확히 같았다 → 트레이딩뷰가 그 종목의 틱을 500원으로 잡았다."""
    if abs(ours - tv) <= PRICE_TOL * tv:
        return True
    for tick in (500.0, 1000.0, 100.0, 50.0, 10.0, 5.0, 1.0):
        if tv % tick == 0 and round(ours / tick) * tick == tv:
            return True
    return False


def compare_trades(ours: pd.DataFrame, tv: pd.DataFrame, bars: pd.DataFrame, sig: pd.DataFrame, rep: Report,
                   daily: bool, tf_minutes: int = 1, warmup_bars: int = 0):
    """우리 거래(backtest trades) vs 트레이딩뷰 롱 거래. 겹치는 기간(우리 봉 시작 + 예열 봉 이후)만 대조."""
    o = ours.copy()
    o["entry_time"] = pd.to_datetime(o["진입시각"]).dt.tz_localize(KST) if pd.to_datetime(o["진입시각"]).dt.tz is None \
        else pd.to_datetime(o["진입시각"])
    xt = pd.to_datetime(o["청산시각"].replace("", np.nan))
    o["exit_time"] = xt.dt.tz_localize(KST) if xt.dt.tz is None else xt
    o["open"] = o["상태"] != "닫힘"
    start = bars["time"].iloc[min(warmup_bars, len(bars) - 1)]
    end = bars["time"].iloc[-1]
    tvw = tv[(tv["entry_time"] >= start) & (tv["entry_time"] <= end)].reset_index(drop=True)
    ow = o[o["entry_time"] >= start].reset_index(drop=True)
    rep.info(f"대조 기간 {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} (예열 {warmup_bars}봉 뒤) — "
             f"트레이딩뷰 롱 거래 {len(tvw)}건(전체 {len(tv)}) · 우리 {len(ow)}건")
    if len(tvw) == 0:
        rep.warn("겹치는 기간에 트레이딩뷰 거래가 없음 — 봉 기간(--days)이나 차트 시간대(Asia/Seoul)를 확인")
        return
    # 11-03: 순번이 아니라 **진입 봉(날짜)** 으로 짝짓는다. 창 시작의 예열 차이로 한쪽에만 있는 거래가 생기면
    # 순번 대조는 전부 밀려 0% 가 나오므로(실측), 같은 진입 봉끼리 맞추고 짝이 없는 거래는 따로 센다.
    tv_by_key = {_bar_key(b["entry_time"], daily, tf_minutes): b for _, b in tvw.iterrows()}
    our_keys = set()
    t_ok = p_ok = xt_ok = xp_ok = 0
    m = 0
    first_bad = None
    unmatched_ours, unmatched_tv = [], []
    for i in range(len(ow)):
        a = ow.iloc[i]
        k = _bar_key(a["entry_time"], daily, tf_minutes)
        our_keys.add(k)
        b = tv_by_key.get(k)
        if b is None:
            unmatched_ours.append(a)
            if first_bad is None:
                first_bad = (i, "진입 시각(트레이딩뷰에 같은 봉 진입 없음)")
            continue
        t_ok += 1
        pe = _price_match(a["진입가"], b["entry_price"])
        both_closed = not a["open"] and not b["open"]
        tx = (_bar_key(a["exit_time"], daily, tf_minutes) == _bar_key(b["exit_time"], daily, tf_minutes)) if both_closed else True
        px = _price_match(a["청산가"], b["exit_price"]) if both_closed else True
        p_ok += pe; xt_ok += tx; xp_ok += px; m += 1
        if first_bad is None and not (pe and tx and px):
            why = [w for w, okv in (("진입가", pe), ("청산 시각", tx), ("청산가", px)) if not okv]
            first_bad = (i, ", ".join(why))
    for k, b in tv_by_key.items():
        if k not in our_keys:
            unmatched_tv.append(b)
    # 분모에 '트레이딩뷰에만 있는 거래'도 넣는다. 빼면 신호를 빠뜨린 이식본이 100% 일치로 통과한다.
    n = max(len(ow) + len(unmatched_tv), 1)
    if first_bad is None and unmatched_tv:
        first_bad = (len(ow), f"트레이딩뷰에만 있는 진입 {len(unmatched_tv)}건")
    if unmatched_ours or unmatched_tv:
        rep.info(f"짝 없는 거래 — 우리만 {len(unmatched_ours)}건"
                 + (f"({', '.join(format(a['entry_time'], '%Y-%m-%d') for a in unmatched_ours[:5])})" if unmatched_ours else "")
                 + f" · 트레이딩뷰만 {len(unmatched_tv)}건"
                 + (f"({', '.join(format(b['entry_time'], '%Y-%m-%d') for b in unmatched_tv[:5])})" if unmatched_tv else "")
                 + " — 창 시작 직후 한 건이면 예열 차이(정상), 중간에 있으면 신호 차이")
    rate = t_ok / n * 100
    rep.data.update({"tv_trades": len(tvw), "our_trades": len(ow), "entry_time_match_pct": round(rate, 1),
                     "entry_price_match_pct": round(p_ok / n * 100, 1)})
    rep.info(f"거래 수 {len(ow)} vs {len(tvw)} · 진입 시각 일치 {t_ok}/{n} ({rate:.1f}%) · 진입가 일치(±0.1% 또는 호가 단위 반올림) {p_ok}/{n} "
             f"({p_ok / n * 100:.1f}%) · 청산 시각 {xt_ok}/{m} · 청산가 {xp_ok}/{m}")
    if first_bad is None:
        rep.ok("트레이딩뷰 거래 목록과 모두 일치")
        return
    i, why = first_bad
    msg = f"처음 갈라진 거래: {i + 1}번째 ({why})"
    if rate < 80:          # 진입 시각이 80% 미만 일치 → 잘못 옮겼을 가능성이 큼
        rep.fail(msg)
    else:                  # 80% 이상 → 데이터·초기값 차이일 수 있음, 아래 봉 값으로 원인 확인
        rep.warn(msg)
    a = ow.iloc[i] if i < len(ow) else None
    # 짝은 진입 봉으로 지었으니 보여 줄 트레이딩뷰 거래도 순번이 아니라 같은 진입 봉에서 고른다.
    b = (tv_by_key.get(_bar_key(a["entry_time"], daily, tf_minutes)) if a is not None
         else (unmatched_tv[0] if unmatched_tv else None))
    if a is not None:
        rep.info(f"  우리      진입 {a['entry_time']:%Y-%m-%d %H:%M} {a['진입가']:,.2f} → 청산 "
                 f"{'' if pd.isna(a['exit_time']) else format(a['exit_time'], '%Y-%m-%d %H:%M')} {a['청산가']:,.2f}")
    if b is not None:
        rep.info(f"  트레이딩뷰 진입 {b['entry_time']:%Y-%m-%d %H:%M} {b['entry_price']:,.2f} → 청산 "
                 f"{'' if pd.isna(b['exit_time']) else format(b['exit_time'], '%Y-%m-%d %H:%M')} {b['exit_price']:,.2f}")
    ref = (a if a is not None else b)["entry_time"]
    j = int(bars["time"].searchsorted(ref))
    lo, hi = max(j - 4, 0), min(j + 3, len(bars))
    view = pd.concat([bars.iloc[lo:hi].reset_index(drop=True),
                      sig.iloc[lo:hi].reset_index(drop=True).drop(columns=["short_entry", "short_exit"])], axis=1)
    view["time"] = view["time"].dt.strftime("%Y-%m-%d %H:%M")
    rep.info("  그 앞뒤 봉(신호는 봉 마감, 체결은 다음 봉 시가):")
    for line in view.to_string(index=False, float_format=lambda v: f"{v:,.2f}").splitlines():
        rep.info("    " + line)


def validate(target: str, bars: pd.DataFrame | None = None, params: dict | None = None,
             tv_trades: str | None = None, tv_cols: dict | None = None, amount: float = 1_000_000,
             tf_minutes: int = 1, warmup_bars: int | None = None) -> Report:
    rep = Report()
    p = Path(target)
    path = p if p.suffix == ".py" else None
    rep.head("① 규격 검사")
    cls = load_class(target, rep)
    if cls is not None and path is None:
        path = Path(inspect.getfile(cls))
    rep.head("② 금지 검사")
    if path is not None and path.exists():
        static_check(path, rep)
    else:
        rep.fail("전략 파일을 찾을 수 없음")
    rep.head("③ 견본 봉 실행")
    if cls is None:
        rep.fail("불러오지 못해 실행 생략")
        return rep
    params = params or {}
    sb, _ = run_on(cls, sample_bars(), params, rep, "합성 봉")
    real = sig = None
    if bars is not None and not bars.empty:
        real, sig = run_on(cls, bars, params, rep, "실제 봉")
    if tv_trades:
        rep.head("④ 트레이딩뷰 거래 목록 대조")
        use_bars, use_sig = (real, sig) if real is not None else (sb, None)
        if use_sig is None and use_bars is not None:
            use_sig = cls(**params).signals(use_bars)
        if use_bars is None:
            rep.fail("봉이 없어 대조 생략")
        else:
            try:
                tv = read_tv_trades(tv_trades, tv_cols)
                rep.ok(f"트레이딩뷰 CSV 읽음 — 롱 거래 {len(tv)}건")
                r = run_backtest(use_bars, use_sig, amount=amount)
                daily = (use_bars["time"].diff().dropna() >= pd.Timedelta(hours=20)).mean() > 0.5
                wb = warmup_bars if warmup_bars is not None else 0
                compare_trades(r.trades, tv, use_bars, use_sig, rep, bool(daily), tf_minutes, wb)
            except Exception as e:
                rep.fail(f"대조 실패: {type(e).__name__}: {e}")
    return rep
