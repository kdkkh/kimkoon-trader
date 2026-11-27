"""계좌 조회."""
from __future__ import annotations

from .client import KisError, get, get_page
from .config import KisConfig

TRADING = "/uapi/domestic-stock/v1/trading"      # 국내주식 주문 · 계좌 경로 앞부분


BALANCE_MAX_PAGES = 20        # 잔고조회 이어 받기 상한(쪽)


def balance(cfg: KisConfig) -> tuple[list[dict], dict]:
    """주식잔고조회 (실전 TTTC8434R / 모의 VTTC8434R). 반환: (보유종목 목록, 계좌 합계)."""
    params = {
        "CANO": cfg.account,
        "ACNT_PRDT_CD": cfg.product,
        "AFHR_FLPR_YN": "N",          # 시간외단일가 반영 안 함
        "OFL_YN": "",
        "INQR_DVSN": "02",            # 종목별
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",            # 전일매매 포함
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    # 보유 종목이 한 쪽을 넘으면 다음 쪽을 이어 받는다 — 첫 쪽만 보면 뒤쪽 종목이 '잔고 0'으로 보여 장부에서 지워진다.
    # 끝까지 못 받으면(쪽 수 상한 · 이어 받기 값 없음) 일부만 돌려주지 않고 오류로 끝낸다(대조하는 쪽이 건너뛰게).
    rows, summary, cont = [], {}, False
    for _ in range(BALANCE_MAX_PAGES):
        d, more = get_page(cfg, TRADING + "/inquire-balance", f"{cfg.tr_prefix}TTC8434R", params, cont=cont)
        rows += d.get("output1") or []
        summary = (d.get("output2") or [{}])[0] or summary
        if not more:
            break
        fk, nk = str(d.get("ctx_area_fk100") or "").strip(), str(d.get("ctx_area_nk100") or "").strip()
        if not nk:
            raise KisError("[잔고조회] 다음 쪽이 있다는데 이어 받기 값이 비어 있음 — 일부만으로는 대조하지 않음")
        params = {**params, "CTX_AREA_FK100": fk, "CTX_AREA_NK100": nk}
        cont = True
    else:
        raise KisError(f"[잔고조회] {BALANCE_MAX_PAGES}쪽을 넘김 — 일부만으로는 대조하지 않음")
    holdings = [h for h in rows if int(h.get("hldg_qty", "0") or 0) > 0]
    return holdings, summary


def daily_orders(cfg: KisConfig, date: str, odno: str = "") -> list[dict]:
    """주식일별주문체결조회 3개월 이내 (실전 TTTC0081R / 모의 VTTC0081R). date = YYYYMMDD."""
    d = get(cfg, TRADING + "/inquire-daily-ccld", f"{cfg.tr_prefix}TTC0081R", {
        "CANO": cfg.account,
        "ACNT_PRDT_CD": cfg.product,
        "INQR_STRT_DT": date,
        "INQR_END_DT": date,
        "SLL_BUY_DVSN_CD": "00",      # 매도·매수 전체
        "PDNO": "",
        "CCLD_DVSN": "00",            # 체결·미체결 전체
        "INQR_DVSN": "00",            # 최근 순
        "INQR_DVSN_3": "00",
        "ORD_GNO_BRNO": "",
        "ODNO": odno,
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    })
    return d.get("output1", [])


# 주문 상태 판정에 쓰는 칸만 남긴다(응답에 있는 연락처·지점 등은 기록하지 않음).
STATUS_FIELDS = ("odno", "orgn_odno", "ord_gno_brno", "ord_orgno", "sll_buy_dvsn_cd", "pdno", "ord_qty", "ord_unpr",
                 "ord_tmd", "tot_ccld_qty", "avg_prvs", "tot_ccld_amt", "rmn_qty", "cncl_yn", "cncl_cfrm_qty",
                 "rjct_qty", "ord_dvsn_cd", "infm_tmd", "excg_dvsn_cd")


def _int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_order_row(r: dict) -> dict:
    """일별주문체결 한 줄 → 상태 판정용 값. remaining 은 rmn_qty, 없으면 주문 − 체결 − 취소확인 − 거부."""
    qty, filled = _int(r.get("ord_qty")), _int(r.get("tot_ccld_qty"))
    cancelled, rejected = _int(r.get("cncl_cfrm_qty")), _int(r.get("rjct_qty"))
    derived = max(qty - filled - cancelled - rejected, 0)
    # rmn_qty 와 '주문 − 체결 − 취소확인 − 거부' 중 작은 값(취소 뒤 rmn_qty 가 늦게 바뀌어도 취소확인으로 잡는다)
    remaining = min(_int(r["rmn_qty"]), derived) if r.get("rmn_qty") not in (None, "") else derived
    return {"odno": str(r.get("odno", "")), "orig_odno": str(r.get("orgn_odno", "") or ""), "code": r.get("pdno", ""),
            "side": "buy" if r.get("sll_buy_dvsn_cd") == "02" else "sell" if r.get("sll_buy_dvsn_cd") == "01" else "",
            "qty": qty, "filled": filled, "avg": _float(r.get("avg_prvs")), "remaining": remaining,
            "cancelled": cancelled, "rejected": rejected, "cancel_row": r.get("cncl_yn") == "Y",
            "ord_tmd": str(r.get("ord_tmd", "") or ""), "orgno": str(r.get("ord_orgno") or r.get("ord_gno_brno") or ""),
            "raw": {k: r.get(k) for k in STATUS_FIELDS if k in r}}


def order_status(cfg: KisConfig, date: str, odno: str) -> dict | None:
    """주문번호 하나의 상태(체결 수량·평균가·남은 수량). 못 찾으면 None. 번호는 앞자리 0을 무시하고 비교."""
    want = str(odno).lstrip("0")
    for r in daily_orders(cfg, date, odno):
        if str(r.get("odno", "")).lstrip("0") == want:
            return parse_order_row(r)
    return None


def today_orders(cfg: KisConfig, date: str) -> list[dict]:
    """그날 주문 최근 순 첫 쪽(모의는 한 번에 15건 — 공식 예제 문서). 응답 없이 끝난 주문을 찾는 데 쓴다."""
    return [parse_order_row(r) for r in daily_orders(cfg, date)]
