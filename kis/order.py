"""주문 — 안전장치를 먼저 통과해야 나간다. **모의투자 전용**(공개판에는 실전 주문 경로가 없다).

- 모의 설정(config.load("paper"))이 아니면 어떤 주문도 보내지 않는다.
- 1회 최대 금액 MAX_ORDER_WON, 같은 주문 60초 안 재요청 차단, 지정가는 호가 단위 검사.
- 주문 기록은 logs/orders.jsonl (계좌번호·키는 적지 않는다).

함수
  limit_order(cfg, side, code, qty, price)        지정가(ORD_DVSN 00)
  market_order(cfg, side, code, qty, est_price)   시장가(ORD_DVSN 01, 단가 0) — 금액 한도는 est_price(추정가)로 검사
  cancel_order(cfg, code, odno, orgno, qty)       남은 수량 전부 취소(정정취소 RVSE_CNCL_DVSN_CD 02, QTY_ALL_ORD_YN Y)
TR(공식 예제 저장소 open-trading-api 의 국내주식 예제로 확인 2026-11-09): 현금 매수 VTTC0012U · 매도 VTTC0011U · 정정취소 VTTC0013U.
반환 기록의 orgno = KRX_FWDG_ORD_ORGNO(한국거래소전송주문조직번호) — 취소할 때 필요하다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from .client import post
from .config import KisConfig

MAX_ORDER_WON = 3_000_000          # 1회 매수 절대 상한(원) — 설정 max_amount 로 더 낮출 수 있고 높일 수는 없다
DUP_WINDOW_SEC = 60
LOG = Path(__file__).resolve().parents[1] / "logs" / "orders.jsonl"
TRADING = "/uapi/domestic-stock/v1/trading"      # 국내주식 주문 경로 앞부분
ORDER_PATH = TRADING + "/order-cash"
CANCEL_PATH = TRADING + "/order-rvsecncl"


def tick_size(price: int) -> int:
    """KRX 주식 호가 단위 (2023-01-25 개편 기준)"""
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < limit:
            return tick
    return 1_000


def _paper_only(cfg: KisConfig) -> None:
    if cfg is None or not cfg.is_paper:
        raise PermissionError("모의투자 설정이 아닙니다 — 이 프로그램은 모의 주문만 보냅니다")


def check(cfg: KisConfig, side: str, code: str, qty: int, price: int, kind: str = "limit") -> None:
    """안전장치. 통과 못 하면 예외. kind = limit(price = 지정가) / market(price = 금액 검사용 추정가)."""
    _paper_only(cfg)
    if side not in ("buy", "sell"):
        raise ValueError("side 는 buy / sell")
    if qty <= 0 or price <= 0:
        raise ValueError("수량·가격은 0보다 커야 합니다")
    if kind == "limit" and price % tick_size(price):
        raise ValueError(f"{price:,}원은 호가 단위({tick_size(price):,}원)에 맞지 않습니다")
    if side == "buy" and qty * price > MAX_ORDER_WON:
        raise ValueError(f"주문 금액 {qty * price:,}원이 1회 한도 {MAX_ORDER_WON:,}원을 넘습니다")
    key_price = price if kind == "limit" else 0
    if LOG.exists():
        now = time.time()
        for line in LOG.read_text(encoding="utf-8").splitlines()[-50:]:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("kind", "order") != "order":
                continue
            if (o.get("mode"), o.get("side"), o.get("code"), o.get("qty"), o.get("price")) == \
                    (cfg.mode, side, code, qty, key_price) and now - o.get("ts", 0) < DUP_WINDOW_SEC:
                raise RuntimeError(f"같은 주문을 {DUP_WINDOW_SEC}초 안에 다시 넣으려 합니다 (중복 방지)")


def _write(rec: dict) -> None:
    """주문이 이미 나간 뒤라 기록 실패가 주문 결과를 바꾸면 안 된다 — 실패는 삼킨다."""
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _send(cfg: KisConfig, side: str, code: str, qty: int, unit: int, dvsn: str) -> dict:
    tr_id = f"{cfg.tr_prefix}TTC00{'12' if side == 'buy' else '11'}U"
    return post(cfg, ORDER_PATH, tr_id, {
        "CANO": cfg.account,
        "ACNT_PRDT_CD": cfg.product,
        "PDNO": code,
        "ORD_DVSN": dvsn,
        "ORD_QTY": str(qty),
        "ORD_UNPR": str(unit),
        "EXCG_ID_DVSN_CD": "KRX",
        "SLL_TYPE": "",
        "CNDT_PRIC": "",
    })


def _rec(cfg, d, side, code, qty, price, order_type, est_price=None) -> dict:
    out = d.get("output") or {}
    rec = {"ts": time.time(), "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": "order", "mode": cfg.mode,
           "type": order_type, "side": side, "code": code, "qty": qty, "price": price,
           "odno": out.get("ODNO"), "orgno": out.get("KRX_FWDG_ORD_ORGNO"), "ord_tmd": out.get("ORD_TMD"),
           "msg": (d.get("msg1") or "").strip()}
    if est_price is not None:
        rec["est_price"] = est_price
    _write(rec)
    return rec


def limit_order(cfg: KisConfig, side: str, code: str, qty: int, price: int) -> dict:
    """지정가 현금 주문. 반환: 주문번호(odno)·주문조직번호(orgno)·주문시각."""
    check(cfg, side, code, qty, price, "limit")
    d = _send(cfg, side, code, qty, price, "00")
    return _rec(cfg, d, side, code, qty, price, "limit")


def market_order(cfg: KisConfig, side: str, code: str, qty: int, est_price: int) -> dict:
    """시장가 현금 주문(단가 0). 매수 금액 한도는 est_price(추정가 — 부르는 쪽이 여유를 두고 정함)로 검사."""
    check(cfg, side, code, qty, est_price, "market")
    d = _send(cfg, side, code, qty, 0, "01")
    return _rec(cfg, d, side, code, qty, 0, "market", est_price=est_price)


def best_order(cfg: KisConfig, side: str, code: str, qty: int, est_price: int) -> dict:
    """최유리지정가(ORD_DVSN 03, 단가 0) — 접수 순간 상대편 맨 앞 가격으로 정해짐. 금액 한도는 est_price 로 검사.
    한투 모의투자 안내상 허용되는 주문 종류 — 실제 접수 · 주문가 기록 방식은 아직 모의에서 확인하지 못했다."""
    check(cfg, side, code, qty, est_price, "market")
    d = _send(cfg, side, code, qty, 0, "03")
    return _rec(cfg, d, side, code, qty, 0, "best", est_price=est_price)


def replace_order(cfg: KisConfig, code: str, odno: str, orgno: str, qty: int, dvsn: str, price: int) -> dict:
    """정정(RVSE_CNCL_DVSN_CD 01): 남은 수량 전부를 다른 가격·종류로. 정정하면 시간 우선은 정정 접수 시점으로 바뀐다(KRX).
    지금 OMS 는 '취소 확정 → 새 주문'을 쓴다(검증된 길). 이 함수는 모의 점검 뒤 쓸지 정한다(아직 미검증)."""
    _paper_only(cfg)
    if not odno or not orgno:
        raise ValueError("정정에는 원주문번호와 주문조직번호가 필요합니다")
    d = post(cfg, CANCEL_PATH, f"{cfg.tr_prefix}TTC0013U", {
        "CANO": cfg.account, "ACNT_PRDT_CD": cfg.product, "KRX_FWDG_ORD_ORGNO": orgno, "ORGN_ODNO": odno,
        "ORD_DVSN": dvsn, "RVSE_CNCL_DVSN_CD": "01", "ORD_QTY": str(max(int(qty), 0)), "ORD_UNPR": str(int(price)),
        "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX",
    })
    out = d.get("output") or {}
    rec = {"ts": time.time(), "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": "replace", "mode": cfg.mode,
           "code": code, "orig_odno": odno, "qty": qty, "dvsn": dvsn, "price": price, "odno": out.get("ODNO"),
           "orgno": out.get("KRX_FWDG_ORD_ORGNO"), "msg": (d.get("msg1") or "").strip()}
    _write(rec)
    return rec


def cancel_order(cfg: KisConfig, code: str, odno: str, orgno: str, qty: int) -> dict:
    """원주문(odno)의 남은 수량 전부 취소. orgno = 원주문의 KRX_FWDG_ORD_ORGNO. 반환: 취소 주문번호."""
    _paper_only(cfg)
    if not odno or not orgno:
        raise ValueError("취소에는 원주문번호와 주문조직번호가 필요합니다")
    d = post(cfg, CANCEL_PATH, f"{cfg.tr_prefix}TTC0013U", {
        "CANO": cfg.account,
        "ACNT_PRDT_CD": cfg.product,
        "KRX_FWDG_ORD_ORGNO": orgno,
        "ORGN_ODNO": odno,
        "ORD_DVSN": "00",
        "RVSE_CNCL_DVSN_CD": "02",          # 02 = 취소
        "ORD_QTY": str(max(int(qty), 0)),
        "ORD_UNPR": "0",
        "QTY_ALL_ORD_YN": "Y",              # 잔량 전부
        "EXCG_ID_DVSN_CD": "KRX",
    })
    out = d.get("output") or {}
    rec = {"ts": time.time(), "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": "cancel", "mode": cfg.mode,
           "code": code, "orig_odno": odno, "qty": qty, "odno": out.get("ODNO"), "ord_tmd": out.get("ORD_TMD"),
           "msg": (d.get("msg1") or "").strip()}
    _write(rec)
    return rec
