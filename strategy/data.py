"""봉 로더 — 한투 REST 로 일봉·당일 분봉·과거 분봉을 받아 DataFrame(time, open, high, low, close, volume)으로.

쓰는 TR (필드명은 공식 예제 저장소 open-trading-api 의 국내주식 예제에서 확인, 경로 앞부분 = QUOTATIONS):
  - 일봉   FHKST03010100 국내주식기간별시세(일/주/월/년) …/inquire-daily-itemchartprice
           요청 FID_COND_MRKT_DIV_CODE·FID_INPUT_ISCD·FID_INPUT_DATE_1(시작)·FID_INPUT_DATE_2(끝)·FID_PERIOD_DIV_CODE(D)·
           FID_ORG_ADJ_PRC(0 수정주가 / 1 원주가). 한 번에 최대 100건 → 끝 날짜를 앞으로 옮기며 이어 받는다.
           응답 output2: stck_bsop_date, stck_oprc, stck_hgpr, stck_lwpr, stck_clpr, acml_vol (실전·모의 같은 TR)
  - 당일 분봉 FHKST03010200 주식당일분봉조회 …/inquire-time-itemchartprice
           한 번 30건, FID_INPUT_HOUR_1 시각부터 거꾸로. output2: stck_bsop_date, stck_cntg_hour, stck_oprc, stck_hgpr,
           stck_lwpr, stck_prpr(그 분의 종가), cntg_vol. 첫 줄은 진행 중인 분일 수 있다(공식 예제 설명).
  - 과거 분봉 FHKST03010230 주식일별분봉조회 — kis/quote.py 의 minute_bars_past 를 그대로 쓴다(한 번 120건, 거래소 J).
           공식 TR 목록상 '모의투자 미지원'(모의 키로 될 때도 있었음) → 기본은 실전 키 조회.
조회 규칙: 시세 조회는 실전 키(조회만, 주문 없음)가 기본, mode 인자로 바꿀 수 있다. 토큰은 kis.auth.get_token 캐시
  (kis.client.get 안에서) — 새로 발급하는 코드는 없다. 호출 사이 최소 0.35초(앞 응답 뒤 기준), EGW00201 이면 1초 쉬고 한 번 더.
캐시: data/bars/{code}_{tf}.csv (tf = 1d, 1m). 받은 봉을 이어 붙여 저장, 같은 시각은 새로 받은 값으로 덮는다.
봉 시각: 일봉은 그 날 00:00 KST, 분봉은 그 분이 시작한 시각(한투 "14:25" = 14:25:00~14:25:59). tz = Asia/Seoul.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from kis import client, config, quote
from strategy.base import BAR_COLS, KST

ROOT = Path(__file__).resolve().parents[1]
BARS_DIR = ROOT / "data" / "bars"
QUOTATIONS = "/uapi/domestic-stock/v1/quotations"      # 국내주식 시세 경로 앞부분
DAILY = (QUOTATIONS + "/inquire-daily-itemchartprice", "FHKST03010100")
MINUTE_TODAY = (QUOTATIONS + "/inquire-time-itemchartprice", "FHKST03010200")
MIN_GAP = 0.3


class Pacer:
    """REST 호출을 한 줄로 세운다. 앞 응답을 받은 뒤 gap 초가 지나야 다음 요청."""

    def __init__(self, gap: float = 0.35):
        self.gap = max(gap, MIN_GAP)
        self.last = 0.0
        self.calls = 0

    def call(self, fn, *args, **kwargs):
        for attempt in range(2):
            wait = self.gap - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            try:
                return fn(*args, **kwargs)
            except client.KisError as e:
                if "EGW00201" not in str(e) or attempt == 1:   # 초당 한도만 한 번 더
                    raise
                time.sleep(1.0)
            finally:
                self.last = time.time()
                self.calls += 1


PACER = Pacer()
_CFG: dict[str, config.KisConfig] = {}


def cfg_of(mode: str) -> config.KisConfig:
    if mode not in _CFG:
        _CFG[mode] = config.load(mode)
    return _CFG[mode]


# ---------- 변환 ----------
def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return empty_bars()
    df = pd.DataFrame(rows, columns=BAR_COLS)
    df = df.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    return df


def daily_rows_to_df(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d = (r.get("stck_bsop_date") or "").strip()
        if not d or not r.get("stck_clpr"):
            continue
        out.append({"time": pd.Timestamp(datetime.strptime(d, "%Y%m%d")).tz_localize(KST),
                    "open": _num(r.get("stck_oprc")), "high": _num(r.get("stck_hgpr")),
                    "low": _num(r.get("stck_lwpr")), "close": _num(r.get("stck_clpr")),
                    "volume": _num(r.get("acml_vol"))})
    return _frame(out)


def minute_rows_to_df(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d, h = (r.get("stck_bsop_date") or "").strip(), (r.get("stck_cntg_hour") or "").strip()
        if not d or not h or not r.get("stck_prpr"):
            continue
        out.append({"time": pd.Timestamp(datetime.strptime(d + h[:4], "%Y%m%d%H%M")).tz_localize(KST),
                    "open": _num(r.get("stck_oprc")), "high": _num(r.get("stck_hgpr")),
                    "low": _num(r.get("stck_lwpr")), "close": _num(r.get("stck_prpr")),
                    "volume": _num(r.get("cntg_vol"))})
    return _frame(out)


# ---------- 캐시 ----------
def empty_bars() -> pd.DataFrame:
    """열 형식이 맞는 빈 봉 표(time 은 KST 시각형 — .dt 를 써도 오류가 나지 않게)."""
    df = pd.DataFrame({c: pd.Series(dtype=float) for c in BAR_COLS})
    df["time"] = pd.Series(dtype=f"datetime64[ns, {KST}]")
    return df[BAR_COLS]


def cache_path(code: str, tf: str) -> Path:
    return BARS_DIR / f"{code}_{tf}.csv"


def read_bars_csv(path: str | Path) -> pd.DataFrame:
    """봉 CSV(열 time, open, high, low, close, volume)를 읽는다. 네트워크 호출 없음.
    time 에 시간대가 없으면 KST 로 본다. 캐시 파일(data/bars)과 같은 형식이다."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"봉 CSV 가 없습니다: {p.name}")
    df = pd.read_csv(p, encoding="utf-8")
    missing = [c for c in BAR_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"봉 CSV 에 열이 없습니다: {', '.join(missing)} (필요: {', '.join(BAR_COLS)})")
    df["time"] = pd.to_datetime(df["time"])
    if df["time"].dt.tz is None:
        df["time"] = df["time"].dt.tz_localize(KST)
    else:
        df["time"] = df["time"].dt.tz_convert(KST)
    return df[BAR_COLS].sort_values("time", kind="stable").reset_index(drop=True)


