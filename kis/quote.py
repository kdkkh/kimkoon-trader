"""시세 조회."""
from __future__ import annotations

from .client import get
from .config import KisConfig

QUOTATIONS = "/uapi/domestic-stock/v1/quotations"      # 국내주식 시세 경로 앞부분


def current_price(cfg: KisConfig, code: str) -> dict:
    """주식현재가 시세 (FHKST01010100) — 실전·모의 같은 tr_id."""
    d = get(cfg, QUOTATIONS + "/inquire-price", "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
    return d["output"]


def volume_rank(cfg: KisConfig, n: int = 10) -> list[dict]:
    """거래량순위 (FHPST01710000) — 공식 문서상 모의투자 미지원 → 실전 키로 조회만."""
    d = get(cfg, QUOTATIONS + "/volume-rank", "FHPST01710000", {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",            # 전체
        "FID_DIV_CLS_CODE": "0",             # 전체(보통주·우선주)
        "FID_BLNG_CLS_CODE": "0",            # 평균거래량 순
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    })
    return d["output"][:n]


def minute_bars_past(cfg: KisConfig, code: str, date: str, hour: str = "153000") -> list[dict]:
    """주식일별분봉조회 (FHKST03010230) — date(YYYYMMDD) 의 hour(HHMMSS) 이전 1분봉 최대 120개.
    공식 전체문서·TR 목록에는 '모의투자 미지원'으로 표시돼 있다(모의 키로 될 때도 있었으나 기본은 실전 키 조회)."""
    d = get(cfg, QUOTATIONS + "/inquire-time-dailychartprice", "FHKST03010230", {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": hour,
        "FID_INPUT_DATE_1": date,
        "FID_PW_DATA_INCU_YN": "Y",      # 과거 데이터 포함
        "FID_FAKE_TICK_INCU_YN": "",
    })
    return [b for b in d.get("output2", []) if b.get("stck_cntg_hour")]