def read_cache(code: str, tf: str) -> pd.DataFrame:
    p = cache_path(code, tf)
    if not p.exists():
        return empty_bars()
    return read_bars_csv(p)


def merge_save(code: str, tf: str, new: pd.DataFrame) -> pd.DataFrame:
    old = read_cache(code, tf)
    parts = [x for x in (old, new) if not x.empty]
    df = _frame(pd.concat(parts).to_dict("records")) if parts else empty_bars()
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["time"] = out["time"].map(lambda t: t.isoformat())
    out.to_csv(cache_path(code, tf), index=False, encoding="utf-8")
    return df


# ---------- 일봉 ----------
def fetch_daily(code: str, start: str, end: str, mode: str = "real", market: str = "J",
                adjusted: bool = True, verbose: bool = True) -> pd.DataFrame:
    """[start, end](YYYYMMDD) 일봉. 100건씩, 받은 것 중 가장 이른 날 하루 전을 다음 끝 날짜로."""
    cfg = cfg_of(mode)
    rows: list[dict] = []
    cur_end = end
    while cur_end >= start:
        d = PACER.call(client.get, cfg, DAILY[0], DAILY[1], {
            "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": cur_end,
            "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0" if adjusted else "1"})
        page = [r for r in d.get("output2") or [] if (r.get("stck_bsop_date") or "").strip()]
        if verbose:
            span = f"{min(r['stck_bsop_date'] for r in page)}~{max(r['stck_bsop_date'] for r in page)}" if page else "없음"
            print(f"  [일봉] {code} ~{cur_end} 요청 → {len(page)}건 ({span})")
        if not page:
            break
        rows += page
        earliest = min(r["stck_bsop_date"] for r in page)
        if earliest <= start or len(page) < 100:
            break
        cur_end = (datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    df = daily_rows_to_df(rows)
    lo, hi = pd.Timestamp(start).tz_localize(KST), pd.Timestamp(end).tz_localize(KST)
    return df[(df["time"] >= lo) & (df["time"] <= hi)].reset_index(drop=True)


def load_daily(code: str, days: int = 365, mode: str = "real", refresh: bool = False,
               fetch: bool = True, verbose: bool = True) -> pd.DataFrame:
    """최근 days(달력 일) 일봉. 캐시에 있는 앞부분은 다시 받지 않고, 캐시 마지막 날(미완성일 수 있음)부터 오늘까지만 받는다."""
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    cached = empty_bars() if refresh else read_cache(code, "1d")
    new_parts = []
    if fetch:
        if cached.empty:
            new_parts.append(fetch_daily(code, start, today, mode, verbose=verbose))
        else:
            first, last = cached["time"].min().strftime("%Y%m%d"), cached["time"].max().strftime("%Y%m%d")
            if start < first:
                prev = (datetime.strptime(first, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
                new_parts.append(fetch_daily(code, start, prev, mode, verbose=verbose))
            new_parts.append(fetch_daily(code, last, today, mode, verbose=verbose))
    new = pd.concat([p for p in new_parts if not p.empty]) if any(not p.empty for p in new_parts) \
        else empty_bars()
    df = merge_save(code, "1d", new) if not new.empty else read_cache(code, "1d")
    lo = pd.Timestamp(start).tz_localize(KST)
    return df[df["time"] >= lo].reset_index(drop=True)


def drop_unfinished_daily(df: pd.DataFrame, now: datetime | None = None, close_hhmm: str = "2005") -> tuple[pd.DataFrame, bool]:
    """오늘 일봉이 아직 안 끝났으면 뺀다. 2026-09-14 부터 거래소 애프터마켓이 20:00 까지라
    일봉에 애프터마켓이 들어가는지 확인 전까지는 20:05 전이면 미완성으로 본다(보수적)."""
    now = now or datetime.now()
    if df.empty:
        return df, False
    last = df["time"].iloc[-1]
    if last.strftime("%Y%m%d") == now.strftime("%Y%m%d") and now.strftime("%H%M") < close_hhmm:
        return df.iloc[:-1].reset_index(drop=True), True
    return df, False


# ---------- 분봉 ----------
def fetch_minute_today(code: str, bars: int = 120, until: str | None = None, mode: str = "real",
                       market: str = "J", verbose: bool = False) -> pd.DataFrame:
    """오늘 1분봉을 until(HHMMSS, 기본 지금)부터 거꾸로 bars 개 이상(30개씩). 진행 중인 분도 들어 있을 수 있다."""
    cfg = cfg_of(mode)
    hour = until or datetime.now().strftime("%H%M%S")
    rows: list[dict] = []
    seen_earliest = None
    while True:
        d = PACER.call(client.get, cfg, MINUTE_TODAY[0], MINUTE_TODAY[1], {
            "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": hour, "FID_PW_DATA_INCU_YN": "N"})
        page = [r for r in d.get("output2") or [] if r.get("stck_cntg_hour") and _num(r.get("stck_prpr")) > 0]
        if verbose:
            print(f"  [당일 분봉] {code} ~{hour} → {len(page)}건")
        if not page:
            break
        rows += page
        earliest = min(r["stck_cntg_hour"] for r in page)
        if earliest == seen_earliest or len({r["stck_cntg_hour"] for r in rows}) >= bars or earliest <= "090000":
            break
        seen_earliest = earliest
        prev = datetime.strptime(earliest, "%H%M%S") - timedelta(minutes=1)
        hour = prev.strftime("%H%M") + "59"
    return minute_rows_to_df(rows)


def fetch_minute_past(code: str, date: str, hour: str = "153000", mode: str = "real",
                      verbose: bool = True) -> pd.DataFrame:
    """date(YYYYMMDD) 의 hour 이전 1분봉 최대 120개 — kis.quote.minute_bars_past(FHKST03010230, 거래소 J) 한 번."""
    rows = PACER.call(quote.minute_bars_past, cfg_of(mode), code, date, hour)
    df = minute_rows_to_df(rows)
    df = df[df["time"].dt.strftime("%Y%m%d") == date].reset_index(drop=True)   # 앞 날짜 봉이 섞여 오면 뺀다
    if verbose:
        span = f"{df['time'].iloc[0]:%H:%M}~{df['time'].iloc[-1]:%H:%M}" if len(df) else "없음"
        print(f"  [과거 분봉] {code} {date} ~{hour} → {len(df)}봉 ({span})")
    return df


def drop_unfinished_minute(df: pd.DataFrame, now: datetime | None = None, tf_minutes: int = 1) -> pd.DataFrame:
    """지금 진행 중인 봉(시작 시각 + tf 분이 아직 안 지난 봉)을 뺀다."""
    if df.empty:
        return df
    now_ts = pd.Timestamp(now or datetime.now())
    now_ts = now_ts.tz_localize(KST) if now_ts.tzinfo is None else now_ts.tz_convert(KST)
    return df[df["time"] + pd.Timedelta(minutes=tf_minutes) <= now_ts].reset_index(drop=True)


def resample_minutes(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """1분봉 → N분봉(봉 시작 시각 기준, 날짜 경계 넘지 않음). 09:00 KST = 00:00 UTC 라 5·10·15·30·60분 경계가 09:00 에 맞는다."""
    if minutes <= 1 or df.empty:
        return df.reset_index(drop=True)
    g = df.set_index("time").resample(f"{minutes}min", label="left", closed="left")
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum()}).dropna(subset=["open"])
    return out.reset_index()[BAR_COLS]


def parse_tf(tf: str) -> tuple[str, int]:
    """'1d' → ('d', 1), '1m'·'5m'·'15m' → ('m', 분)."""
    tf = tf.lower().strip()
    if tf in ("1d", "d", "day"):
        return "d", 1
    if tf.endswith("m") and tf[:-1].isdigit():
        return "m", int(tf[:-1])
    raise ValueError("tf 는 1d 또는 1m·3m·5m·15m·30m·60m 처럼")
